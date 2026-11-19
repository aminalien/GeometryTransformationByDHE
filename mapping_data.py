"""Identity-preserving two-layer import, correspondence review and routing."""
from dataclasses import dataclass,replace
from collections import defaultdict
from copy import deepcopy
from math import isfinite

import geopandas as gpd
import numpy as np
from shapely import force_2d,make_valid,STRtree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from shell_conformity import arithmetic_bound
from web_generalization_data import json_value

DEFAULT_MAPPING_SOURCE='C:/University/PhD/Data/Kleczkow Updated/osm_data.shp'
DEFAULT_MAPPING_TARGET='C:/University/PhD/Data/Kleczkow Updated/citygml_data.shp'


def polygons(g):
    if g.is_empty:return
    if g.geom_type=='Polygon':yield g
    elif hasattr(g,'geoms'):
        for child in g.geoms:yield from polygons(child)


@dataclass(frozen=True)
class MappingFeature:
    uid:str
    original_id:str
    geometry:object
    attributes:dict
    members:tuple[str,...]
    member_records:tuple=()


@dataclass(frozen=True)
class MappingLayer:
    side:str
    source:str
    crs:object
    features:dict
    notes:tuple


def prepare_layer(frame,side,source,*,crs=None,override_crs=None):
    if side not in ('source','target'):raise ValueError('Choose source or target.')
    notes=list(frame.attrs.get('web_import_notes',()))
    if override_crs:
        if not notes:notes.append(dict(action='CRS interpretation override',declared=str(frame.crs),assigned=str(override_crs),explicit_review=True))
        frame=frame.set_crs(override_crs,allow_override=True)
    if frame.crs is None:
        if not crs:raise ValueError('Supply the input CRS before mapping.')
        frame=frame.set_crs(crs)
    if not frame.crs.is_projected or any(abs(a.unit_conversion_factor-1)>1e-10 for a in frame.crs.axis_info[:2]):
        frame=frame.to_crs(frame.estimate_utm_crs())
    result={}
    for i,(_,row) in enumerate(frame.iterrows(),1):
        uid=('s' if side=='source' else 't')+str(i)
        attrs={str(k):json_value(v) for k,v in row.items() if k!=frame.geometry.name}
        ident=next((str(attrs[k]) for k in ('osm_id','gml_id','id','fid','objectid') if k in attrs and attrs[k] is not None),str(i))
        g=row.geometry
        if g is None or g.is_empty:raise ValueError(f'{uid}: empty geometry; remove or repair it before import.')
        g=force_2d(g)
        if g.geom_type in ('Polygon','MultiPolygon'):
            original=g
            # CityGML exports can contain coincident component boundaries.
            # Dissolve within this identity only, never across different rows.
            g=unary_union([p for part in polygons(g) for p in polygons(make_valid(part))])
            if not original.is_valid or original.geom_type!=g.geom_type:
                notes.append(dict(feature=uid,original_id=ident,repair='Per-feature polygon repair/component dissolve',
                                  input_area=float(original.area),output_area=float(g.area)))
        elif side=='source' or g.geom_type not in ('Point','LineString'):
            raise ValueError(f'{uid}: use polygon sources and polygon, simple-line or point targets.')
        if g.is_empty or not g.is_valid or not np.isfinite(g.bounds).all():raise ValueError(f'{uid}: footprint repair did not produce valid geometry.')
        if g.geom_type=='LineString' and (not g.is_simple or g.is_ring or g.length==0):raise ValueError(f'{uid}: target line must be simple, open and nonzero.')
        result[uid]=MappingFeature(uid,ident,g,attrs,(uid,),
                                   (dict(uid=uid,original_id=ident,attributes=deepcopy(attrs)),))
    if not result:raise ValueError('The mapping layer is empty.')
    return MappingLayer(side,str(source),frame.crs,result,tuple(notes))


def reproject_layer(layer,crs):
    if layer.crs==crs:return layer
    keys=list(layer.features)
    geometries=gpd.GeoSeries([layer.features[k].geometry for k in keys],crs=layer.crs).to_crs(crs)
    return replace(layer,crs=crs,features={k:replace(layer.features[k],geometry=g) for k,g in zip(keys,geometries)})


def overlap_review(layer):
    fs=list(layer.features.values());tree=STRtree([f.geometry for f in fs]);result=[]
    for i,f in enumerate(fs):
        if f.geometry.geom_type not in ('Polygon','MultiPolygon'):continue
        for j in tree.query(f.geometry,predicate='intersects'):
            if j>=i or fs[j].geometry.geom_type not in ('Polygon','MultiPolygon'):continue
            g=f.geometry.intersection(fs[j].geometry)
            if g.area>arithmetic_bound([*f.geometry.bounds,*fs[j].geometry.bounds])**2:
                result.append(dict(ids=[fs[j].uid,f.uid],area=float(g.area)))
    return result


def resolve_overlap(layer,ids,action,keeper=None):
    ids=list(dict.fromkeys(ids))
    if not ids or any(k not in layer.features for k in ids):raise ValueError('Choose existing layer feature IDs.')
    fs=dict(layer.features);notes=list(layer.notes)
    if action=='exclude':
        for k in ids:del fs[k]
    else:
        if keeper not in ids:raise ValueError('Choose the feature retaining ownership.')
        if action=='union':
            g=unary_union([fs[k].geometry for k in ids]);members=tuple(k for i in ids for k in fs[i].members)
            records=tuple(record for k in ids for record in member_records(fs[k]))
            fs[keeper]=replace(fs[keeper],geometry=g,members=members,member_records=records)
            for k in ids:
                if k!=keeper:del fs[k]
        elif action=='assign':
            owned=fs[keeper].geometry
            for k in ids:
                if k==keeper:continue
                g=fs[k].geometry.difference(owned)
                if g.is_empty:del fs[k]
                else:fs[k]=replace(fs[k],geometry=g)
        else:raise ValueError('Choose assign, union, or exclude.')
    if not fs:raise ValueError('An input layer must retain at least one feature.')
    notes.append(dict(action=action,feature_ids=ids,keeper=keeper,explicit_review=True))
    return replace(layer,features=fs,notes=tuple(notes))


def member_records(feature):
    return deepcopy(feature.member_records or (dict(uid=feature.uid,original_id=feature.original_id,attributes=feature.attributes),))


def matching_id(feature):
    """A supplied original ID shared by all members; row-number fallbacks never match."""
    values={next((str(r['attributes'][k]) for k in ('osm_id','gml_id','id','fid','objectid')
                  if k in r['attributes'] and r['attributes'][k] is not None),None)
            for r in member_records(feature)}
    if len(values)!=1:return None
    value=next(iter(values))
    return value if value is not None and value.strip() else None


def union_overlaps(layer,*,match_by_id=False,cancelled=lambda:False):
    """Union whole positive-area overlap components, never touching neighbours."""
    graph=defaultdict(set)
    identities={k:matching_id(f) for k,f in layer.features.items()} if match_by_id else {}
    for pair in overlap_review(layer):
        a,b=pair['ids']
        if match_by_id and (identities[a] is None or identities[a]!=identities[b]):continue
        graph[a].add(b);graph[b].add(a)
    if not graph:return layer
    order={k:i for i,k in enumerate(layer.features)};seen=set();result=layer
    for uid in layer.features:
        if uid not in graph or uid in seen:continue
        if cancelled():raise ValueError('Mapping preparation cancelled; previous inputs preserved.')
        pending=[uid];component=set()
        while pending:
            k=pending.pop()
            if k not in component:component.add(k);pending.extend(graph[k]-component)
        seen.update(component);ids=sorted(component,key=order.get)
        keeper=min(ids,key=lambda k:(-layer.features[k].geometry.area,order[k]))
        result=resolve_overlap(result,ids,'union',keeper)
        note={**result.notes[-1], 'explicit_review':False,'automatic':True,
              'policy':('Same supplied ID; largest area, then input order' if match_by_id else 'Largest area, then input order'),
              'member_records':list(member_records(result.features[keeper]))}
        result=replace(result,notes=(*result.notes[:-1],note))
    return result


def automatic_review(groups,source,target):
    """Accept valid groups, resolving unmatched rows without a confirmation gate."""
    groups=deepcopy(groups)
    validate_groups(groups,source,target,require_confirmed=False)
    for g in groups:
        if g['decision']=='unresolved':
            g['decision']='map' if g['sources'] and g['targets'] else 'disappearance' if g['sources'] else 'appearance'
        g['confirmed']=True
    validate_groups(groups,source,target)
    return groups


def review_summary(layers,groups):
    return dict(automatic=True,groups=len(groups),
                decisions={k:sum(g['decision']==k for g in groups) for k in ('map','appearance','disappearance','exclude')},
                union_groups=sum(isinstance(n,dict) and n.get('automatic',False) and n.get('action')=='union' for l in layers.values() for n in l.notes),
                holed_features=sum(any(p.interiors for p in polygons(f.geometry)) for l in layers.values() for f in l.features.values()))


def suggest_groups(source,target,*,overlap_fraction=.25,search_distance=10.):
    if not isfinite(overlap_fraction) or not 0<overlap_fraction<=1:raise ValueError('Overlap fraction must be in (0,1].')
    if not isfinite(search_distance) or search_distance<0:raise ValueError('Search distance must be nonnegative.')
    sf=list(source.features.values());tf=list(target.features.values());tree=STRtree([f.geometry for f in tf])
    graph=defaultdict(set);edges=[];near=[]
    for s in sf:
        for j in tree.query(s.geometry,predicate='dwithin',distance=search_distance):
            t=tf[j];area=s.geometry.intersection(t.geometry).area
            ratio=area/min(s.geometry.area,t.geometry.area) if min(s.geometry.area,t.geometry.area)>0 else 0.
            symbol=t.geometry.geom_type in ('Point','LineString') and s.geometry.covers(t.geometry)
            edge=dict(source=s.uid,target=t.uid,overlap_area=float(area),overlap_fraction=float(ratio),distance=float(s.geometry.distance(t.geometry)))
            if ratio>=overlap_fraction or symbol:
                graph[s.uid].add(t.uid);graph[t.uid].add(s.uid);edges.append(edge)
            else:near.append(edge)
    groups=[];seen=set()
    for k in [*source.features,*target.features]:
        if k in seen:continue
        pending=[k];keys=set()
        while pending:
            n=pending.pop()
            if n not in keys:keys.add(n);pending.extend(graph[n]-keys)
        seen.update(keys);ss=sorted(keys&source.features.keys());ts=sorted(keys&target.features.keys())
        groups.append(dict(id=f'g{len(groups)+1}',sources=ss,targets=ts,
                           decision='map' if ss and ts else 'unresolved',confirmed=False,anchor=None))
    return groups,dict(edges=edges,proximity_candidates=sorted(near,key=lambda e:e['distance']))


def id_groups(source,target):
    """Batch all same-ID polygons; unmatched and symbol rows stay one-sided."""
    buckets={}
    for side,layer in (('sources',source),('targets',target)):
        for uid,f in layer.features.items():
            value=matching_id(f) if f.geometry.geom_type in ('Polygon','MultiPolygon') else None
            key=value if value is not None else (side,uid)
            bucket=buckets.setdefault(key,dict(sources=[],targets=[]))
            bucket[side].append(uid)
    groups=[dict(id=f'g{i}',**b,decision='map' if b['sources'] and b['targets'] else 'unresolved',confirmed=False,anchor=None)
            for i,b in enumerate(buckets.values(),1)]
    return groups,dict(edges=[],proximity_candidates=[])


def validate_id_groups(groups,source,target):
    """Enforce complete ID buckets even for direct API/build callers."""
    validate_groups(groups,source,target)
    expected,_=id_groups(source,target)
    expected=automatic_review(expected,source,target)
    signature=lambda g:(frozenset(g['sources']),frozenset(g['targets']),g['decision'])
    if {signature(g) for g in groups}!={signature(g) for g in expected}:
        raise ValueError('ID-only matching requires one automatic group per supplied polygon ID; manual regrouping and cross-ID mappings are disabled.')


def validate_groups(groups,source,target,*,require_confirmed=True):
    if not isinstance(groups,list):raise ValueError('Correspondences must be a list of groups.')
    seen=set();ids=set()
    for g in groups:
        if not isinstance(g,dict):raise ValueError('Each correspondence must be a group object.')
        if not isinstance(g.get('id'),str) or not g['id'].strip() or g['id'] in ids:raise ValueError('Mapping group IDs must be nonempty and unique.')
        ids.add(g['id']);ss=g.get('sources',[]);ts=g.get('targets',[])
        if any(not isinstance(keys,list) or any(not isinstance(k,str) for k in keys) for keys in (ss,ts)):
            raise ValueError('Source and target IDs must be lists of feature IDs.')
        anchor=g.get('anchor')
        if anchor is not None and (not isinstance(anchor,int) or isinstance(anchor,bool) or anchor<0):raise ValueError('The optional vertex anchor must be a nonnegative integer.')
        if not isinstance(g.get('confirmed',False),bool):raise ValueError('Group confirmation must be true or false.')
        if not ss and not ts:raise ValueError('A group must contain at least one feature.')
        if any(k not in source.features for k in ss) or any(k not in target.features for k in ts):raise ValueError(f"{g['id']}: unknown feature ID.")
        keys=[*ss,*ts]
        if len(set(keys))!=len(keys) or seen&set(keys):raise ValueError('Each feature must belong to exactly one mapping group.')
        seen.update(keys)
        decision=g.get('decision')
        valid=(not require_confirmed and decision=='unresolved') or decision=='exclude' or decision=='map' and ss and ts or decision=='appearance' and not ss and ts or decision=='disappearance' and ss and not ts
        if not valid:raise ValueError(f"{g['id']}: explicitly map, exclude, appear, or disappear.")
        if require_confirmed and not g.get('confirmed'):raise ValueError(f"{g['id']}: confirm its correspondence before construction.")
    if seen!=set(source.features)|set(target.features):raise ValueError('Every feature must be assigned, explicitly unmatched, or excluded.')


def route_group(group,source,target,mode):
    ss=group['sources'];ts=group['targets'];decision=group['decision']
    if mode not in ('classic','smooth','hybrid'):raise ValueError('Choose Classic, Smooth, or Hybrid.')
    if decision=='exclude':return 'excluded','Explicitly excluded'
    if mode=='classic':return 'classic','Classic selected'
    holed=[k for k in ss if any(p.interiors for p in polygons(source.features[k].geometry))]
    holed.extend(k for k in ts if any(p.interiors for p in polygons(target.features[k].geometry)))
    if holed:
        if mode=='smooth' and decision=='map' and len(ts)==1 and target.features[ts[0]].geometry.geom_type=='Polygon':
            many=sum(len(list(polygons(source.features[k].geometry))) for k in ss)>1
            return 'smooth',('Skeleton aggregation and hole-preserving hull morph' if many else 'Continuous hole-preserving polygon mapping')
        return ('unsupported','Smooth-only cannot preserve polygon holes; choose Classic or Hybrid for '+', '.join(holed)) if mode=='smooth' else ('classic','Hole-preserving automatic decomposition: '+', '.join(holed))
    if decision in ('appearance','disappearance'):
        return 'smooth','Centroid-based '+decision
    if len(ss)==1 and len(ts)==1:return 'smooth','One-to-one boundary change, displacement/orientation or collapse'
    if len(ts)>1:
        if mode=='smooth':return 'unsupported','Splits and many-to-many groups require Classic'
        return 'classic','Split' if len(ss)==1 else 'Many-to-many correspondence'
    if len(ss)>1 and len(ts)==1:
        if target.features[ts[0]].geometry.geom_type not in ('Polygon','MultiPolygon'):
            return 'unsupported','Many-to-one smooth aggregation requires a polygon target'
        adjacent=unary_union([source.features[k].geometry for k in ss]).geom_type=='Polygon'
        if adjacent and mode=='hybrid':return 'classic','Adjacent-source merging, including displacement plus merging'
        if mode=='smooth':return 'smooth','Skeleton aggregation with medial-axis recovery, then hull to supplied target'
        return 'smooth','Two-stage aggregation to the supplied target'
    return 'unsupported','Unsupported correspondence'
