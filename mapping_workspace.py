"""Reviewed mapping inputs and atomic job publication on the shared DHE worker."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import RLock
from uuid import uuid4
from math import isfinite

import geopandas as gpd

from mapping_data import (DEFAULT_MAPPING_SOURCE,DEFAULT_MAPPING_TARGET,prepare_layer,reproject_layer,
                          overlap_review,resolve_overlap,suggest_groups,route_group,union_overlaps,
                          automatic_review,review_summary,member_records,id_groups,validate_id_groups,matching_id,change_layer_side)
from mapping_builders import build_mapping
from mapping_scene import MappingScene
from web_measurements import layer_comparison
from web_generalization_data import UploadStore,fetch_mapping_osm


class MappingWorkspace:
    def __init__(self,paths=None):
        self.paths=paths or dict(source=DEFAULT_MAPPING_SOURCE,target=DEFAULT_MAPPING_TARGET)
        self.layers={};self.groups=[];self.suggestions={};self.revision=0;self.result=None;self.preview=None
        self.input_layers={}
        self.settings=dict(mode='hybrid',match_by_id=False,source_z=5.,target_z=15.,geometry_tolerance=.01,
                           overlap_fraction=.25,search_distance=10.,source_label='',target_label='',source_scale=None,target_scale=None,source_time='',target_time='')
        self.uploads=UploadStore(allow_symbols=True);self.lock=RLock()

    def check(self,revision):
        if revision!=self.revision:raise ValueError('Mapping changed in another tab. Refresh its inputs and review again.')

    def _prepare(self,layers,settings,*,groups=None,suggestions=None,regenerate=False,progress=lambda *a:None,cancelled=lambda:False):
        only_ids=settings['match_by_id']
        progress('Automatically unioning same-ID overlaps' if only_ids else 'Automatically unioning overlaps within each mapping layer')
        prepared={k:union_overlaps(l,match_by_id=only_ids,cancelled=cancelled) for k,l in layers.items()}
        changed=any(prepared[k] is not layers[k] for k in layers)
        if len(prepared)!=2:return prepared,[],{}
        if regenerate or changed or not groups:
            progress('Matching all polygons by supplied ID' if only_ids else 'Automatically matching features and classifying appearances/disappearances')
            if only_ids:groups,suggestions=id_groups(prepared['source'],prepared['target'])
            else:
                groups,suggestions=suggest_groups(prepared['source'],prepared['target'],
                    overlap_fraction=settings['overlap_fraction'],search_distance=settings['search_distance'])
        groups=automatic_review(groups,prepared['source'],prepared['target'])
        if only_ids:validate_id_groups(groups,prepared['source'],prepared['target'])
        # The client can send rows previously decorated by state(). Those are
        # snapshot diagnostics, not persistent instructions for a later build.
        for g in groups:
            for key in ('builder','reason','initial_builder','initial_reason','fallback'):g.pop(key,None)
        if cancelled():raise ValueError('Mapping preparation cancelled; previous inputs preserved.')
        return prepared,groups,suggestions or {}

    def state(self):
        with self.lock:
            layers={}
            for side,layer in self.layers.items():
                geographic=gpd.GeoSeries([f.geometry for f in layer.features.values()],crs=layer.crs).to_crs(4326)
                features=[]
                for f,g in zip(layer.features.values(),geographic):
                    features.append(dict(type='Feature',geometry=g.__geo_interface__,properties=dict(uid=f.uid,original_id=f.original_id,kind=f.geometry.geom_type,side=side,members=list(f.members),attributes=f.attributes,member_records=list(member_records(f)))))
                layers[side]=dict(name=layer.source,crs=layer.crs.to_string(),crs_label=layer.crs.name,count=len(features),notes=list(layer.notes),overlaps=overlap_review(layer),features=dict(type='FeatureCollection',features=features))
            groups=deepcopy(self.groups)
            if len(self.layers)==2:
                for g in groups:g['builder'],g['reason']=route_group(g,self.layers['source'],self.layers['target'],self.settings['mode'])
            snapshot=self.preview or self.result
            if snapshot is not None and snapshot.revision==self.revision:
                actual={r['group']:r for r in snapshot.result.report['routing']}
                for g in groups:
                    if g['id'] in actual:
                        r=actual[g['id']]
                        for key in ('builder','reason','initial_builder','fallback'):
                            if key in r:g[key]=deepcopy(r[key])
            return dict(revision=self.revision,layers=layers,groups=groups,settings=self.settings.copy(),suggestions=deepcopy(self.suggestions),
                        automatic_review=review_summary(self.layers,self.groups),
                        measurements=layer_comparison(self.layers),
                        has_result=self.result is not None,has_preview=self.preview is not None,
                        preview_id=self.preview.preview_id if self.preview else None,
                        can_swap=len(self.layers)==2 and all(f.geometry.geom_type in ('Polygon','MultiPolygon') for f in self.layers['target'].features.values()),
                        default_paths={k:Path(v).name for k,v in self.paths.items()})

    def execute(self,action,revision,*,progress=lambda *a:None,cancelled=lambda:False,**p):
        self.check(revision)
        if action in ('load','osm'):
            side=p.pop('side')
            if side not in ('source','target'):raise ValueError('Choose source or target.')
            progress(f'Reading {side} layer and preserving feature identities')
            if action=='osm':
                progress('Downloading OSM building footprints')
                frame,notes=fetch_mapping_osm(p['bounds'])
                frame.attrs['web_import_notes']=[dict(action='OSM import',message=n) for n in notes]
                name='OpenStreetMap buildings; © OpenStreetMap contributors, ODbL'
            elif p.get('upload_id'):
                frame,name=self.uploads.read_frame(p['upload_id'],p['layer'],p.get('crs'),p.get('override_crs'),p.get('accept_declared_crs',False))
            else:
                path=self.paths[side]
                if not Path(path).is_file():raise ValueError(f'Default {side} dataset unavailable. Upload a layer instead: {path}')
                frame=gpd.read_file(path);name=Path(path).name
            layer=prepare_layer(frame,side,name,crs=p.get('crs'),override_crs=p.get('override_crs'))
            other=self.layers.get('target' if side=='source' else 'source')
            if other is not None:layer=reproject_layer(layer,other.crs)
            inputs={**self.layers,**self.input_layers,side:layer}
            layers,groups,suggestions=self._prepare(inputs,self.settings,regenerate=True,progress=progress,cancelled=cancelled)
            if cancelled():raise ValueError('Mapping load cancelled; previous inputs preserved.')
            with self.lock:self.input_layers=inputs;self.layers=layers;self.groups=groups;self.suggestions=suggestions;self.preview=None;self.revision+=1
        elif action=='swap':
            if len(self.layers)!=2:raise ValueError('Load both layers before swapping.')
            if any(f.geometry.geom_type not in ('Polygon','MultiPolygon') for f in self.layers['target'].features.values()):
                raise ValueError('Swap requires polygon targets; lines and points cannot become polygon sources.')
            inputs={side:change_layer_side(self.input_layers.get(other,self.layers[other]),side)
                    for side,other in (('source','target'),('target','source'))}
            settings=self.settings.copy()
            for suffix in ('label','scale','time'):
                a,b='source_'+suffix,'target_'+suffix
                settings[a],settings[b]=settings[b],settings[a]
            layers,groups,suggestions=self._prepare(inputs,settings,regenerate=True,progress=progress,cancelled=cancelled)
            with self.lock:
                self.input_layers=inputs;self.layers=layers;self.groups=groups;self.suggestions=suggestions
                self.settings=settings;self.result=None;self.preview=None;self.revision+=1
        elif action=='overlap':
            side=p['side']
            if side not in self.layers:raise ValueError('Load this layer first.')
            if self.settings['match_by_id'] and p['resolution']=='union':
                features=self.layers[side].features
                values={matching_id(features[k]) for k in p['ids'] if k in features}
                if len(values)!=1 or None in values:raise ValueError('ID-only matching cannot union polygons with different or missing supplied IDs; assign the overlap or exclude a polygon.')
            layer=resolve_overlap(self.layers[side],p['ids'],p['resolution'],p.get('keeper'))
            inputs={**self.layers,**self.input_layers,side:layer}
            layers,groups,suggestions=self._prepare(inputs,self.settings,regenerate=True,progress=progress,cancelled=cancelled)
            if cancelled():raise ValueError('Mapping preparation cancelled; previous inputs preserved.')
            with self.lock:self.input_layers=inputs;self.layers=layers;self.groups=groups;self.suggestions=suggestions;self.preview=None;self.revision+=1
        elif action in ('analyze','review'):
            if action=='review' and len(self.layers)!=2:raise ValueError('Load both source and target layers first.')
            settings={**self.settings,**p.get('settings',{})}
            allowed=set(self.settings)
            if set(settings)-allowed:raise ValueError('Unknown mapping settings.')
            if settings['mode'] not in ('classic','smooth','hybrid'):raise ValueError('Choose Classic, Smooth, or Hybrid.')
            if not isinstance(settings['match_by_id'],bool):raise ValueError('match_by_id must be true or false.')
            for key in ('source_z','target_z','geometry_tolerance','overlap_fraction','search_distance'):
                if not isinstance(settings[key],(int,float)) or isinstance(settings[key],bool) or not isfinite(settings[key]):raise ValueError(f'{key} must be finite.')
            if not 0<settings['source_z']<settings['target_z'] or settings['geometry_tolerance']<=0 or not 0<settings['overlap_fraction']<=1 or settings['search_distance']<0:
                raise ValueError('Require increasing positive Z levels, positive fit tolerance, overlap in (0,1], and nonnegative search distance.')
            for key in ('source_scale','target_scale'):
                if settings[key] is not None and (not isinstance(settings[key],(int,float)) or isinstance(settings[key],bool) or not isfinite(settings[key]) or settings[key]<=0):raise ValueError('Scale denominators must be positive.')
            for key in ('source_label','target_label','source_time','target_time'):
                if not isinstance(settings[key],str):raise ValueError('Layer labels and times must be text.')
            matching_changed=settings['match_by_id']!=self.settings['match_by_id']
            regenerate=action=='analyze' or matching_changed or any(settings[k]!=self.settings[k] for k in ('overlap_fraction','search_distance'))
            inputs={**self.layers,**self.input_layers} if matching_changed else self.layers
            layers,groups,suggestions=self._prepare(inputs,settings,groups=p.get('groups',self.groups),suggestions=self.suggestions,
                                                   regenerate=regenerate,progress=progress,cancelled=cancelled)
            if cancelled():raise ValueError('Mapping review cancelled; previous inputs preserved.')
            with self.lock:self.layers=layers;self.groups=groups;self.suggestions=suggestions;self.settings=settings;self.preview=None;self.revision+=1
        elif action=='build':
            if len(self.layers)!=2:raise ValueError('Load both mapping layers first.')
            layers,groups,suggestions=self._prepare(self.layers,self.settings,groups=self.groups,suggestions=self.suggestions,progress=progress,cancelled=cancelled)
            progress('Planning the reviewed mapping')
            result=build_mapping(layers['source'],layers['target'],groups,self.settings,progress=progress,cancelled=cancelled)
            changed=any(layers[k] is not self.layers[k] for k in layers) or groups!=self.groups
            preview=bool(p.get('preview',False));next_revision=self.revision+int(not preview or changed)
            progress('Checking endpoint geometry and preparing geographic DHE snapshot')
            snapshot=MappingScene(result,next_revision,uuid4().hex if preview else None)
            snapshot.payload['measurements']=layer_comparison(layers)
            snapshot.validate_endpoints(layers['source'],layers['target'],groups)
            if cancelled():raise ValueError('Mapping cancelled; previously published model preserved.')
            with self.lock:
                self.layers=layers;self.groups=groups;self.suggestions=suggestions;self.revision=next_revision
                if preview:self.preview=snapshot
                else:self.result=snapshot;self.preview=None;self.revision=next_revision
        elif action=='discard-preview':
            with self.lock:self.preview=None
        else:raise ValueError('Unknown Mapping action.')
        return dict(revision=self.revision,preview_id=self.preview.preview_id if self.preview else None)

    def scene(self,preview_id=None):
        with self.lock:
            snapshot=self.preview if preview_id else self.result
            if snapshot is None:raise ValueError('Build a Mapping model first.')
            if preview_id and snapshot.preview_id!=preview_id:raise ValueError('Mapping preview expired.')
            return snapshot

    def close(self):self.uploads.close()
