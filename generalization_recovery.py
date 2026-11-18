"""Bounded simplification continuation with an immutable requested target."""
from copy import copy,deepcopy

from shapely.geometry import Polygon

from generalization_geometry import TOL,polygon_target
from generalization_model import State,Proposal,OperationRecord,_core,_restore
from shell_conformity import TransitionShellError,CoordinateRegistry,arithmetic_bound

SUBSTEP_COUNTS=(2,4,8,16)
RETRYABLE_TOPOLOGY={'nonmanifold_edge_incidence','unmatched_edges','inconsistent_orientation',
                    'invalid_vertex_link','self_intersection','transition_column_incidence'}


def retryable(exc):
    return isinstance(exc,TransitionShellError) and exc.report.get('classification') in RETRYABLE_TOPOLOGY


def recoverable_level(exc,actions):
    """Route a failed joint level only when simplification can be responsible."""
    if not retryable(exc):return False
    if all(a['operation']=='simplification' for a in actions):return True
    report=exc.report;owners=set(report.get('owners',()))
    if report.get('cell') is not None:owners.add(report['cell'])
    owners.update(c[key] for c in report.get('columns',()) for key in ('old','new') if c.get(key) is not None)
    return any(a['operation']=='simplification' and owners.intersection(a['selected']) for a in actions)


def preview_unit(session,action,dz,*,cancelled=lambda:False,**internal):
    saved=_core()
    try:
        if cancelled():raise ValueError('Generalization cancelled; original workspace preserved.')
        try:return session._preview_single(**action,dz=dz,**internal)
        except ValueError as exc:
            if action['operation']!='simplification' or len(action['selected'])!=1 or not retryable(exc):raise
            return _continue(session,action,dz,exc,cancelled)
    finally:_restore(saved)


def _registered_target(source,target):
    """Use current evaluations of the same boundary, not modelling-tolerance snaps."""
    points=[*source.exterior.coords,*target.exterior.coords];bound=arithmetic_bound(points)
    if source.boundary.hausdorff_distance(target.boundary)<=bound and source.symmetric_difference(target).area<=bound*(source.length+target.length):
        return source
    registry=CoordinateRegistry(points,dimension=2)
    for p in source.exterior.coords:registry.canonical(p)
    return Polygon([registry.canonical(p) for p in target.exterior.coords])


def _continue(session,action,dz,original,cancelled):
    from generalization_levels import _action_record
    base=session.state;owner=action['selected'][0];source=base.features[owner].geometry
    tolerance=action['tolerance'];lo=base.z;hi=lo+dz;saved=_core()
    goal=polygon_target(source,'simplification',tolerance,action['iterations'],action['ratio'])
    recovery=dict(selected=[owner],requested_tolerance=tolerance,z_s=lo,z_t=hi,
                  target_policy='Frozen original-source simplification target',
                  original_failure=deepcopy(original.report),attempts=[],status='planning')
    def check_cancel():
        if cancelled():raise ValueError('Generalization cancelled; original workspace preserved.')
    for count in SUBSTEP_COUNTS:
        check_cancel();_restore(saved)
        times=[lo+dz*i/count for i in range(count)]+[hi]
        if any(b-a<=TOL for a,b in zip(times,times[1:])):
            recovery['attempts'].append(dict(substeps=count,status='unsupported_interval'))
            break
        fork=copy(session);fork.history=list(session.history[:session.cursor+1]);fork.last_diagnostic=None
        attempt=dict(substeps=count,status='planning',steps=[]);recovery['attempts'].append(attempt)
        affected=set();notes=[];failed=False
        for index,(start,end) in enumerate(zip(times,times[1:]),1):
            check_cancel();current=tolerance*index/count if index<count else tolerance
            target=polygon_target(source,'simplification',current,action['iterations'],action['ratio']) if index<count else goal
            target=_registered_target(fork.state.features[owner].geometry,target)
            step=dict(tolerance=current,z_s=start,z_t=end,status='planning');attempt['steps'].append(step)
            try:
                proposal=fork._preview_single(**{**action,'tolerance':current},dz=end-start,
                                              _simplification_targets={owner:target})
                check_cancel()
            except ValueError as exc:
                step.update(status='failed',failure=deepcopy(getattr(exc,'report',dict(message=str(exc)))))
                attempt['status']='failed'
                if not retryable(exc):
                    recovery['status']='stopped'
                    exc.report={**getattr(exc,'report',{}),'simplification_recovery':[recovery]}
                    raise
                failed=True;break
            step['status']='passed';affected.update(proposal.affected);notes.extend(proposal.notes);fork.apply(proposal)
        if failed:continue
        actual=fork.state.features[owner].geometry
        if actual.hausdorff_distance(goal)>TOL*10 or actual.symmetric_difference(goal).area>max(1e-6,goal.area*1e-9):
            recovery['status']='target_mismatch'
            raise TransitionShellError('Simplification recovery did not reach the requested original-source target.',
                'recovery_target_mismatch',cell=owner,simplification_recovery=[recovery])
        check_cancel();attempt['status']='passed';recovery.update(status='passed',successful_substeps=count)
        record=OperationRecord('simplification',tuple(action['selected']),tuple(sorted(affected)),
            session.history[session.cursor-1].z if session.cursor else min(p[2] for f in base.faces for p in f.points),lo,hi,
            parameters=dict(dz=dz,**{k:action[k] for k in ('tolerance','iterations','ratio')}),
            diagnostics=dict(simplification_recovery=[recovery]),
            actions=(_action_record(action,None,affected,(),lo,hi),))
        state=State(fork.state.features,fork.state.faces,hi,
            f'Simplification {owner}: tolerance {tolerance:g}; {count} recovery substeps; Z={lo:g}–{hi:g}',
            tuple(sorted(affected)),record,parent_step_id=base.step_id)
        notes.append(f'Feature {owner}: simplification recovered with {count} tolerance substeps; requested tolerance {tolerance:g} and final target preserved.')
        session.last_diagnostic=None
        return Proposal(base,state,fork.model,fork.core,{owner:goal},tuple(sorted(affected)),list(dict.fromkeys(notes)))
    recovery['status']='exhausted'
    original.report={**recovery['original_failure'],'simplification_recovery':[recovery]}
    raise original
