"""Per-group full-interval winners, failure fallback, and atomic composition."""
import json
from threading import Event
import unittest
from unittest.mock import patch

from shapely.geometry import box

from generalization_model import GeneralizationSession,_core
from shell_conformity import TransitionShellError
from main import validate_model
from test_generalization_workflows import session


def chain(areas):
    polygons=[];x=0.
    for area in areas:
        polygons.append(box(x,0,x+area/2,2));x+=area/2
    return polygons


class PrioritySelectionTests(unittest.TestCase):
    def preview(self,polygons,selected=None,**kwargs):
        s=session(polygons);base=s.state;core=_core()
        p=s.preview('point',selected or list(base.features),consider_substeps=False,**kwargs)
        self.assertIs(s.state,base);self.assertEqual(_core(),core);self.assertEqual(len(s.history),1)
        self.assertIsNone(s.last_diagnostic);self.assertEqual(p.state.z,15.)
        self.assertEqual({(r['z_s'],r['z_t']) for r in p.state.operation.actions},{(5.,15.)})
        self.assertNotIn('adjacent_schedule',p.state.operation.diagnostics)
        json.dumps(p.state.operation.diagnostics,allow_nan=False);validate_model(p.model)
        return s,p,p.state.operation.diagnostics['priority_selection']

    def test_shared_middle_winner_precedes_larger_endpoint(self):
        s,p,choice=self.preview(chain([10,5,1]))
        self.assertEqual(choice['selected'],[2])
        self.assertEqual([c['shared_winner'] for c in choice['groups'][0]['candidates']],[False,True,False])
        self.assertEqual({tuple(d['selected']) for d in choice['deferred']},{(1,),(3,)})
        self.assertEqual([p.state.features[k].geometry.geom_type for k in (1,2,3)],['Polygon','Point','Polygon'])
        self.assertGreater(p.state.features[1].geometry.area,s.state.features[1].geometry.area)

    def test_endpoints_run_together_when_middle_loses_to_both(self):
        _,p,choice=self.preview(chain([10,2,6]))
        self.assertEqual(choice['selected'],[1,3])
        self.assertEqual([p.state.features[k].geometry.geom_type for k in (1,2,3)],['Point','Polygon','Point'])

    def test_independent_groups_and_isolated_feature_all_run(self):
        polygons=[*chain([4,8]),box(20,0,25,2),box(25,0,27,2),box(40,0,42,2)]
        _,p,choice=self.preview(polygons)
        self.assertEqual(choice['selected'],[2,3,5]);self.assertEqual(len(choice['groups']),3)
        self.assertEqual(set(p.state.operation.selected),{2,3,5})
        self.assertEqual([p.state.features[k].geometry.geom_type for k in (1,2,3,4,5)],
                         ['Polygon','Point','Point','Polygon','Point'])

    def test_shared_winners_rank_by_area_in_larger_chain(self):
        _,_,choice=self.preview(chain([10,5,4,1]))
        self.assertEqual(choice['selected'],[2,4])
        entries=choice['groups'][0]['candidates']
        self.assertTrue(entries[1]['shared_winner']);self.assertTrue(entries[2]['shared_winner'])
        self.assertLess(entries[1]['rank'],entries[2]['rank'])

    def test_branched_shared_winner_blocks_all_its_neighbours(self):
        # Centre beats the small upper arm, but not the larger left arm.
        _,_,choice=self.preview([box(0,0,2,2),box(-3,0,0,2),box(2,0,3,2),box(0,2,2,3)])
        self.assertEqual(choice['selected'],[1])
        self.assertEqual(len(choice['deferred']),3)

    def test_shared_winners_use_area_rather_than_number_of_neighbours(self):
        _,_,choice=self.preview([box(0,0,2,2),box(2,0,5,2),box(-1,0,0,1),box(0,2,1,3),box(5,0,6,1)])
        entries=choice['groups'][0]['candidates']
        self.assertTrue(entries[0]['shared_winner']);self.assertTrue(entries[1]['shared_winner'])
        self.assertEqual(len(entries[0]['neighbours']),3);self.assertEqual(len(entries[1]['neighbours']),2)
        self.assertLess(entries[1]['rank'],entries[0]['rank'])
        self.assertEqual(choice['selected'],[2,3,4])

    def test_ties_use_feature_ids_independent_of_selection_order(self):
        s=session(chain([4,4]));a=s.preview('point',[2,1],consider_substeps=False)
        b=s.preview('point',[1,2],consider_substeps=False)
        self.assertEqual(a.state.operation.diagnostics['priority_selection'],b.state.operation.diagnostics['priority_selection'])
        self.assertEqual(a.state.operation.selected,(1,))

    def test_failed_shared_winner_does_not_block_endpoints(self):
        s=session(chain([10,5,1]));base=s.state;core=_core();original=GeneralizationSession._preview_single
        def build(owner,operation,selected,**kwargs):
            if tuple(selected)==(2,):raise TransitionShellError('Middle failed','self_intersection',cell=2)
            return original(owner,operation,selected,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',build):
            p=s.preview('point',[1,2,3],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(1,3))
        self.assertEqual(p.state.operation.diagnostics['feature_errors'][0]['selected'],[2])
        self.assertEqual(p.state.operation.diagnostics['feature_errors'][0]['phase'],'construction')
        self.assertEqual(p.state.features[2].geometry.geom_type,'Polygon')
        self.assertIs(s.state,base);self.assertEqual(_core(),core);validate_model(p.model)

    def test_failed_pair_winner_tries_next_and_other_groups_continue(self):
        s=session([*chain([4,8]),box(20,0,22,2)]);original=GeneralizationSession._preview_single
        def build(owner,operation,selected,**kwargs):
            if tuple(selected)==(2,):raise ValueError('Bad feature')
            return original(owner,operation,selected,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',build):
            p=s.preview('point',[1,2,3],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(1,3));self.assertIsNone(s.last_diagnostic)
        self.assertEqual([e['selected'] for e in p.state.operation.diagnostics['feature_errors']],[[2]])
        validate_model(p.model)

    def test_entire_failed_group_does_not_discard_another_group(self):
        s=session([*chain([4,8]),box(20,0,22,2)]);original=GeneralizationSession._preview_single
        def build(owner,operation,selected,**kwargs):
            if tuple(selected) in ((1,),(2,)):raise ValueError('Failed group member')
            return original(owner,operation,selected,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',build):
            p=s.preview('point',[1,2,3],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(3,))
        self.assertEqual([e['selected'] for e in p.state.operation.diagnostics['feature_errors']],[[2],[1]])
        self.assertEqual(p.state.operation.diagnostics['priority_selection']['groups'][0]['selected'],[])
        validate_model(p.model)

    def test_composition_failure_keeps_accepted_plans_and_tries_next_candidate(self):
        import generalization_schedule as scheduling
        s=session([box(0,0,2,2),box(20,0,22,2),box(22,0,26,2)])
        original=scheduling._compose_plans;core=_core();base=s.state
        def compose(owner,plans,selected,*args):
            if 3 in selected:raise TransitionShellError('Candidate conflicts with accepted plan','level_transition_overlap')
            return original(owner,plans,selected,*args)
        with patch.object(scheduling,'_compose_plans',compose):
            p=s.preview('point',[1,2,3],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(1,2))
        self.assertEqual(p.state.operation.diagnostics['feature_errors'][0]['phase'],'composition')
        self.assertIs(s.state,base);self.assertEqual(_core(),core);validate_model(p.model)

    def test_priority_failure_is_reported_without_discarding_other_features(self):
        import generalization_schedule as scheduling
        s=session(chain([4,8]));original=scheduling._priority
        def score(state,action):
            if action['selected']==(2,):raise ValueError('Cannot construct target')
            return original(state,action)
        with patch.object(scheduling,'_priority',score):p=s.preview('point',[1,2],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(1,))
        self.assertEqual(p.state.operation.diagnostics['feature_errors'][0]['phase'],'priority')

    def test_all_failures_and_cancellation_preserve_history_and_core(self):
        s=session(chain([4,8]));base=s.state;core=_core();stop=Event();original=GeneralizationSession._preview_single
        with patch.object(GeneralizationSession,'_preview_single',side_effect=ValueError('Failed feature')):
            with self.assertRaises(ValueError) as caught:s.preview('point',[1,2],consider_substeps=False)
        self.assertEqual(caught.exception.report['classification'],'all_features_failed')
        self.assertEqual(len(caught.exception.report['feature_errors']),2)
        def build(owner,*args,**kwargs):
            p=original(owner,*args,**kwargs);stop.set();return p
        with patch.object(GeneralizationSession,'_preview_single',build):
            with self.assertRaisesRegex(ValueError,'cancelled'):
                s.preview('point',[1,2],consider_substeps=False,cancelled=stop.is_set)
        self.assertIs(s.state,base);self.assertEqual(_core(),core);self.assertEqual(len(s.history),1)

    def test_one_history_entry_and_undo_for_multiple_winners(self):
        s,p,_=self.preview(chain([10,2,6]));base=s.state
        s.apply(p);self.assertEqual(len(s.history),2);self.assertEqual(s.cursor,1)
        s.undo();self.assertIs(s.state,base);s.go(1);self.assertEqual(s.state.z,15.)

    def test_simplification_recovery_remains_available_with_substeps_off(self):
        from generalization_geometry import polygon_target
        from test_generalization_recovery import reported_data
        s=GeneralizationSession(reported_data());base=s.state;core=_core()
        goal=polygon_target(base.features[3].geometry,'simplification',1.)
        p=s.preview('simplification',[3,99],tolerance=1.,consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(3,99))
        recovery=p.state.operation.diagnostics['simplification_recovery'][0]
        self.assertEqual(recovery['selected'],[3]);self.assertEqual(recovery['successful_substeps'],2)
        self.assertEqual((recovery['z_s'],recovery['z_t']),(5.,15.))
        self.assertLess(p.state.features[3].geometry.symmetric_difference(goal).area,1e-9)
        self.assertIs(s.state,base);self.assertEqual(_core(),core);self.assertEqual(len(s.history),1)
        json.dumps(p.state.operation.diagnostics,allow_nan=False);validate_model(p.model)

    def test_merging_and_aggregation_remain_indivisible_units(self):
        s=session([*chain([4,4]),box(20,0,22,2),box(24,0,26,2),box(40,0,42,2)])
        p=s.preview_level([dict(operation='merging',selected=[1,2],winner=1),
                           dict(operation='aggregation',selected=[3,4]),dict(operation='point',selected=[5])],consider_substeps=False)
        self.assertEqual(p.state.operation.selected,(1,2,3,4,5))
        self.assertEqual({r['selected'] for r in p.state.operation.actions},{(1,2),(3,4),(5,)})
        self.assertEqual(p.state.features[1].lineage,(1,2));self.assertEqual(p.state.features[6].lineage,(3,4))
        validate_model(p.model)


if __name__=='__main__':unittest.main()
