"""Private adjacent-operation timelines, composed into one transactional level."""
from copy import copy
from itertools import combinations
from math import isfinite

import numpy as np
from shapely import constrained_delaunay_triangles
from shapely.geometry import Polygon,LineString,box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from generalization_geometry import TOL,polygon_target,visible_point,skeleton_ridge
from generalization_model import Face,Feature,State,Proposal,OperationRecord,_prism,parts,realize,_core,_restore
from generalization_levels import _planning_action,_action_record
from shell_conformity import arithmetic_bound,TransitionShellError


def adjacent_components(actions,state):
    units=[]
    for index,action in enumerate(actions,1):
        selections=[action['selected']] if action['operation'] in ('merging','aggregation') else [(k,) for k in action['selected']]
        units.extend(dict(action={**action,'selected':tuple(keys)},original=action,index=index) for keys in selections)
    footprints=[unary_union([state.features[k].geometry for k in u['action']['selected']]) for u in units]
    graph={i:set() for i in range(len(units))}
    for i,j in combinations(graph,2):
        if footprints[i].boundary.intersection(footprints[j].boundary).length>TOL:
            graph[i].add(j);graph[j].add(i)
    for i,unit in enumerate(units):
        unit['neighbours']=tuple(units[j]['action']['selected'] for j in sorted(graph[i]))
    components=[];unseen=set(graph)
    while unseen:
        pending=[min(unseen)];component=set()
        while pending:
            i=pending.pop()
            if i in component:continue
            component.add(i);pending.extend(graph[i]-component)
        unseen-=component;components.append([units[i] for i in sorted(component)])
    return components


def _priority(state,action):
    geometries=[state.features[k].geometry for k in action['selected']]
    if any(g.geom_type!='Polygon' for g in geometries):raise ValueError('A pending scheduled feature is no longer a polygon.')
    source=unary_union(geometries);op=action['operation']
    if op in ('simplification','smoothing'):
        target=polygon_target(geometries[0],op,action['tolerance'],action['iterations'],action['ratio'])
    elif op=='point':target=visible_point(geometries[0])
    elif op=='polyline':target=skeleton_ridge(geometries[0])[0]
    elif op=='merging':
        if source.geom_type!='Polygon' or source.interiors:raise ValueError('Merging needs an edge-connected, hole-free union.')
        target=source
    else:target=source.convex_hull
    return float(source.symmetric_difference(target).area),float(source.area),float(target.area)


def _reserve_aggregates(session,components):
    next_id=max(k for state in session.history for k in state.features)+1
    for unit in sorted((u for component in components for u in component),key=lambda u:(u['action']['selected'],u['action']['operation'])):
        if unit['action']['operation']=='aggregation':unit['aggregate_id']=next_id;next_id+=1


def _aggregate_attributes(prior,unit,proposal):
    if unit['action']['operation']!='aggregation':return
    members=[prior.features[k] for k in unit['action']['selected']];f=proposal.state.features[proposal.state.operation.winner]
    for field,value in members[0].attributes.items():
        if all(str(m.attributes.get(field))==str(value) for m in members):f.attributes[field]=value
    f.attributes['source_members']=[dict(index=m.index,lineage=list(m.lineage),attributes=dict(m.attributes)) for m in members]


def _polygons(g):
    yield from parts(g)


def _lines(g):
    if g.is_empty:return
    if g.geom_type=='LineString':yield g
    elif hasattr(g,'geoms'):
        for child in g.geoms:yield from _lines(child)


def _z_clip(ring,z,above):
    result=[]
    for a,b in zip(ring,ring[1:]+ring[:1]):
        inside_a=a[2]>=z if above else a[2]<=z
        inside_b=b[2]>=z if above else b[2]<=z
        if inside_a:result.append(a)
        if inside_a!=inside_b:
            t=(z-a[2])/(b[2]-a[2]);result.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1]),z))
    return result


def _clip_face(face,region,lo,hi,*,outside=False):
    """Clip real facets, including vertical walls, without moving time levels."""
    a=np.asarray(face.points,dtype=float)
    if a[:,2].max()<lo or a[:,2].min()>hi:return []
    if region is None and a[:,2].min()>=lo and a[:,2].max()<=hi:return [face]
    relative=a-a[0];normal=np.sum(np.cross(relative,np.roll(relative,-1,axis=0)),axis=0)
    bound=arithmetic_bound(a);span=max(np.linalg.norm(p[:2]-a[0,:2]) for p in a)
    result=[]
    if abs(normal[2])<=bound*span:
        if span==0:return []
        delta=max((p[:2]-a[0,:2] for p in a),key=np.linalg.norm);l2=float(delta@delta)
        parameters=(a[:,:2]-a[0,:2])@delta/l2
        line=LineString([a[0,:2]+float(t)*delta for t in (parameters.min(),parameters.max())])
        pieces=line if region is None else line.difference(region) if outside else line.intersection(region)
        flat=Polygon([(float(t),float(p[2])) for t,p in zip(parameters,a)])
        for segment in _lines(pieces):
            ends=np.asarray(segment.coords);ts=(ends-a[0,:2])@delta/l2;ta,tb=float(ts.min()),float(ts.max())
            if tb<=ta:continue
            for p in _polygons(flat.intersection(box(ta,lo,tb,hi))):
                p=orient(p,1 if flat.exterior.is_ccw else -1)
                ring=[]
                for t,z in p.exterior.coords[:-1]:
                    if abs(z-lo)<=bound:z=lo
                    elif abs(z-hi)<=bound:z=hi
                    xy=a[0,:2]+t*delta
                    # Reuse the actual clipping endpoints on shared XY cuts.
                    if t==ts[0]:xy=ends[0]
                    elif t==ts[-1]:xy=ends[-1]
                    ring.append((float(xy[0]),float(xy[1]),float(z)))
                if p.area>bound*bound:result.append(Face(face.owner,ring,face.part))
        return result
    footprint=Polygon(a[:,:2]);pieces=footprint if region is None else footprint.difference(region) if outside else footprint.intersection(region)
    for polygon in _polygons(pieces):
        split=polygon.interiors or a[:,2].min()<lo or a[:,2].max()>hi
        pieces2=constrained_delaunay_triangles(polygon).geoms if split else [polygon]
        for p in pieces2:
            p=orient(p,1 if normal[2]>0 else -1);ring=[]
            for x,y in p.exterior.coords[:-1]:
                z=float(a[0,2]) if np.ptp(a[:,2])==0 else float(a[0,2]-(normal[0]*(x-a[0,0])+normal[1]*(y-a[0,1]))/normal[2])
                for vertex in a:
                    if max(abs(x-vertex[0]),abs(y-vertex[1]))<=bound:z=float(vertex[2]);break
                if abs(z-lo)<=bound:z=lo
                elif abs(z-hi)<=bound:z=hi
                ring.append((float(x),float(y),z))
            ring=_z_clip(_z_clip(ring,lo,True),hi,False)
            ring=[p for i,p in enumerate(ring) if p!=ring[i-1]]
            if len(ring)>=3 and p.area>bound*bound:result.append(Face(face.owner,ring,face.part))
    return result


def _influence(base,result,selected,lo):
    geometries=[base.features[k].geometry for k in selected]
    geometries.extend(f.geometry for k,f in result.features.items()
                      if (k in selected or k not in base.features) and f.geometry.geom_type=='Polygon')
    for face in result.faces:
        zs=[p[2] for p in face.points]
        if max(zs)<=lo or max(zs)==min(zs):continue
        p=Polygon([point[:2] for point in face.points])
        xy=np.asarray([point[:2] for point in face.points]);span=max(np.linalg.norm(q-xy[0]) for q in xy)
        if p.area>TOL*TOL and 2*p.area>arithmetic_bound(face.points)*span:geometries.append(p)
    return unary_union(geometries)


def _compose_plans(session,plans,selected,boundaries,lo,hi,check_cancel):
    saved=_core()
    try:return _compose(session,plans,selected,boundaries,lo,hi,check_cancel)
    finally:_restore(saved)


def _compose(session,plans,selected,boundaries,lo,hi,check_cancel):
    """Compose validated private shells without modifying accepted plan states."""
    base=session.state
    if len(plans)==1:
        model=plans[0]['model'];faces=plans[0]['state'].faces;core=plans[0]['core']
        features={k:Feature(f.index,f.geometry,dict(f.attributes),f.lineage,f.label)
                  for k,f in plans[0]['state'].features.items()}
    else:
        check_cancel()
        for a,b in combinations(plans,2):
            if a['region'].intersection(b['region']).area>TOL*TOL:
                raise TransitionShellError('Parallel adjacent components have overlapping transition regions.',
                    'level_transition_overlap',stage='scheduled_components',failure_scope='level',
                    component_actions=[a['actions'],b['actions']])
        region=unary_union([p['region'] for p in plans]);upper=[]
        def clip_bands(face,footprint,*,outside=False):
            if len({p[2] for p in face.points})==1:
                return _clip_face(face,footprint,lo,hi,outside=outside)
            return [f for start,end in zip(boundaries,boundaries[1:])
                    for f in _clip_face(face,footprint,start,end,outside=outside)]
        polygons={k:f.geometry for k,f in base.features.items() if f.geometry.geom_type=='Polygon'}
        for owner,g in polygons.items():
            if owner in selected:continue
            for face in _prism(owner,g,lo,hi):
                if face.part!='bottom':upper.extend(clip_bands(face,region,outside=True))
        for plan in plans:
            check_cancel()
            owned=plan['owners']|{k for k in plan['state'].features if k not in base.features}
            for face in plan['state'].faces:
                if face.owner in selected and face.owner not in owned:continue
                # Selected cells retain their own validated facets; passive
                # neighbours are clipped to each plan's transition region.
                upper.extend(clip_bands(face,None if face.owner in owned else plan['region']))
        historical=[f for f in base.faces if not (f.owner in polygons and all(p[2]==lo for p in f.points))]
        model,faces,core=realize([*historical,*upper],session.origin)
        features={k:Feature(f.index,f.geometry,dict(f.attributes),f.lineage,f.label) for k,f in base.features.items()}
        for plan in plans:
            for k in plan['owners']:features.pop(k,None)
            for k,f in plan['state'].features.items():
                if k in plan['owners'] or k not in base.features:
                    features[k]=Feature(f.index,f.geometry,dict(f.attributes),f.lineage,f.label)
        for k,f in features.items():
            if f.geometry.geom_type!='Polygon' or k in selected or k not in base.features:continue
            pieces=[polygons[k].difference(region)] if k in polygons else []
            pieces.extend(p['state'].features[k].geometry.intersection(p['region']) for p in plans
                          if k in p['state'].features and p['state'].features[k].geometry.geom_type=='Polygon')
            g=unary_union([polygon for piece in pieces for polygon in _polygons(piece)])
            if g.is_empty or g.geom_type!='Polygon' or g.interiors or not g.is_valid:
                raise ValueError(f'Scheduled neighbour update makes feature {k} empty, split, holed, or invalid.')
            f.geometry=g
    old={k:f.geometry for k,f in base.features.items() if f.geometry.geom_type=='Polygon'}
    new={k:f.geometry for k,f in features.items() if f.geometry.geom_type=='Polygon'}
    actual=session._validate_sections(model,old,new,lo,hi)
    check_cancel()
    for k,f in features.items():
        if f.geometry.geom_type=='Polygon':f.geometry=actual[str(k)].simplify(arithmetic_bound(actual[str(k)].exterior.coords),preserve_topology=True)
    return model,faces,core,features


def preview_scheduled(session,actions,components,dz,*,cancelled=lambda:False):
    base=session.state;lo=base.z;hi=lo+dz
    def check_cancel():
        if cancelled():raise ValueError('Generalization cancelled; original workspace preserved.')
    if not isfinite(hi) or hi<=lo:raise ValueError('The target level is not representably above the source level.')
    # Reserve aggregate identities before priorities can change execution order.
    _reserve_aggregates(session,components)
    plans=[];schedule=[];records=[];recoveries=[];feature_errors=[];affected=set();notes=[]
    requested={k for a in actions for k in a['selected']}
    for component_index,component in enumerate(components,1):
        check_cancel()
        fork=copy(session);fork.history=list(session.history[:session.cursor+1]);remaining=list(component)
        if dz/len(component)<=TOL:
            raise TransitionShellError('Adjacent subintervals must be representable and longer than the geometric tolerance.',
                                       'unsupported_transition_interval',stage='adjacent_schedule',component=component_index)
        applied_owners=set();component_actions=[];slot=0
        # Equal subintervals among units that still remain; a skipped failure
        # redistributes the leftover height instead of leaving the level short.
        while remaining:
            check_cancel()
            count=len(remaining);start=fork.state.z;end=start+(hi-start)/count
            if end-start<=TOL:
                raise TransitionShellError('Adjacent subintervals must be representable and longer than the geometric tolerance.',
                                           'unsupported_transition_interval',stage='adjacent_schedule',component=component_index)
            ranked=[]
            for unit in remaining:
                with _planning_action(unit['original'],unit['index']):score,source_area,target_area=_priority(fork.state,unit['action'])
                ranked.append((score,unit['action']['selected'],unit['action']['operation'],unit,source_area,target_area))
            score,_,_,unit,source_area,target_area=min(ranked,key=lambda r:r[:3]);remaining.remove(unit);slot+=1
            prior=fork.state
            try:
                from generalization_recovery import preview_unit
                proposal=preview_unit(fork,unit['action'],end-start,cancelled=cancelled,
                                      **({'_aggregate_id':unit['aggregate_id']} if 'aggregate_id' in unit else {}))
            except ValueError as exc:
                if cancelled() or 'cancelled' in str(exc).lower():
                    raise
                # Skip this feature/unit and continue with the rest of the level.
                report=dict(getattr(exc,'report',dict(classification='level_construction_failure',message=str(exc))))
                report.update(failure_scope='feature',scheduled_action=dict(unit['original']),
                              scheduled_unit=dict(unit['action']),component=component_index,slot=slot,z_s=start,z_t=end,
                              selected=list(unit['action']['selected']),operation=unit['action']['operation'],
                              message=str(exc))
                feature_errors.append(report)
                notes.append(f"Skipped {unit['action']['operation']} {','.join(map(str,unit['action']['selected']))}: {exc}")
                continue
            r=proposal.state.operation
            recoveries.extend(r.diagnostics.get('simplification_recovery',()))
            _aggregate_attributes(prior,unit,proposal)
            action_record=_action_record(unit['action'],r.winner,r.affected,r.retired,start,end)
            if r.z_m is not None:action_record['z_m']=r.z_m
            records.append(action_record)
            schedule.append(dict(component=component_index,slot=slot,action_index=unit['index'],operation=r.name,
                selected=list(r.selected),z_s=start,z_t=end,changed_area=score,source_area=source_area,target_area=target_area,
                priority_metric='symmetric_difference_area_m2'))
            affected.update(proposal.affected);notes.extend(proposal.notes);fork.apply(proposal)
            applied_owners.update(unit['action']['selected']);component_actions.append(unit['original'])
        if not applied_owners:
            continue
        plans.append(dict(state=fork.state,model=fork.model,core=fork.core,owners=applied_owners,
                          region=_influence(base,fork.state,applied_owners,lo),actions=component_actions))
    if not plans:
        report=dict(classification='all_features_failed',message='Every selected feature failed during generalization.',
                    failure_scope='level',feature_errors=feature_errors,level_actions=actions,source_z=lo)
        exc=ValueError(report['message']);exc.report=report;raise exc
    selected={k for r in records for k in r['selected']}
    boundaries=sorted({lo,hi,*[s['z_s'] for s in schedule],*[s['z_t'] for s in schedule],
        *[s[key] for r in recoveries for a in r['attempts'] if a['status']=='passed' for s in a['steps'] for key in ('z_s','z_t')]})
    model,faces,core,features=_compose_plans(session,plans,selected,boundaries,lo,hi,check_cancel)
    name=actions[0]['operation'] if len(actions)==1 else 'batch'
    records.sort(key=lambda r:(r['z_s'],r['selected'],r['name']))
    schedule.sort(key=lambda r:(r['z_s'],r['component'],r['slot']))
    parameters={k:actions[0][k] for k in ('tolerance','iterations','ratio')} if len(actions)==1 else {}
    record=OperationRecord(name,tuple(sorted(selected)),tuple(sorted(affected)),
        session.history[session.cursor-1].z if session.cursor else min(p[2] for f in base.faces for p in f.points),lo,hi,
        winner=records[0]['winner'] if len(records)==1 else None,retired=tuple(sorted(selected-set(features))),
        parameters=dict(dz=dz,**parameters),diagnostics=dict(adjacent_schedule=schedule),actions=tuple(records))
    if recoveries:record.diagnostics['simplification_recovery']=recoveries
    if feature_errors:
        record.diagnostics['feature_errors']=feature_errors
        skipped=sorted({k for err in feature_errors for k in err.get('selected',())})
        notes.append('Skipped failed features (unchanged this step): '+', '.join(map(str,skipped)))
    label=' + '.join(f"{r['name']} {','.join(map(str,r['selected']))}" for r in records) or 'empty'
    skipped=requested-selected
    state=State(features,faces,hi,f'Level: {label}; adjacent substeps; Z={lo:g}–{hi:g}',tuple(sorted(affected)),record,parent_step_id=base.step_id)
    targets={k:f.geometry for k,f in features.items() if k in selected or k not in base.features}
    if schedule:
        notes.append('Adjacent operations staged by changed footprint area: '+ '; '.join(
            f"{s['operation']} {','.join(map(str,s['selected']))}: Z={s['z_s']:g}–{s['z_t']:g}" for s in schedule))
    if skipped and not feature_errors:
        notes.append('Deferred selected features this step: '+', '.join(map(str,sorted(skipped))))
    return Proposal(base,state,model,core,targets,tuple(sorted(affected)),list(dict.fromkeys(notes)))


def preview_prioritized(session,actions,components,dz,*,cancelled=lambda:False):
    """Full-interval, nonadjacent winners; shared winners precede area ranking."""
    from generalization_recovery import preview_unit
    base=session.state;lo=base.z;hi=lo+dz
    def check_cancel():
        if cancelled():raise ValueError('Generalization cancelled; original workspace preserved.')
    if not isfinite(hi) or hi<=lo:raise ValueError('The target level is not representably above the source level.')
    _reserve_aggregates(session,components)
    plans=[];records=[];recoveries=[];errors=[];notes=[];affected=set();selected=set();result=None
    priority=dict(consider_substeps=False,policy='shared_winners_then_greatest_changed_area',
                  priority_metric='symmetric_difference_area_m2',selected=[],deferred=[],groups=[])
    def fail(exc,unit,entry,group,phase):
        if cancelled() or 'cancelled' in str(exc).lower():raise exc
        report=dict(getattr(exc,'report',dict(classification='level_construction_failure',message=str(exc))))
        report.update(failure_scope='feature',scheduled_action=dict(unit['original']),scheduled_unit=dict(unit['action']),
                      component=group,selected=list(unit['action']['selected']),operation=unit['action']['operation'],
                      z_s=lo,z_t=hi,phase=phase,message=str(exc))
        entry.update(status='failed',failure=report);errors.append(report)
        notes.append(f"Skipped {unit['action']['operation']} {','.join(map(str,unit['action']['selected']))}: {exc}")
    for group_index,component in enumerate(components,1):
        check_cancel();entries={};ranks={}
        group=dict(component=group_index,candidates=[],selected=[],deferred=[]);priority['groups'].append(group)
        for unit in component:
            check_cancel();action=unit['action'];key=action['selected']
            entry=dict(selected=list(key),operation=action['operation'],neighbours=[list(k) for k in unit['neighbours']],
                       shared_winner=False,status='pending',z_s=lo,z_t=hi)
            entries[key]=entry;group['candidates'].append(entry)
            try:score,source_area,target_area=_priority(base,action)
            except ValueError as exc:fail(exc,unit,entry,group_index,'priority');continue
            entry.update(changed_area=score,source_area=source_area,target_area=target_area)
            ranks[key]=(-score,key,action['operation'])
        for unit in component:
            key=unit['action']['selected']
            if key in ranks:
                entries[key]['shared_winner']=(len(unit['neighbours'])>=2 and
                    any(k in ranks and ranks[key]<ranks[k] for k in unit['neighbours']))
        ordered=sorted((u for u in component if u['action']['selected'] in ranks),
                       key=lambda u:(not entries[u['action']['selected']]['shared_winner'],*ranks[u['action']['selected']]))
        blocked={}
        for rank,unit in enumerate(ordered,1):
            check_cancel();action=unit['action'];key=action['selected'];entry=entries[key];entry['rank']=rank
            if key in blocked:
                entry.update(status='deferred',blocked_by=[list(k) for k in blocked[key]])
                deferred=dict(selected=list(key),operation=action['operation'])
                group['deferred'].append(deferred);priority['deferred'].append(deferred)
                continue
            fork=copy(session);fork.history=list(session.history[:session.cursor+1]);phase='construction'
            try:
                proposal=preview_unit(fork,action,dz,cancelled=cancelled,
                                      **({'_aggregate_id':unit['aggregate_id']} if 'aggregate_id' in unit else {}))
                _aggregate_attributes(base,unit,proposal)
                recovery=proposal.state.operation.diagnostics.get('simplification_recovery',[])
                times={lo,hi,*[s[k] for r in recovery for a in r['attempts'] if a['status']=='passed'
                              for s in a['steps'] for k in ('z_s','z_t')]}
                plan=dict(state=proposal.state,model=proposal.model,core=proposal.core,owners=set(key),
                          region=_influence(base,proposal.state,set(key),lo),actions=[unit['original']],boundaries=times)
                phase='composition';tentative=[*plans,plan]
                boundaries=sorted(set().union(*(p['boundaries'] for p in tentative)))
                composed=_compose_plans(session,tentative,selected|set(key),boundaries,lo,hi,check_cancel)
                check_cancel()
            except ValueError as exc:fail(exc,unit,entry,group_index,phase);continue
            # A failed candidate never blocks its neighbours. Commit only to
            # the private plan after both its own shell and composition pass.
            plans.append(plan);result=composed;entry['status']='applied';selected.update(key)
            group['selected'].extend(key);affected.update(proposal.affected);recoveries.extend(recovery);notes.extend(proposal.notes)
            r=proposal.state.operation;record=_action_record(action,r.winner,r.affected,r.retired,lo,hi)
            if r.z_m is not None:record['z_m']=r.z_m
            records.append(record)
            for neighbour in unit['neighbours']:blocked.setdefault(neighbour,[]).append(key)
    priority['selected']=sorted(selected)
    if not plans:
        report=dict(classification='all_features_failed',message='Every selected feature failed during generalization.',
                    failure_scope='level',feature_errors=errors,priority_selection=priority,level_actions=actions,source_z=lo)
        exc=ValueError(report['message']);exc.report=report;raise exc
    check_cancel();model,faces,core,features=result;records.sort(key=lambda r:(r['selected'],r['name']))
    parameters={k:actions[0][k] for k in ('tolerance','iterations','ratio')} if len(actions)==1 else {}
    diagnostics=dict(priority_selection=priority)
    if errors:diagnostics['feature_errors']=errors
    if recoveries:diagnostics['simplification_recovery']=recoveries
    record=OperationRecord(actions[0]['operation'] if len(actions)==1 else 'batch',tuple(sorted(selected)),tuple(sorted(affected)),
        session.history[session.cursor-1].z if session.cursor else min(p[2] for f in base.faces for p in f.points),lo,hi,
        winner=records[0]['winner'] if len(records)==1 else None,retired=tuple(sorted(selected-set(features))),
        parameters=dict(dz=dz,consider_substeps=False,**parameters),diagnostics=diagnostics,actions=tuple(records))
    state=State(features,faces,hi,f"Level: priority winners {','.join(map(str,sorted(selected)))}; Z={lo:g}–{hi:g}",
                tuple(sorted(affected)),record,parent_step_id=base.step_id)
    notes.append('Substeps off: shared winners first, then greatest changed footprint area; applied '+', '.join(map(str,sorted(selected))))
    if priority['deferred']:
        notes.append('Deferred adjacent operations: '+', '.join(','.join(map(str,d['selected'])) for d in priority['deferred']))
    session.last_diagnostic=None
    return Proposal(base,state,model,core,{k:f.geometry for k,f in features.items() if k in selected or k not in base.features},
                    tuple(sorted(affected)),list(dict.fromkeys(notes)))
