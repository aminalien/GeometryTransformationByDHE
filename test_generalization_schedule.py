"""Actual DHE sections, ordering, and transactional adjacent subintervals."""
from threading import Event
import unittest
from unittest.mock import patch

from shapely.geometry import box,Polygon
from shapely.affinity import translate

from generalization_model import _core
from generalization_levels import normalize_actions
from generalization_schedule import _priority
from plane_slicing import PlaneSlicer,SlicePlane
from main import validate_model
from test_generalization_workflows import session
from lts_evaluation import evaluate_history


def sections(proposal,z):return PlaneSlicer(proposal.model).sections(SlicePlane.horizontal(z),include_degenerate=True)


class AdjacentScheduleTests(unittest.TestCase):
    def test_pair_half_intervals_recompute_priority_and_keep_one_history_step(self):
        s=session([box(0,0,2,2),box(2,0,6,2)]);base=s.state;core=_core();history=list(s.history)
        p=s.preview('point',[2,1]);schedule=p.state.operation.diagnostics['adjacent_schedule']
        self.assertEqual([(r['selected'],r['z_s'],r['z_t']) for r in schedule],[([1],5.,10.),([2],10.,15.)])
        self.assertGreater(schedule[1]['source_area'],8.)
        self.assertAlmostEqual(schedule[1]['changed_area'],schedule[1]['source_area'])
        middle=sections(p,10);self.assertEqual(middle['1'].geom_type,'Point')
        self.assertGreater(middle['2'].area,8.)
        self.assertNotIn('1',sections(p,12.5));self.assertEqual(sections(p,15)['2'].geom_type,'Point')
        self.assertEqual(p.state.operation.name,'point');self.assertEqual(p.state.parent_step_id,base.step_id)
        self.assertIs(s.state,base);self.assertEqual(s.history,history);self.assertEqual(_core(),core)
        validate_model(p.model);s.apply(p);self.assertEqual(s.cursor,1);self.assertEqual(len(s.history),2)
        s.undo();self.assertIs(s.state,base);s.go(1);self.assertEqual(s.state.z,15.)

    def test_three_feature_chain_uses_thirds_and_deterministic_order(self):
        s=session([box(0,0,2,2),box(2,0,5,2),box(5,0,9,2)])
        a=s.preview('point',[3,1,2]);b=s.preview('point',[2,3,1])
        schedule=a.state.operation.diagnostics['adjacent_schedule']
        self.assertEqual([r['selected'] for r in schedule],[[1],[2],[3]])
        for i,r in enumerate(schedule):
            self.assertAlmostEqual(r['z_s'],5+10*i/3);self.assertAlmostEqual(r['z_t'],5+10*(i+1)/3)
        self.assertEqual(schedule,b.state.operation.diagnostics['adjacent_schedule'])
        self.assertEqual([(f.owner,f.points) for f in a.state.faces],[(f.owner,f.points) for f in b.state.faces])
        validate_model(a.model)

    def test_parallel_pairs_and_chains_keep_their_own_interval_width(self):
        shapes=[box(0,0,2,2),box(2,0,6,2),box(20,0,22,2),box(22,0,25,2),box(25,0,29,2)]
        s=session(shapes);p=s.preview('point',list(s.state.features));schedule=p.state.operation.diagnostics['adjacent_schedule']
        windows={r['selected'][0]:(r['z_s'],r['z_t']) for r in schedule}
        self.assertEqual(windows[1],(5.,10.));self.assertEqual(windows[2],(10.,15.))
        for i,k in enumerate((3,4,5)):
            self.assertAlmostEqual(windows[k][0],5+10*i/3);self.assertAlmostEqual(windows[k][1],5+10*(i+1)/3)
        self.assertEqual(sections(p,10)['1'].geom_type,'Point')
        self.assertEqual(sections(p,10)['4'].geom_type,'Polygon')
        self.assertEqual(set(sections(p,15)),{'2','5'})
        validate_model(p.model)

    def test_corner_contacts_and_separated_features_keep_legacy_timing(self):
        for shapes in ([box(0,0,2,2),box(2,2,4,4)],[box(0,0,2,2),box(10,0,12,2)]):
            s=session(shapes);p=s.preview('point',[1,2])
            self.assertNotIn('adjacent_schedule',p.state.operation.diagnostics)
            self.assertEqual(set(sections(p,15)),{'1','2'})

    def test_priority_counts_added_and_removed_area_instead_of_net_area(self):
        s=session([box(0,0,2,2)]);action=normalize_actions([dict(operation='simplification',selected=[1])],s.state)[0]
        shifted=translate(s.state.features[1].geometry,xoff=1)
        with patch('generalization_schedule.polygon_target',return_value=shifted):
            changed,before,after=_priority(s.state,action)
        self.assertEqual(before,after);self.assertEqual(changed,4.)

    def test_simplification_changes_only_its_active_window_and_freezes_completed_target(self):
        a=Polygon([(0,0),(2,0),(2,2),(1.1,2),(1,2.2),(.9,2),(0,2)])
        b=Polygon([(2,0),(6,0),(6,2),(4.1,2),(4,2.4),(3.9,2),(2,2)])
        s=session([a,b]);p=s.preview('simplification',[1,2],tolerance=.5)
        schedule=p.state.operation.diagnostics['adjacent_schedule']
        self.assertEqual([r['selected'] for r in schedule],[[1],[2]])
        self.assertLess(schedule[0]['changed_area'],schedule[1]['changed_area'])
        self.assertLess(sections(p,7.5)['2'].symmetric_difference(b).area,1e-7)
        self.assertLess(sections(p,12.5)['1'].symmetric_difference(box(0,0,2,2)).area,1e-7)
        self.assertLess(p.state.features[2].geometry.symmetric_difference(box(2,0,6,2)).area,1e-7)

    def test_polyline_collapse_ends_at_its_own_subinterval(self):
        s=session([box(0,0,4,2),box(4,0,10,2)])
        p=s.preview('polyline',[1,2])
        self.assertIn(sections(p,10)['1'].geom_type,('LineString','MultiLineString'))
        self.assertNotIn('1',sections(p,12.5))
        self.assertEqual(p.state.features[1].geometry.geom_type,'LineString')

    def test_action_evaluation_uses_real_subinterval_endpoints(self):
        s=session([box(0,0,2,2),box(2,0,6,2)]);p=s.preview('point',[1,2]);s.apply(p)
        record=evaluate_history(s.history,s.cursor,'last_operation',s.model,dict(source='test',fingerprint='test'),'test')
        rows={r['scope']:r for r in record['tables']['summary'] if r['step']==1}
        self.assertEqual(rows['Feature 1']['action_target_z'],10.)
        self.assertEqual(rows['Feature 1']['geometry_type'],'Point')
        self.assertEqual(rows['Feature 2']['action_source_z'],10.)
        self.assertIn('centroid',rows['Feature 1']['operation_descriptor'].lower())
        self.assertEqual(len({r['t'] for r in record['tables']['sections']}),11)

    def test_automatic_substeps_do_not_consume_extra_levels(self):
        from automatic_generalization import generate_candidates,build_automatic_run
        from test_automatic_recovery import candidate
        a=Polygon([(0,0),(6,0),(6,4),(3.1,4),(3,4.2),(2.9,4),(0,4)])
        b=Polygon([(6,0),(12,0),(12,4),(9.1,4),(9,4.4),(8.9,4),(6,4)])
        s=session([a,b]);base=s.state;core=_core()
        def candidates(state,focus,settings,scale,**kwargs):
            if kwargs.get('final'):return generate_candidates(state,focus,settings,scale,**kwargs)
            if any(len(state.features[k].geometry.exterior.coords)>5 for k in focus):
                return [candidate(state,[k],op='simplification',tolerance=.5) for k in sorted(focus)]
            return [candidate(state,sorted(focus),op='merging')]
        with patch('automatic_generalization.generate_candidates',side_effect=candidates):
            run=build_automatic_run(s,[1,2],dict(final_kind='polygon',max_levels=2))
        self.assertEqual(len(run.states),2);self.assertEqual(len(run.report['steps']),2)
        self.assertEqual(len(run.report['steps'][0]['adjacent_schedule']),2)
        self.assertEqual(run.states[0].z,15.);self.assertEqual(run.states[1].z,25.)
        self.assertIs(s.state,base);self.assertEqual(_core(),core)

    def test_group_merge_is_one_unit_and_zero_change_runs_first(self):
        s=session([box(0,0,2,2),box(2,0,4,2),box(4,0,6,2)])
        p=s.preview_level([dict(operation='merging',selected=[1,2],winner=1),dict(operation='smoothing',selected=[3])])
        schedule=p.state.operation.diagnostics['adjacent_schedule']
        self.assertEqual([(r['selected'],r['z_s'],r['z_t']) for r in schedule],[([1,2],5.,10.),([3],10.,15.)])
        self.assertEqual(schedule[0]['changed_area'],0.)
        self.assertLess(sections(p,10)['1'].symmetric_difference(box(0,0,4,2)).area,1e-7)
        self.assertEqual(p.state.features[1].lineage,(1,2));self.assertNotIn(2,p.state.features)
        validate_model(p.model)

    def test_parallel_aggregates_reserve_distinct_ids_and_preserve_provenance(self):
        shapes=[box(0,0,2,2),box(4,0,6,2),box(6,0,8,2),box(20,0,22,2),box(24,0,26,2),box(26,0,28,2)]
        s=session(shapes,['a','a','a','b','b','b'])
        p=s.preview_level([dict(operation='aggregation',selected=[1,2]),dict(operation='simplification',selected=[3,6],tolerance=.1),
                           dict(operation='aggregation',selected=[4,5])])
        self.assertEqual(set(p.state.features),{3,6,7,8})
        self.assertEqual(p.state.features[7].lineage,(1,2));self.assertEqual(p.state.features[8].lineage,(4,5))
        self.assertEqual(p.state.features[7].attributes['kind'],'a');self.assertEqual(p.state.features[8].attributes['kind'],'b')
        self.assertEqual(len(p.state.features[7].attributes['source_members']),2)
        self.assertEqual(len(p.state.operation.actions),4);validate_model(p.model)

    def test_aggregate_id_order_does_not_follow_component_traversal(self):
        # Components are (first, third action) and (second action), so assigning
        # IDs while walking components would swap the second/third aggregates.
        s=session([box(0,0,2,2),box(4,0,6,2),box(20,0,22,2),box(24,0,26,2),box(6,0,8,2),box(10,0,12,2)])
        actions=[dict(operation='aggregation',selected=[1,2]),dict(operation='aggregation',selected=[3,4]),
                 dict(operation='aggregation',selected=[5,6])]
        p=s.preview_level(actions)
        self.assertEqual({k:f.lineage for k,f in p.state.features.items()},{7:(1,2),8:(3,4),9:(5,6)})
        validate_model(p.model)

    def test_scheduled_shell_failure_keeps_level_scope_and_original_action_context(self):
        from shell_conformity import TransitionShellError
        s=session([box(0,0,2,2),box(2,0,6,2)]);core=_core();base=s.state
        with patch.object(s.__class__,'_preview_single',side_effect=TransitionShellError('joint shell','self_intersection',cell=1)):
            with self.assertRaises(ValueError) as caught:s.preview('point',[1,2])
        report=caught.exception.report
        self.assertEqual(report['failure_scope'],'level');self.assertNotIn('failed_action',report)
        self.assertEqual(report['scheduled_action']['selected'],(1,2))
        self.assertEqual(report['scheduled_unit']['selected'],(1,))
        self.assertEqual((report['z_s'],report['z_t']),(5.,10.))
        self.assertIs(s.state,base);self.assertEqual(_core(),core)

    def test_parallel_components_can_update_separate_edges_of_one_passive_neighbor(self):
        s=session([box(0,0,2,2),box(2,0,4,2),box(10,0,12,2),box(12,0,14,2),box(0,-2,14,0)])
        original=s.state.features[5].geometry
        p=s.preview('point',[1,2,3,4])
        self.assertGreater(p.state.features[5].geometry.area,original.area)
        self.assertLess(sections(p,5)['5'].symmetric_difference(original).area,1e-7)
        validate_model(p.model)

    def test_failure_cancel_and_tiny_intervals_leave_session_unchanged(self):
        s=session([box(0,0,2,2),box(2,0,6,2)]);base=s.state;core=_core();history=list(s.history)
        original=s._preview_single;stop=Event();calls=[]
        def construct(*args,**kwargs):
            p=original(*args,**kwargs);calls.append(p);stop.set();return p
        with patch.object(s.__class__,'_preview_single',side_effect=construct):
            with self.assertRaisesRegex(ValueError,'cancelled'):s.preview('point',[1,2],cancelled=stop.is_set)
        self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(ValueError,'subintervals'):s.preview('point',[1,2],dz=1.5e-7)
        with patch('generalization_schedule.realize',side_effect=RuntimeError('allocation failure')):
            # Multiple components require a final combined realization.
            other=session([box(0,0,2,2),box(2,0,4,2),box(20,0,22,2)])
            other_base=other.state;other_core=_core()
            with self.assertRaisesRegex(RuntimeError,'allocation'):other.preview('point',[1,2,3])
            self.assertIs(other.state,other_base);self.assertEqual(_core(),other_core)
        self.assertIs(s.state,base);self.assertEqual(s.history,history)
        # Restore the saved session's core after constructing the independent fixture.
        from generalization_model import _restore
        _restore(core);self.assertEqual(_core(),core)


if __name__=='__main__':unittest.main()
