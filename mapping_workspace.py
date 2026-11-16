"""Reviewed mapping inputs and atomic job publication on the shared DHE worker."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import RLock
from uuid import uuid4
from math import isfinite

import geopandas as gpd

from mapping_data import (DEFAULT_MAPPING_SOURCE,DEFAULT_MAPPING_TARGET,prepare_layer,reproject_layer,
                          overlap_review,resolve_overlap,suggest_groups,validate_groups,route_group)
from mapping_builders import build_mapping
from mapping_scene import MappingScene
from web_generalization_data import UploadStore


class MappingWorkspace:
    def __init__(self,paths=None):
        self.paths=paths or dict(source=DEFAULT_MAPPING_SOURCE,target=DEFAULT_MAPPING_TARGET)
        self.layers={};self.groups=[];self.suggestions={};self.revision=0;self.result=None;self.preview=None
        self.settings=dict(mode='hybrid',source_z=5.,target_z=15.,geometry_tolerance=.01,
                           overlap_fraction=.25,search_distance=10.,source_label='',target_label='',source_scale=None,target_scale=None,source_time='',target_time='')
        self.uploads=UploadStore(allow_symbols=True);self.lock=RLock()

    def check(self,revision):
        if revision!=self.revision:raise ValueError('Mapping changed in another tab. Refresh its inputs and review again.')

    def state(self):
        with self.lock:
            layers={}
            for side,layer in self.layers.items():
                geographic=gpd.GeoSeries([f.geometry for f in layer.features.values()],crs=layer.crs).to_crs(4326)
                features=[]
                for f,g in zip(layer.features.values(),geographic):
                    features.append(dict(type='Feature',geometry=g.__geo_interface__,properties=dict(uid=f.uid,original_id=f.original_id,kind=f.geometry.geom_type,side=side,members=list(f.members),attributes=f.attributes)))
                layers[side]=dict(name=layer.source,crs=layer.crs.to_string(),count=len(features),notes=list(layer.notes),overlaps=overlap_review(layer),features=dict(type='FeatureCollection',features=features))
            groups=deepcopy(self.groups)
            if len(self.layers)==2:
                for g in groups:g['builder'],g['reason']=route_group(g,self.layers['source'],self.layers['target'],self.settings['mode'])
            return dict(revision=self.revision,layers=layers,groups=groups,settings=self.settings.copy(),suggestions=deepcopy(self.suggestions),
                        has_result=self.result is not None,has_preview=self.preview is not None,
                        preview_id=self.preview.preview_id if self.preview else None,
                        default_paths={k:str(v) for k,v in self.paths.items()})

    def execute(self,action,revision,*,progress=lambda *a:None,cancelled=lambda:False,**p):
        self.check(revision)
        if action=='load':
            side=p.pop('side')
            if side not in ('source','target'):raise ValueError('Choose source or target.')
            progress(f'Reading {side} layer and preserving feature identities')
            if p.get('upload_id'):
                frame,name=self.uploads.read_frame(p['upload_id'],p['layer'],p.get('crs'),p.get('override_crs'),p.get('accept_declared_crs',False))
            else:
                path=self.paths[side]
                if not Path(path).is_file():raise ValueError(f'Default {side} dataset unavailable. Upload a layer instead: {path}')
                frame=gpd.read_file(path);name=Path(path).name
            layer=prepare_layer(frame,side,name,crs=p.get('crs'),override_crs=p.get('override_crs'))
            other=self.layers.get('target' if side=='source' else 'source')
            if other is not None:layer=reproject_layer(layer,other.crs)
            if cancelled():raise ValueError('Mapping load cancelled; previous inputs preserved.')
            with self.lock:self.layers={**self.layers,side:layer};self.groups=[];self.suggestions={};self.preview=None;self.revision+=1
        elif action=='overlap':
            side=p['side']
            if side not in self.layers:raise ValueError('Load this layer first.')
            layer=resolve_overlap(self.layers[side],p['ids'],p['resolution'],p.get('keeper'))
            with self.lock:self.layers={**self.layers,side:layer};self.groups=[];self.preview=None;self.revision+=1
        elif action in ('analyze','review'):
            if len(self.layers)!=2:raise ValueError('Load both source and target layers first.')
            settings={**self.settings,**p.get('settings',{})}
            allowed=set(self.settings)
            if set(settings)-allowed:raise ValueError('Unknown mapping settings.')
            if settings['mode'] not in ('classic','smooth','hybrid'):raise ValueError('Choose Classic, Smooth, or Hybrid.')
            for key in ('source_z','target_z','geometry_tolerance','overlap_fraction','search_distance'):
                if not isinstance(settings[key],(int,float)) or isinstance(settings[key],bool) or not isfinite(settings[key]):raise ValueError(f'{key} must be finite.')
            if not 0<settings['source_z']<settings['target_z'] or settings['geometry_tolerance']<=0 or not 0<settings['overlap_fraction']<=1 or settings['search_distance']<0:
                raise ValueError('Require increasing positive Z levels, positive fit tolerance, overlap in (0,1], and nonnegative search distance.')
            for key in ('source_scale','target_scale'):
                if settings[key] is not None and (not isinstance(settings[key],(int,float)) or isinstance(settings[key],bool) or not isfinite(settings[key]) or settings[key]<=0):raise ValueError('Scale denominators must be positive.')
            for key in ('source_label','target_label','source_time','target_time'):
                if not isinstance(settings[key],str):raise ValueError('Layer labels and times must be text.')
            if action=='analyze':
                progress('Suggesting overlap and proximity correspondences')
                groups,suggestions=suggest_groups(self.layers['source'],self.layers['target'],overlap_fraction=settings['overlap_fraction'],search_distance=settings['search_distance'])
            else:
                groups=deepcopy(p['groups']);suggestions=self.suggestions
                validate_groups(groups,self.layers['source'],self.layers['target'],require_confirmed=False)
            if cancelled():raise ValueError('Mapping review cancelled; previous inputs preserved.')
            with self.lock:self.groups=groups;self.suggestions=suggestions;self.settings=settings;self.preview=None;self.revision+=1
        elif action=='build':
            if len(self.layers)!=2:raise ValueError('Load both mapping layers first.')
            progress('Planning the reviewed mapping')
            result=build_mapping(self.layers['source'],self.layers['target'],self.groups,self.settings,progress=progress,cancelled=cancelled)
            preview=bool(p.get('preview',False));next_revision=self.revision if preview else self.revision+1
            progress('Checking endpoint geometry and preparing geographic DHE snapshot')
            snapshot=MappingScene(result,next_revision,uuid4().hex if preview else None)
            snapshot.validate_endpoints(self.layers['source'],self.layers['target'],self.groups)
            if cancelled():raise ValueError('Mapping cancelled; previously published model preserved.')
            with self.lock:
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
