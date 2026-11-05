"""Regress the reported false shell rejection and automatic failure recovery."""
from collections import Counter
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from shapely.geometry import box,Polygon
from shapely.ops import unary_union

from aggregation_geometry import build_aggregation_preview
from aggregation_layer import build_aggregation_model,_plan_upper_shell,validate_aggregation_model
from automatic_generalization import (Candidate,AutomaticRunError,build_automatic_run,
    generate_candidates)
from generalization_geometry import DEFAULT_DATA,load_shapefile
from generalization_model import GeneralizationSession,_core
from main import validate_model
from plane_slicing import PlaneSlicer,SlicePlane
from shell_conformity import TransitionShellError
from test_generalization_workflows import session
from top_face_overlay import cell_volume


def candidate(state,keys,op='aggregation',tolerance=1.):
    source=unary_union([state.features[k].geometry for k in keys])
    action=dict(operation=op,selected=list(keys),winner=keys[0] if op=='merging' else None,
                tolerance=tolerance,iterations=1,ratio=.25)
    return Candidate(action,source.convex_hull,.1,{},'Regression candidate')


class UpperCellTests(unittest.TestCase):
    def test_point_touch_retains_sources_and_validates_real_upper_cell(self):
        # The hole touches the exterior at (3,3). Extruding the union would
        # produce four lower walls at that point, but each source is simple.
        preview=build_aggregation_preview(dict(
            a=Polygon([(0,0),(4,0),(4,1),(1,1),(1,4),(0,4)]),
            b=box(3,1,4,3),c=box(1,3,3,4)))
        self.assertEqual(preview.union.exterior.intersection(preview.union.interiors[0]).geom_type,'Point')
        plans,_,volume=_plan_upper_shell(preview,5,13,15)
        self.assertFalse(any(p.part=='base_wall' for p in plans))
        self.assertTrue(all(p[2]==5 for f in plans if f.part=='bottom_cap' for p in f.points))
        model=build_aggregation_model(preview,source_z=5,merge_z=13,top_z=15)
        validate_aggregation_model(model)
        self.assertEqual(len(model.cells),4)
        self.assertTrue(all(cell_volume(c)>0 for c in model.cells))
        self.assertAlmostEqual(cell_volume(model.aggregation.cell),volume)
        self.assertAlmostEqual(sum(cell_volume(c) for c in model.cells),model.aggregation.expected_volume)
        slicer=PlaneSlicer(model)
        for z,expected in ((2.5,preview.union),(5,preview.union),(13,preview.hull),(15,preview.hull)):
            section=unary_union(list(slicer.sections(SlicePlane.horizontal(z)).values()))
            self.assertLess(section.symmetric_difference(expected).area,1e-8)

    def test_corrupt_upper_face_is_still_rejected(self):
        preview=build_aggregation_preview({'a':box(0,0,2,2),'b':box(6,0,8,2)})
        from aggregation_layer import _check_shell
        def missing_cap(plans,**kwargs):
            return _check_shell([p for p in plans if p.part!='upper_cap'],**kwargs)
        with patch('aggregation_layer._check_shell',side_effect=missing_cap):
            with self.assertRaises(TransitionShellError) as caught:_plan_upper_shell(preview,5,13,15)
        self.assertEqual(caught.exception.report['classification'],'unmatched_edges')


class RetryTests(unittest.TestCase):
    def grouped_candidates(self,state,focus,settings,scale,**kwargs):
        if kwargs.get('final'):return generate_candidates(state,focus,settings,scale,**kwargs)
        if len(focus)==4:return [candidate(state,[1,2]),candidate(state,[3,4])]
        return [candidate(state,sorted(focus))]

    def test_local_failure_preserves_good_first_action_and_nested_evidence(self):
        s=session([box(0,0,2,2),box(4,0,6,2),box(20,0,22,2),box(24,0,26,2)])
        bad_calls=[]
        def observed(sources,**kwargs):
            if set(sources)=={'3','4'}:
                bad_calls.append(1)
                raise TransitionShellError('Known local failure','nonmanifold_edge_incidence',
                    stage='growth_boundary',cell='aggregate',edges=[dict(incidence=4)])
            return build_aggregation_preview(sources,**kwargs)
        with patch('automatic_generalization.generate_candidates',side_effect=self.grouped_candidates), \
             patch('aggregation_geometry.build_aggregation_preview',side_effect=observed):
            run=build_automatic_run(s,[1,2,3,4],dict(final_kind='polygon'))
        self.assertEqual(bad_calls,[1])
        self.assertEqual(run.report['steps'][0]['actions'][0]['selected'],[1,2])
        evidence=run.report['rejections'][0]['diagnostic']
        self.assertEqual(evidence['failed_action']['selected'],(3,4))
        self.assertEqual(evidence['failure_scope'],'action')
        self.assertEqual(evidence['stage'],'growth_boundary')
        self.assertEqual(evidence['edges'],[dict(incidence=4)])
        self.assertEqual(evidence['cell'],'aggregate')
        json.dumps(run.report,allow_nan=False)

    def test_target_overlap_is_filtered_before_construction(self):
        s=session([box(0,0,2,2),box(10,10,12,12),box(0,10,2,12),box(10,0,12,2)])
        original=GeneralizationSession.preview_level;calls=[]
        def observed(owner,actions,**kwargs):
            calls.append(actions)
            return original(owner,actions,**kwargs)
        with patch('automatic_generalization.generate_candidates',side_effect=self.grouped_candidates), \
             patch.object(GeneralizationSession,'preview_level',observed):
            run=build_automatic_run(s,[1,2,3,4],dict(final_kind='polygon'))
        self.assertEqual(len(calls[0]),1)
        self.assertFalse(run.report['rejections'])
        self.assertEqual(len(run.states[-1].features),1)

    def test_transition_conflict_excludes_the_pair_without_blacklisting_actions(self):
        s=session([box(0,0,2,2),box(4,0,6,2),box(20,0,22,2),box(24,0,26,2)])
        original=GeneralizationSession.preview_level;calls=[]
        def observed(owner,actions,**kwargs):
            calls.append(len(actions))
            if len(actions)>1:
                raise TransitionShellError('Known transition conflict','level_transition_overlap',
                    stage='joint_transitions',conflicting_actions=actions[:2])
            return original(owner,actions,**kwargs)
        with patch('automatic_generalization.generate_candidates',side_effect=self.grouped_candidates), \
             patch.object(GeneralizationSession,'preview_level',observed):
            run=build_automatic_run(s,[1,2,3,4],dict(final_kind='polygon'))
        self.assertEqual(calls[:2],[2,1])
        self.assertEqual(len(run.states[-1].features),1)
        self.assertEqual(run.report['rejections'][0]['diagnostic']['stage'],'joint_transitions')

    def test_new_private_state_retries_a_previously_failed_action(self):
        s=session([box(0,0,2,2),box(2,0,4,2),box(20,0,22,2),box(24,0,26,2)])
        bad_calls=[]
        def choices(state,focus,settings,scale,**kwargs):
            if kwargs.get('final'):return generate_candidates(state,focus,settings,scale,**kwargs)
            if len(focus)==4:return [candidate(state,[1,2],'merging'),candidate(state,[3,4])]
            return [candidate(state,[3,4] if len(focus)==3 else sorted(focus))]
        def observed(sources,**kwargs):
            if set(sources)=={'3','4'}:
                bad_calls.append(1)
                if len(bad_calls)==1:raise ValueError('Transient local construction failure')
            return build_aggregation_preview(sources,**kwargs)
        with patch('automatic_generalization.generate_candidates',side_effect=choices), \
             patch('aggregation_geometry.build_aggregation_preview',side_effect=observed):
            run=build_automatic_run(s,[1,2,3,4],dict(final_kind='polygon'))
        self.assertEqual(len(bad_calls),2)
        self.assertEqual(len(run.states[-1].features),1)

    def test_identical_failure_is_cached_across_scales_and_remains_atomic(self):
        s=session([box(0,0,2,2),box(2,0,4,2)]);base=s.state;core=_core();history=list(s.history)
        error=TransitionShellError('Known joint failure','nonmanifold_edge_incidence',
                                   stage='realization',edges=[dict(incidence=4)])
        with patch.object(GeneralizationSession,'preview_level',side_effect=error) as preview:
            with self.assertRaises(AutomaticRunError) as caught:
                build_automatic_run(s,[1,2],dict(max_scale_advances=3))
        self.assertEqual(preview.call_count,1)
        report=caught.exception.report['automatic']
        self.assertEqual(report['status'],'failed');self.assertEqual(report['constructed_private_levels'],0)
        self.assertEqual(report['cached_constructions_skipped'],3)
        self.assertIn('Known joint failure',str(caught.exception))
        self.assertEqual(report['failure_summary'][0]['classification'],'nonmanifold_edge_incidence')
        self.assertEqual(report['rejections'][0]['diagnostic']['edges'],[dict(incidence=4)])
        self.assertIs(s.state,base);self.assertEqual(s.history,history);self.assertEqual(_core(),core)
        json.dumps(caught.exception.report,allow_nan=False)

    def test_changed_simplification_tolerance_is_retried(self):
        s=session([box(0,0,2,2),box(20,0,22,2)])
        def choices(state,focus,settings,scale,**kwargs):
            return [candidate(state,[1],'simplification',scale/10000)]
        with patch('automatic_generalization.generate_candidates',side_effect=choices), \
             patch.object(GeneralizationSession,'preview_level',side_effect=ValueError('Cannot realize')) as preview:
            with self.assertRaises(AutomaticRunError):build_automatic_run(s,[1,2],dict(max_scale_advances=3))
        self.assertEqual([c.args[0][0]['tolerance'] for c in preview.call_args_list],[1.,2.,4.,8.])

    def test_unattributed_batch_failure_uses_smaller_prefix_within_budget(self):
        s=session([box(0,0,2,2),box(2,0,4,2),box(20,0,22,2),box(22,0,24,2),box(40,0,42,2),box(42,0,44,2)])
        original=GeneralizationSession.preview_level;calls=[]
        def choices(state,focus,settings,scale,**kwargs):
            if kwargs.get('final'):return generate_candidates(state,focus,settings,scale,**kwargs)
            pairs=[p for p in ([1,2],[3,4],[5,6]) if set(p)<=focus]
            return [candidate(state,p,'merging') for p in pairs] if pairs else [candidate(state,sorted(focus))]
        def observed(owner,actions,**kwargs):
            calls.append((owner.state.step_id,len(actions)))
            if len(actions)>1:raise ValueError('Unattributed joint failure')
            return original(owner,actions,**kwargs)
        with patch('automatic_generalization.generate_candidates',side_effect=choices), \
             patch.object(GeneralizationSession,'preview_level',observed):
            run=build_automatic_run(s,list(s.state.features),dict(final_kind='polygon',max_attempts=2))
        self.assertEqual([n for _,n in calls[:2]],[3,1])
        self.assertTrue(all(n<=2 for n in Counter(k for k,_ in calls).values()))
        self.assertEqual(len(run.states[-1].features),1)


@unittest.skipUnless(Path(DEFAULT_DATA).is_file(),'Local reported dataset unavailable')
class ReportedDatasetTests(unittest.TestCase):
    def test_reported_second_level_and_default_automatic_run(self):
        s=GeneralizationSession(load_shapefile());base=s.state
        self.assertEqual(len(base.features),32)
        self.assertAlmostEqual(base.features[1].geometry.area,219.10523196679515)
        ops=[('merging',[3,18],3),('aggregation',[8,11,12,14],None),('aggregation',[24,31],None),
             ('aggregation',[17,30],None),('aggregation',[1,32],None),('merging',[15,20],15),
             ('merging',[2,5],5),('merging',[7,10],7),('simplification',[25],None),
             ('simplification',[16],None),('simplification',[4],None),('smoothing',[21],None)]
        first=s.preview_level([dict(operation=o,selected=k,winner=w,tolerance=1) for o,k,w in ops])
        s.apply(first)
        self.assertEqual(len(s.state.features),22)
        second=s.preview('aggregation',sorted(s.state.features));validate_model(second.model)
        self.assertEqual(len(second.state.features),1)
        s.go(0)
        core=_core()
        run=build_automatic_run(s,sorted(base.features))
        self.assertIs(s.state,base);self.assertEqual(_core(),core)
        self.assertEqual(run.report['status'],'complete')
        self.assertEqual(len(run.states[-1].features),1)
        self.assertEqual(next(iter(run.states[-1].features.values())).lineage,tuple(range(1,33)))
        validate_model(run.model)
        json.dumps(run.report,allow_nan=False)


if __name__=='__main__':unittest.main()
