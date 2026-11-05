"""Joint, transactional realization of independent actions in one LTS interval."""
from math import isfinite
from collections import defaultdict
from itertools import combinations

from shapely.geometry import Polygon
from shapely.ops import unary_union,linemerge

from generalization_geometry import TOL,polygon_target,visible_point,skeleton_ridge
from generalization_model import (Face,Patch,Feature,State,Proposal,OperationRecord,_core,_restore,
    _conform_footprints,_polygon_patches,_collapse_patches,_recipient,_lift_unchanged_isolated,_layer_faces,realize)
from shell_conformity import arithmetic_bound

OPERATIONS=('simplification','smoothing','point','polyline','merging','aggregation')


def normalize_actions(actions,state):
    result=[];assigned=set()
    for index,raw in enumerate(actions,1):
        if not isinstance(raw,dict):raise ValueError('Each level action must be an object.')
        unknown=set(raw)-{'operation','selected','winner','tolerance','iterations','ratio'}
        if unknown:raise ValueError('Unknown action fields: '+', '.join(sorted(unknown)))
        op=raw.get('operation');selected=tuple(sorted(set(map(int,raw.get('selected',())))))
        if op not in OPERATIONS or not selected:raise ValueError(f'Action {index}: choose a supported operation and polygon features.')
        if assigned.intersection(selected):raise ValueError(f'Features {sorted(assigned.intersection(selected))} are assigned to more than one operation in this level.')
        if any(k not in state.features or state.features[k].geometry.geom_type!='Polygon' for k in selected):
            raise ValueError(f'Action {index}: only polygon features at the current source level may be assigned; collapsed symbols are terminal.')
        tolerance=float(raw.get('tolerance',1));iterations=int(raw.get('iterations',1));ratio=float(raw.get('ratio',.25))
        if not isfinite(tolerance) or tolerance<0 or not isfinite(ratio) or not 0<ratio<.5 or not 1<=iterations<=8:
            raise ValueError(f'Action {index}: invalid tolerance, smoothing iterations (1–8), or ratio (0–0.5).')
        winner=raw.get('winner')
        if op in ('merging','aggregation') and len(selected)<2:raise ValueError(f'Action {index}: select at least two polygons.')
        if op=='merging' and (winner is None or int(winner) not in selected):raise ValueError(f'Action {index}: choose a selected merge winner.')
        assigned.update(selected)
        result.append(dict(operation=op,selected=selected,winner=int(winner) if op=='merging' else None,
                           tolerance=tolerance,iterations=iterations,ratio=ratio))
    if not result:raise ValueError('Add at least one operation to the level.')
    return sorted(result,key=lambda a:(a['selected'],a['operation']))


def _action_record(action,winner,affected,retired,lo,hi):
    return dict(name=action['operation'],selected=action['selected'],winner=winner,
        affected=tuple(sorted(affected)),retired=tuple(retired),z_s=lo,z_t=hi,
        parameters={k:action[k] for k in ('tolerance','iterations','ratio')})


def preview_level(session,actions,*,dz=10.):
    saved=_core()
    session.last_diagnostic=None
    try:
        actions=normalize_actions(actions,session.state)
        if not isfinite(dz) or dz<=TOL:raise ValueError('Height increment must be finite and positive.')
        if len(actions)==1:
            action=actions[0];proposal=session._preview_single(**action,dz=dz)
            r=proposal.state.operation
            r.actions=(_action_record(action,r.winner,r.affected,r.retired,r.z_s,r.z_t),)
            return proposal
        return _mixed(session,actions,dz)
    except ValueError as exc:
        report=getattr(exc,'report',dict(classification='level_planning_constraint',message=str(exc)))
        report.update(level_actions=actions,source_z=session.state.z)
        session.last_diagnostic=report;exc.report=report
        raise
    finally:_restore(saved)


def _mixed(session,actions,dz):
    state=session.state;lo=state.z;hi=lo+dz
    if not isfinite(hi) or hi<=lo:raise ValueError('The target level is not representably above the source level.')
    sources=_conform_footprints({k:f.geometry for k,f in state.features.items() if f.geometry.geom_type=='Polygon'})
    selected={k for a in actions for k in a['selected']}
    targets={};patches=[];action_patches=[];notes=[];diagnostics={};remap={};groups={};winners=[]
    next_id=max(k for s in session.history for k in s.features)+1
    for index,action in enumerate(actions,1):
        op=action['operation'];keys=action['selected'];local=[];winner=None
        if op in ('merging','aggregation'):
            if op=='merging':
                winner=action['winner'];target=unary_union([sources[k] for k in keys])
                if target.geom_type!='Polygon' or target.interiors:raise ValueError(f'Action {index}: merging needs an edge-connected, hole-free union.')
                local,_=_polygon_patches(winner,sources[winner],target,lo,hi)
            else:
                from aggregation_geometry import build_aggregation_preview
                from aggregation_layer import _plan_shell
                winner=next_id;next_id+=1
                preview=build_aggregation_preview({str(k):sources[k] for k in keys},tolerance=TOL)
                plans,_,_=_plan_shell(preview,lo-1,lo,lo+.8*dz,hi)
                local=[Patch(winner,p.points) for p in plans if p.part=='connecting'];target=preview.hull
                remap.update({k:winner for k in keys})
                diagnostics[f'action_{index}_skeleton_timing']=dict(preview.timing_diagnostics)
            targets[winner]=target;groups[winner]=action
        else:
            for k in keys:
                if op in ('simplification','smoothing'):
                    target=polygon_target(state.features[k].geometry,op,action['tolerance'],action['iterations'],action['ratio'])
                    ps,_=_polygon_patches(k,sources[k],target,lo,hi)
                elif op=='point':
                    target=visible_point(sources[k]);ps=_collapse_patches(k,sources[k],target,lo,hi)
                else:
                    target,method=skeleton_ridge(sources[k]);notes.append(f'Feature {k}: {method}')
                    ps=_collapse_patches(k,sources[k],target,lo,hi)
                targets[k]=target;local.extend(ps)
        action_patches.append(local);patches.extend(local);winners.append(winner)
    explicit={k:g for k,g in targets.items() if g.geom_type=='Polygon'}
    for (a,g),(b,h) in combinations(explicit.items(),2):
        if g.intersection(h).area>TOL*TOL:raise ValueError(f'Level targets {a} and {b} overlap; edit the assigned actions.')
    # Independent operations must not assign different height fields to the
    # same positive-area transition region. Boundary contacts are conformed by
    # the existing interval builder.
    for left,right in combinations(action_patches,2):
        for a in left:
            for b in right:
                overlap=a.geometry.intersection(b.geometry)
                if overlap.area>TOL*TOL:
                    raise ValueError('Transition regions of separate actions overlap. Use separate levels or change their parameters.')
    occupied=unary_union(list(explicit.values()))
    new={k:g.difference(occupied) for k,g in sources.items() if k not in selected}
    gains=defaultdict(list)
    for action,local in zip(actions,action_patches):
        if action['operation'] in ('merging','aggregation'):continue
        for patch in local:
            region=patch.geometry.intersection(sources[patch.source]).difference(occupied)
            if region.area>TOL*TOL:
                recipient=_recipient(patch,sources,selected,lo)
                if recipient is not None:gains[recipient].append(region)
    for k,regions in gains.items():new[k]=unary_union([new[k],*regions])
    new.update(explicit)
    for k,g in new.items():
        if g.is_empty or g.geom_type!='Polygon' or g.interiors or not g.is_valid:
            raise ValueError(f'Joint neighbour update makes feature {k} empty, split, holed, or invalid.')
    for (k,g),(j,h) in combinations(new.items(),2):
        if g.intersection(h).area>TOL*TOL:raise ValueError(f'Joint targets {j} and {k} overlap.')
    old={}
    for k,g in sources.items():
        owner=remap.get(k,k);old[owner]=unary_union([old.get(owner,Polygon()),g])
    lifted=_lift_unchanged_isolated(state,old,new,lo,hi)
    lower_sources=set(remap)
    historical=[Face(f.owner,list(f.points),f.part) for f in state.faces
        if f.owner not in lifted and (f.owner in lower_sources or not all(p[2]==lo for p in f.points))]
    historical.extend(f for shell in lifted.values() for f in shell)
    layer=_layer_faces({k:g for k,g in old.items() if k not in lifted},{k:g for k,g in new.items() if k not in lifted},patches,lo,hi,diagnostics=diagnostics)
    layer.extend(Face(remap[f.owner],list(reversed(f.points)),'bottom') for f in state.faces
        if f.owner in remap and all(p[2]==lo for p in f.points))
    model,faces,core=realize([*historical,*layer],session.origin)
    actual=session._validate_sections(model,sources,new,lo,hi)
    for k,g in targets.items():
        if g.geom_type!='Polygon':
            if str(k) not in actual or actual[str(k)].hausdorff_distance(g)>TOL*10:raise ValueError(f'Feature {k}: final collapse contact differs from its target.')
            targets[k]=linemerge(actual[str(k)]) if actual[str(k)].geom_type=='MultiLineString' else actual[str(k)]
    for k in new:
        g=actual[str(k)]
        if g.geom_type!='Polygon' or g.interiors:raise ValueError(f'Feature {k}: unsupported realized top.')
        new[k]=g.simplify(arithmetic_bound(g.exterior.coords),preserve_topology=True)
    features={k:Feature(f.index,new.get(k,f.geometry),dict(f.attributes),f.lineage,f.label) for k,f in state.features.items() if k not in selected}
    for k,g in targets.items():
        if k in groups:
            action=groups[k];keys=action['selected'];op=action['operation']
            attrs=dict(state.features[k].attributes) if op=='merging' else {'operation':'aggregation'}
            # Preserve uniform class attributes and source provenance on a new aggregate.
            if op=='aggregation':
                for field,value in state.features[keys[0]].attributes.items():
                    if all(str(state.features[i].attributes.get(field))==str(value) for i in keys):attrs[field]=value
                attrs['source_members']=[dict(index=i,lineage=list(state.features[i].lineage),attributes=dict(state.features[i].attributes)) for i in keys]
            lineage=tuple(sorted({n for i in keys for n in state.features[i].lineage}))
            features[k]=Feature(k,new[k],attrs,lineage,f'Feature {k}' if op=='merging' else f'Aggregate {k}')
        else:
            f=state.features[k];features[k]=Feature(k,new.get(k,g),dict(f.attributes),f.lineage,f.label)
    affected=tuple(sorted(selected|{k for k,g in new.items() if k not in sources or sources[k].symmetric_difference(g).area>TOL*TOL}))
    records=tuple(_action_record(a,w,affected,[k for k in a['selected'] if k not in features],lo,hi) for a,w in zip(actions,winners))
    record=OperationRecord('batch',tuple(sorted(selected)),affected,
        session.history[session.cursor-1].z if session.cursor else min(p[2] for f in state.faces for p in f.points),lo,hi,
        retired=tuple(sorted(selected-set(features))),parameters=dict(dz=dz),diagnostics=diagnostics,
        z_m=lo+.8*dz if remap else None,actions=records)
    label=' + '.join(f"{a['operation']} {','.join(map(str,a['selected']))}" for a in actions)
    result=State(features,faces,hi,f'Level: {label}; Z={lo:g}–{hi:g}',affected,record,parent_step_id=state.step_id)
    notes.append('All actions share one interval; neighbours resolved jointly. '+f'{len(lifted)} unchanged isolated roofs lifted.')
    return Proposal(state,result,model,core,targets,affected,notes)
