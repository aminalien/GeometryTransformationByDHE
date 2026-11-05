"""Deterministic scale-aware selection; atomic publication of a private LTS run.

These are configurable planning heuristics, not calibrated cartographic scores.
All DHE execution remains in the shared level/session engine.
"""
from dataclasses import dataclass,asdict
from copy import copy
from collections import Counter
from itertools import combinations
from math import isfinite,atan2,pi

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from generalization_geometry import TOL,polygon_target,visible_point,skeleton_ridge
from generalization_model import _core,_restore
from lts_evaluation import DisplacementReference


@dataclass(frozen=True)
class AutomaticSettings:
    scale:float=10000.
    scale_multiplier:float=2.
    dz:float=10.
    final_kind:str='automatic'
    class_field:str=''
    min_width_mm:float=.3
    min_area_mm2:float=.09
    min_length_mm:float=1.
    gap_mm:float=.5
    tolerance_mm:float=.1
    max_levels:int=20
    max_scale_advances:int=20
    max_attempts:int=8
    geometry_weight:float=.3
    topology_weight:float=.1
    cartography_weight:float=.6

    def validate(self):
        for key,value in asdict(self).items():
            default=getattr(type(self)(),key)
            if isinstance(default,str):
                if not isinstance(value,str):raise ValueError(f'Automatic setting {key} must be text.')
                continue
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value):raise ValueError(f'Automatic setting {key} must be a finite number.')
            if value<0 or (value==0 and not key.endswith('_weight')):raise ValueError(f'Automatic setting {key} must be positive (weights may be zero).')
        if self.geometry_weight+self.topology_weight+self.cartography_weight<=0:raise ValueError('At least one score weight must be positive.')
        if self.scale_multiplier<=1:raise ValueError('Scale multiplier must exceed one.')
        if self.final_kind not in ('automatic','polygon','point','polyline'):raise ValueError('Unknown final representation.')
        if any(not isinstance(v,int) for v in (self.max_levels,self.max_scale_advances,self.max_attempts)):
            raise ValueError('Automatic limits must be integers.')
        if self.max_levels>100 or self.max_scale_advances>100 or self.max_attempts>100:raise ValueError('Automatic limits cannot exceed 100.')


@dataclass
class Candidate:
    action:dict|None
    target:object
    score:float
    measurements:dict
    reason:str


@dataclass
class AutomaticRun:
    base:object
    states:tuple
    model:object
    core:tuple
    report:dict


class AutomaticRunError(ValueError):
    def __init__(self,message,report,classification='automatic_run_incomplete'):
        super().__init__(message);self.report=dict(classification=classification,message=message,automatic=report)


def _action_key(action):
    """Only parameters consumed by the construction, independent of scale costs."""
    op=action['operation']
    parameters=({'merging':('winner',),'simplification':('tolerance',),
                 'smoothing':('iterations','ratio')}).get(op,())
    return (op,tuple(sorted(action['selected'])),*(action.get(k) for k in parameters))


def _compatible_candidates(candidates,blocked,failed_pairs):
    chosen=[];used=set();keys=set()
    for c in candidates:
        key=_action_key(c.action)
        if key in blocked or used.intersection(c.action['selected']):continue
        if any(frozenset((key,k)) in failed_pairs for k in keys):continue
        if c.target.geom_type=='Polygon' and any(other.target.geom_type=='Polygon'
                and c.target.intersection(other.target).area>TOL*TOL for other in chosen):continue
        chosen.append(c);used.update(c.action['selected']);keys.add(key)
    return chosen


def indicators(g):
    ring=np.asarray(g.exterior.coords[:-1]);vectors=np.roll(ring,-1,axis=0)-ring
    lengths=np.linalg.norm(vectors,axis=1)
    turns=np.array([atan2(float(a[0]*b[1]-a[1]*b[0]),float(a@b)) for a,b in zip(np.roll(vectors,1,axis=0),vectors)])
    turns=turns[np.abs(turns)>1e-8]
    rectilinearity=float(np.mean(np.abs(np.abs(turns)-pi/2)<=pi/18)) if len(turns) else 0.
    irregularity=float(np.mean(turns*np.roll(turns,1)<0)) if len(turns) else 0.
    p=list(g.minimum_rotated_rectangle.exterior.coords)
    sides=[Point(a).distance(Point(b)) for a,b in zip(p,p[1:])]
    return dict(area=float(g.area),perimeter=float(g.length),vertices=len(ring),width=min(sides),length=max(sides),
        elongation=max(sides)/max(min(sides),TOL),rectilinearity=rectilinearity,irregularity=irregularity,
        edge_lengths=lengths.tolist())


def thresholds(settings,scale):
    factor=scale/1000
    return dict(width=settings.min_width_mm*factor,area=settings.min_area_mm2*factor**2,
        length=settings.min_length_mm*factor,gap=settings.gap_mm*factor,tolerance=settings.tolerance_mm*factor)


def violation(g,limits):
    if g.geom_type=='Point':return 0.
    if g.geom_type in ('LineString','MultiLineString'):return max(0.,1-g.length/limits['length'])
    if g.geom_type!='Polygon':return 1.
    m=indicators(g)
    return float(np.mean([max(0.,1-m['area']/limits['area']),max(0.,1-m['width']/limits['width']),
        np.mean(np.asarray(m['edge_lengths'])<limits['tolerance']),
        m['irregularity'] if m['rectilinearity']<.75 else 0.]))


def score_candidate(source,target,others,limits,settings):
    hd=DisplacementReference([source]).compare([target])
    area=float(source.symmetric_difference(target).area/max(source.area,target.area,TOL*TOL))
    terms=[min(1.,area)]
    if hd['normalized_hausdorff'] is not None:terms.append(min(1.,hd['normalized_hausdorff']))
    geometric=float(np.mean(terms))
    old={k for k,g in others.items() if source.distance(g)<=TOL}
    new={k for k,g in others.items() if target.distance(g)<=TOL}
    topology=len(old^new)/max(1,len(old|new));cartography=violation(target,limits)
    weights=(settings.geometry_weight,settings.topology_weight,settings.cartography_weight)
    score=sum(w*v for w,v in zip(weights,(geometric,topology,cartography)))/sum(weights)
    return score,dict(geometric=geometric,external_adjacency_change=topology,cartographic_violation=cartography,
        sampled_nhd=hd['normalized_hausdorff'],hd_reason=hd['hd_reason'],area_change_ratio=area)


def generate_candidates(state,focus,settings,scale,*,final=False,rejections=None):
    limits=thresholds(settings,scale);features=state.features
    polygons={k:features[k].geometry for k in sorted(focus)};candidates=[]
    source_metrics={k:indicators(g) for k,g in polygons.items()}
    def rejected(op,k,exc):
        if rejections is not None:rejections.append(dict(scale=scale,actions=[dict(operation=op,selected=[k])],stage='candidate_geometry',reason=str(exc)))
    def add(op,keys,target,reason,winner=None):
        source=unary_union([polygons[k] for k in keys]);others={k:f.geometry for k,f in features.items() if k not in keys and f.geometry.geom_type=='Polygon'}
        score,measurements=score_candidate(source,target,others,limits,settings)
        measurements['source_indicators']={str(k):{name:v for name,v in source_metrics[k].items() if name!='edge_lengths'} for k in keys}
        action=None if op is None else dict(operation=op,selected=list(keys),winner=winner,
            tolerance=limits['tolerance'],iterations=1,ratio=.25)
        candidates.append(Candidate(action,target,score,measurements,reason))
    for k,g in polygons.items():
        retain_score,_=score_candidate(g,g,{},limits,settings)
        metrics=source_metrics[k]
        if final:add(None,(k,),g,'Retain the polygon representation.')
        if not final or settings.final_kind in ('automatic','polygon'):
            for op in ('simplification','smoothing'):
                if op=='smoothing' and (metrics['rectilinearity']>=.75 or metrics['irregularity']<.25):continue
                try:target=polygon_target(g,op,limits['tolerance'],1,.25)
                except ValueError as exc:rejected(op,k,exc);continue
                if target.equals(g):continue
                add(op,(k,),target,'Remove scale-inappropriate detail.' if op=='simplification' else 'Reduce non-rectilinear turning irregularity.')
                if candidates[-1].score>=retain_score-1e-12:candidates.pop()
        if final:
            for op in ('point','polyline'):
                if settings.final_kind not in ('automatic',op):continue
                if settings.final_kind=='automatic':
                    if op=='point' and metrics['area']>=limits['area']:continue
                    if op=='polyline' and not (metrics['elongation']>=4 and metrics['width']<limits['width'] and metrics['length']>=limits['length']):continue
                try:target=visible_point(g) if op=='point' else skeleton_ridge(g)[0]
                except ValueError as exc:rejected(op,k,exc);continue
                add(op,(k,),target,'Final dimensional representation selected from measured size/elongation and scale constraints.')
    if final:
        if settings.final_kind not in ('automatic','polygon'):
            candidates=[c for c in candidates if c.action and c.action['operation']==settings.final_kind]
        return sorted(candidates,key=lambda c:(c.score,c.action['operation'] if c.action else ''))
    adjacency={k:set() for k in polygons};near={k:set() for k in polygons};pairs=set()
    for a,b in combinations(polygons,2):
        if polygons[a].boundary.intersection(polygons[b].boundary).length>TOL:
            adjacency[a].add(b);adjacency[b].add(a);pairs.add((a,b))
    for a,g in polygons.items():
        closest=sorted((g.distance(h),b) for b,h in polygons.items() if b!=a)[:4]
        for distance,b in closest:
            if distance<=limits['gap']:near[a].add(b);near[b].add(a);pairs.add(tuple(sorted((a,b))))
    groups=set(pairs)
    for graph in (adjacency,near):
        unseen=set(graph)
        while unseen:
            pending=[min(unseen)];component=set()
            while pending:
                k=pending.pop()
                if k in component:continue
                component.add(k);pending.extend(graph[k]-component)
            unseen-=component
            if len(component)>1:groups.add(tuple(sorted(component)))
    for keys in sorted(groups):
        union=unary_union([polygons[k] for k in keys])
        if union.geom_type=='Polygon' and not union.interiors:
            winner=min(keys,key=lambda k:(-polygons[k].area,k))
            add('merging',keys,union,'Edge-connected group; largest-area feature retains its attributes.',winner)
        else:
            add('aggregation',keys,union.convex_hull,'Nearby separated group; convex-hull aggregation with retained lower source cells.')
    return sorted(candidates,key=lambda c:(0 if len(c.action['selected'])>1 else 1,c.score,
        -len(c.action['selected']),tuple(c.action['selected']),c.action['operation']))


def build_automatic_run(session,selected,settings=None,*,cancelled=lambda:False,progress=lambda message:None):
    settings=AutomaticSettings(**settings) if isinstance(settings,dict) else settings or AutomaticSettings();settings.validate()
    selected=tuple(sorted(set(map(int,selected))))
    if not selected or any(k not in session.state.features or session.state.features[k].geometry.geom_type!='Polygon' for k in selected):
        raise ValueError('Select current polygon features for automatic generalization; collapsed symbols are terminal.')
    family={i for k in selected for i in session.state.features[k].lineage}
    class_value=None
    if settings.class_field:
        values=[session.state.features[k].attributes.get(settings.class_field) for k in selected]
        if any(v is None or str(v).strip() in ('','nan','None','<NA>') for v in values) or len({str(v) for v in values})!=1:
            raise ValueError('One final feature requires matching, nonmissing classes. Clear the class constraint or select one compatible class.')
        class_value=values[0]
    base=session.state;saved=_core();fork=copy(session);fork.history=list(session.history[:session.cursor+1]);fork.last_diagnostic=None
    start=fork.cursor;scale=settings.scale;advances=0
    report=dict(settings=asdict(settings),selected=list(selected),lineage=sorted(family),steps=[],rejections=[],status='planning')
    # The private step identity fixes all source geometry, historical cells and
    # neighbours. Failures never escape that state, even if IDs are reused.
    failed_levels={};failed_actions={};failed_pairs={}
    report['cached_constructions_skipped']=0
    def cache_skip(entry):
        entry['cached_skips']=entry.get('cached_skips',0)+1;entry['last_scale']=scale
        report['cached_constructions_skipped']+=1
    def reject(chosen,exc):
        diagnostic=dict(getattr(exc,'report',None) or dict(classification='level_construction_failure',message=str(exc)))
        entry=dict(scale=scale,actions=[dict(c.action) for c in chosen],reason=str(exc),diagnostic=diagnostic)
        report['rejections'].append(entry)
        return entry
    def failed_report(status):
        report.update(status=status,last_scale=scale,constructed_private_levels=len(report['steps']))
        counts=Counter((r.get('diagnostic',{}).get('classification','candidate_geometry'),r['reason'])
                       for r in report['rejections'])
        report['failure_summary']=[dict(classification=k[0],reason=k[1],count=n) for k,n in counts.most_common()]
    def check_cancel():
        if cancelled():raise AutomaticRunError('Automatic run cancelled; original workspace preserved.',report,'automatic_run_cancelled')
    def construct(chosen):
        check_cancel()
        if fork.cursor-start>=settings.max_levels:raise AutomaticRunError('Maximum constructed levels reached before the requested final representation.',report)
        progress(f'Constructing private level {fork.cursor-start+1} at scale 1:{scale:g}')
        proposal=fork.preview_level([c.action for c in chosen],dz=settings.dz)
        for c in chosen:
            if c.action['operation']=='aggregation' and settings.class_field:
                action=next(a for a in proposal.state.operation.actions if tuple(a['selected'])==tuple(c.action['selected']))
                proposal.state.features[action['winner']].attributes[settings.class_field]=class_value
        step=dict(scale=scale,z_s=proposal.state.operation.z_s,z_t=proposal.state.operation.z_t,step_id=proposal.state.step_id,
            actions=[dict(**c.action,score=c.score,measurements=c.measurements,reason=c.reason) for c in chosen],
            affected=list(proposal.affected),notes=list(proposal.notes))
        assigned={k for c in chosen for k in c.action['selected']}
        step['neighbour_changes']=[k for k in proposal.affected if k in proposal.base.features and k not in assigned]
        proposal.state.operation.diagnostics['automatic']=step
        fork.apply(proposal);report['steps'].append(step)
    try:
        _restore(session.core)
        while True:
            check_cancel()
            focus={k for k,f in fork.state.features.items() if set(f.lineage)<=family and set(f.lineage)&family}
            if len(focus)==1:
                candidates=generate_candidates(fork.state,focus,settings,scale,final=True,rejections=report['rejections'])
                completed=False
                for c in candidates[:settings.max_attempts]:
                    if c.action is None:completed=True;break
                    try:construct([c]);completed=True;break
                    except AutomaticRunError:raise
                    except ValueError as exc:reject([c],exc)
                if not completed:raise AutomaticRunError('No feasible final representation satisfies the requested choice.',report)
                break
            if fork.cursor-start>=settings.max_levels:raise AutomaticRunError('Maximum constructed levels reached before consolidation to one feature.',report)
            progress(f'Analyzing {len(focus)} selected-family polygons at scale 1:{scale:g}')
            candidates=generate_candidates(fork.state,focus,settings,scale,rejections=report['rejections'])
            state_key=fork.state.step_id
            local_failures=failed_actions.setdefault(state_key,{})
            conflicts=failed_pairs.setdefault(state_key,set())
            blocked=set(local_failures)
            for c in candidates:
                if _action_key(c.action) in local_failures:cache_skip(local_failures[_action_key(c.action)])
            succeeded=False;batch_limit=None;attempt=0
            while attempt<settings.max_attempts:
                check_cancel()
                chosen=_compatible_candidates(candidates,blocked,conflicts)
                if batch_limit is not None:chosen=chosen[:batch_limit]
                if not chosen:break
                batch_key=(state_key,settings.dz,tuple(sorted(_action_key(c.action) for c in chosen)))
                cached=failed_levels.get(batch_key)
                if cached is not None:
                    cache_skip(cached)
                    if len(chosen)==1:blocked.add(_action_key(chosen[0].action));batch_limit=None
                    else:batch_limit=max(1,len(chosen)//2)
                    continue
                attempt+=1
                try:construct(chosen);succeeded=True;break
                except AutomaticRunError:raise
                except ValueError as exc:
                    entry=reject(chosen,exc);failed_levels[batch_key]=entry
                    diagnostic=entry['diagnostic'];failed=diagnostic.get('failed_action')
                    if diagnostic.get('failure_scope')=='action' and failed:
                        key=_action_key(failed);local_failures[key]=entry;blocked.add(key);batch_limit=None
                    elif len(diagnostic.get('conflicting_actions',()))==2:
                        conflicts.add(frozenset(_action_key(a) for a in diagnostic['conflicting_actions']))
                        batch_limit=None
                    elif len(chosen)>1:
                        # A joint shell failure is not proof that any one action
                        # is invalid. Try progressively smaller ranked prefixes.
                        batch_limit=max(1,len(chosen)//2)
                    else:
                        blocked.add(_action_key(chosen[0].action));batch_limit=None
            if succeeded and sum(bool(set(f.lineage)&family) for f in fork.state.features.values())==1:continue
            if advances>=settings.max_scale_advances:raise AutomaticRunError('Scale-advance limit reached before consolidation to one feature.',report)
            advances+=1;scale*=settings.scale_multiplier
            if not isfinite(scale):raise AutomaticRunError('Scale denominator overflow.',report)
        check_cancel()
        final=[f for f in fork.state.features.values() if set(f.lineage)&family]
        if len(final)!=1 or set(final[0].lineage)!=family:raise AutomaticRunError('Final selected lineage is incomplete.',report)
        from main import validate_model
        validate_model(fork.model)
        report.update(status='complete',final_index=final[0].index,final_geometry=final[0].geometry.geom_type,
            final_scale=scale,remaining_cartographic_violation=violation(final[0].geometry,thresholds(settings,scale)))
        return AutomaticRun(base,tuple(fork.history[start+1:]),fork.model,fork.core,report)
    except AutomaticRunError as exc:
        cancelled_run=exc.report['classification']=='automatic_run_cancelled'
        failed_report('cancelled' if cancelled_run else 'failed')
        if not cancelled_run and report['rejections']:
            message=f"{exc} Last planning failure: {report['rejections'][-1]['reason']}"
            exc.args=(message,);exc.report['message']=message
        raise
    except Exception as exc:
        failed_report('failed')
        raise AutomaticRunError(str(exc),report) from exc
    finally:_restore(saved)


def apply_automatic_run(session,run):
    if session.state is not run.base:raise ValueError('Automatic result is stale; the workspace changed.')
    history=session.history[:session.cursor+1]+list(run.states)
    session.history,session.cursor,session.model,session.core=history,len(history)-1,run.model,run.core
    session.last_automatic_report=run.report
    _restore(run.core)
