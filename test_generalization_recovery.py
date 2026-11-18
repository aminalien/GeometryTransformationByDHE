"""Tolerance continuation, immutable targets, bounded retries and atomicity."""
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely import from_wkt
from shapely.geometry import box

from generalization_model import GeneralizationSession,_core
from generalization_geometry import polygon_target
from shell_conformity import TransitionShellError
from main import validate_model
from test_generalization_workflows import session
from web_generalization_data import DEFAULT_WEB_DATA,read_web_dataset


def reported_data():
    # Feature 3 and its shared-boundary neighbour, with the original local
    # coordinates and a small origin anchor. No external dataset is required.
    source=from_wkt('POLYGON ((71.2191063167993 31.034447700716555, 73.55878845672123 64.01357285492122, '
        '73.64396435860544 64.73918982408941, 83.15165736875497 63.98665567673743, 86.14091133326292 63.74572823010385, '
        '92.71197856322397 63.35798111651093, 92.5610986626707 60.42732186987996, 92.5136445327662 59.60258892271668, '
        '110.4193816682091 58.47896435763687, 109.46638378989883 47.75936464872211, 108.82760121487081 40.65367157384753, '
        '112.48639170848764 40.25303675234318, 112.21567193628289 36.84061534423381, 111.47651305876207 27.517803539521992, '
        '102.06243727798574 28.172702285461128, 102.13453011633828 28.608646978624165, 93.90223636780865 29.318146478384733, '
        '93.8614742856007 28.760656556114554, 91.35160280927084 28.881164270453155, 90.22275387856644 28.939303992316127, '
        '90.28480962733738 29.486248868517578, 80.8677003668854 30.2523780092597, 71.2191063167993 31.034447700716555))')
    neighbor=from_wkt('POLYGON ((72.78706410806626 64.0703568002209, 73.38331164419651 72.73272952996194, '
        '80.71300885931123 72.25444321148098, 86.82900015928317 71.86536849383265, 86.14091133326292 63.74572823010385, '
        '83.15165736875497 63.98665567673743, 73.64396435860544 64.73918982408941, 73.55878845672123 64.01357285492122, '
        '72.78706410806626 64.0703568002209))')
    frame=gpd.GeoDataFrame({'_index':[3,18,99],'osm_id':['reported-3','neighbor-18','origin']},
                          geometry=[source,neighbor,box(0,0,.01,.01)],crs=2180)
    return SimpleNamespace(frame=frame,conflicts=[],rejected=[],source='Simplification regression')


class SimplificationRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(Path(DEFAULT_WEB_DATA).is_file(),'Local reported web dataset unavailable')
    def test_reported_six_feature_web_selection(self):
        s=GeneralizationSession(read_web_dataset(DEFAULT_WEB_DATA));base=s.state;before=_core()
        selected=[3,8,9,11,12,14]
        p=s.preview('simplification',selected,tolerance=1.)
        self.assertEqual(p.state.z,15.);self.assertIs(s.state,base);self.assertEqual(_core(),before)
        self.assertTrue(any(r['selected']==[3] and r['status']=='passed' for r in p.state.operation.diagnostics['simplification_recovery']))
        self.assertEqual(len(s.history),1);validate_model(p.model)

    def test_reported_failure_recovers_in_two_substeps_with_one_history_entry(self):
        s=GeneralizationSession(reported_data());base=s.state;before=_core();source=base.features[3].geometry
        goal=polygon_target(source,'simplification',1.)
        with self.assertRaises(TransitionShellError) as original:s._preview_single('simplification',[3],tolerance=1.)
        self.assertEqual(original.exception.report['classification'],'nonmanifold_edge_incidence')
        p=s.preview('simplification',[3],tolerance=1.)
        recovery=p.state.operation.diagnostics['simplification_recovery'][0]
        self.assertEqual(recovery['successful_substeps'],2)
        self.assertEqual([(r['tolerance'],r['z_s'],r['z_t']) for r in recovery['attempts'][-1]['steps']],[(.5,5.,10.),(1.,10.,15.)])
        self.assertLess(p.state.features[3].geometry.symmetric_difference(goal).area,1e-9)
        self.assertEqual(p.state.operation.parameters['tolerance'],1.)
        self.assertEqual(p.state.operation.parameters['dz'],10.)
        self.assertIsNone(s.last_diagnostic);self.assertIs(s.state,base);self.assertEqual(_core(),before)
        self.assertEqual(len(s.history),1);self.assertEqual(p.state.parent_step_id,base.step_id)
        self.assertNotIn('_simplification_targets',json.dumps(recovery,allow_nan=False))
        validate_model(p.model);s.apply(p);self.assertEqual(len(s.history),2);self.assertEqual(s.cursor,1)
        s.undo();self.assertIs(s.state,base);s.go(1);self.assertEqual(s.state.z,15.)

    def test_successful_direct_simplification_is_unchanged(self):
        s=session([box(0,0,4,4)]);original=GeneralizationSession._preview_single;calls=[]
        def observed(owner,*args,**kwargs):calls.append(kwargs);return original(owner,*args,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',observed):p=s.preview('simplification',[1],tolerance=.1)
        self.assertEqual(len(calls),1);self.assertNotIn('simplification_recovery',p.state.operation.diagnostics)

    def test_cumulative_simplification_cannot_replace_the_frozen_target(self):
        shape=from_wkt('POLYGON ((2.966 0, 4.234 2.445, 2.217 3.839, 0 2.808, -1.231 2.133, -3.218 1.858, '
                       '-3.422 0, -3.122 -1.803, -1.185 -2.052, 0 -3.593, 2.139 -3.705, 2.889 -1.668, 2.966 0))')
        s=session([shape]);source=s.state.features[1].geometry;goal=polygon_target(source,'simplification',1.)
        cumulative=polygon_target(polygon_target(source,'simplification',.5),'simplification',1.)
        self.assertGreater(goal.symmetric_difference(cumulative).area,1.)
        original=GeneralizationSession._preview_single
        def fail_direct(owner,*args,**kwargs):
            if '_simplification_targets' not in kwargs:raise TransitionShellError('Direct test failure','nonmanifold_edge_incidence',cell=1)
            return original(owner,*args,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',fail_direct):p=s.preview('simplification',[1],tolerance=1.)
        self.assertLess(p.state.features[1].geometry.symmetric_difference(goal).area,1e-7)
        self.assertGreater(p.state.features[1].geometry.symmetric_difference(cumulative).area,1.)

    def test_bounded_exhaustion_preserves_original_error_and_workspace(self):
        s=session([box(0,0,4,4)]);base=s.state;before=_core();calls=[]
        def fail(*args,**kwargs):
            calls.append(kwargs);raise TransitionShellError('Original failure','nonmanifold_edge_incidence',cell=1)
        with patch.object(GeneralizationSession,'_preview_single',side_effect=fail):
            with self.assertRaisesRegex(TransitionShellError,'Original failure') as caught:s.preview('simplification',[1],tolerance=1.)
        recovery=caught.exception.report['simplification_recovery'][0]
        self.assertEqual([a['substeps'] for a in recovery['attempts']],[2,4,8,16])
        self.assertEqual(recovery['status'],'exhausted');self.assertEqual(len(calls),5)
        json.dumps(caught.exception.report,allow_nan=False)
        self.assertIs(s.state,base);self.assertEqual(_core(),before);self.assertEqual(len(s.history),1)

    def test_finer_attempt_restarts_from_the_original_source(self):
        s=session([box(0,0,4,4)]);original=GeneralizationSession._preview_single;starts=[]
        def observed(owner,*args,**kwargs):
            starts.append((owner.state.z,kwargs['dz']))
            if kwargs['dz']>2.5:raise TransitionShellError('Need shorter window','unmatched_edges',cell=1)
            return original(owner,*args,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',observed):p=s.preview('simplification',[1],tolerance=1.)
        recovery=p.state.operation.diagnostics['simplification_recovery'][0]
        self.assertEqual(recovery['successful_substeps'],4)
        self.assertEqual(starts[:3],[(5.,10.),(5.,5.),(5.,2.5)])
        self.assertEqual([r['tolerance'] for r in recovery['attempts'][-1]['steps']],[.25,.5,.75,1.])

    def test_invalid_parameters_unsupported_errors_and_other_operations_do_not_retry(self):
        s=session([box(0,0,4,4)])
        for error in (ValueError('Invalid geometry'),RuntimeError('Allocation failure'),
                      TransitionShellError('Invalid interval','unsupported_transition_interval')):
            with patch.object(GeneralizationSession,'_preview_single',side_effect=error) as construct:
                with self.assertRaises(type(error)):s.preview('simplification',[1],tolerance=1.)
                self.assertEqual(construct.call_count,1)
        with self.assertRaisesRegex(ValueError,'positive'):s.preview('simplification',[1],tolerance=0.)
        with patch.object(GeneralizationSession,'_preview_single',side_effect=TransitionShellError('Other operation','unmatched_edges')) as construct:
            with self.assertRaises(TransitionShellError):s.preview('smoothing',[1])
            self.assertEqual(construct.call_count,1)

    def test_cancellation_after_partial_construction_is_atomic(self):
        s=session([box(0,0,4,4)]);base=s.state;before=_core();stop=Event();original=GeneralizationSession._preview_single
        def observed(owner,*args,**kwargs):
            if '_simplification_targets' not in kwargs:raise TransitionShellError('Direct failure','unmatched_edges',cell=1)
            proposal=original(owner,*args,**kwargs);stop.set();return proposal
        with patch.object(GeneralizationSession,'_preview_single',observed):
            with self.assertRaisesRegex(ValueError,'cancelled'):s.preview('simplification',[1],tolerance=1.,cancelled=stop.is_set)
        self.assertIs(s.state,base);self.assertEqual(_core(),before);self.assertEqual(len(s.history),1)

    def test_recovery_stays_inside_an_adjacent_features_assigned_window(self):
        s=session([box(0,0,2,2),box(2,0,6,2),box(20,0,22,2)]);original=GeneralizationSession._preview_single
        def observed(owner,operation,selected,**kwargs):
            if list(selected)==[1] and '_simplification_targets' not in kwargs:
                raise TransitionShellError('First feature failure','nonmanifold_edge_incidence',cell=1)
            return original(owner,operation,selected,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',observed):p=s.preview('simplification',[1,2,3],tolerance=.1)
        recovery=p.state.operation.diagnostics['simplification_recovery'][0]
        self.assertEqual((recovery['z_s'],recovery['z_t']),(5.,10.))
        self.assertEqual([(r['z_s'],r['z_t']) for r in recovery['attempts'][-1]['steps']],[(5.,7.5),(7.5,10.)])
        windows={tuple(r['selected']):(r['z_s'],r['z_t']) for r in p.state.operation.diagnostics['adjacent_schedule']}
        self.assertEqual(windows,{(1,):(5.,10.),(2,):(10.,15.),(3,):(5.,15.)});validate_model(p.model)

    def test_mixed_level_recovery_keeps_other_operation_at_full_interval(self):
        s=GeneralizationSession(reported_data());base=s.state;goal=polygon_target(base.features[3].geometry,'simplification',1.)
        p=s.preview_level([dict(operation='simplification',selected=[3],tolerance=1.),dict(operation='point',selected=[99])])
        self.assertEqual(p.state.operation.name,'batch')
        self.assertEqual(p.state.operation.diagnostics['simplification_recovery'][0]['status'],'passed')
        self.assertEqual(p.state.features[99].geometry.geom_type,'Point')
        self.assertLess(p.state.features[3].geometry.symmetric_difference(goal).area,1e-7)
        self.assertEqual(p.state.z,15.);self.assertIs(s.state,base);validate_model(p.model)

    def test_nonadjacent_batch_recovers_the_failing_feature_and_clears_old_diagnostic(self):
        s=GeneralizationSession(reported_data());p=s.preview('simplification',[3,99],tolerance=1.)
        self.assertIsNone(s.last_diagnostic)
        self.assertEqual(p.state.operation.diagnostics['simplification_recovery'][0]['selected'],[3])
        self.assertEqual(p.state.operation.diagnostics['direct_level_failure']['cell'],3)
        self.assertEqual(len(s.history),1);self.assertEqual(p.state.z,15.)

    def test_nonretryable_failure_after_a_successful_partial_stage_stops_immediately(self):
        s=session([box(0,0,4,4)]);original=GeneralizationSession._preview_single;base=s.state;before=_core();calls=[]
        def observed(owner,*args,**kwargs):
            calls.append(kwargs)
            if '_simplification_targets' not in kwargs:raise TransitionShellError('Direct failure','unmatched_edges',cell=1)
            if owner.state.z>5:raise ValueError('Invalid neighbour update')
            return original(owner,*args,**kwargs)
        with patch.object(GeneralizationSession,'_preview_single',observed):
            with self.assertRaisesRegex(ValueError,'Invalid neighbour') as caught:s.preview('simplification',[1],tolerance=1.)
        self.assertEqual(len(calls),3)
        self.assertEqual(caught.exception.report['simplification_recovery'][0]['status'],'stopped')
        self.assertIs(s.state,base);self.assertEqual(_core(),before)

    def test_automatic_run_reports_recovery_without_an_extra_level(self):
        from automatic_generalization import build_automatic_run,Candidate
        from test_automatic_recovery import candidate
        s=GeneralizationSession(reported_data());base=s.state;before=_core()
        def candidates(state,focus,settings,scale,**kwargs):
            if kwargs.get('final'):return [Candidate(None,state.features[next(iter(focus))].geometry,0.,{},'Retain final polygon')]
            if len(state.features[3].geometry.exterior.coords)>9:return [candidate(state,[3],op='simplification',tolerance=1.)]
            return [candidate(state,[3,18],op='merging')]
        with patch('automatic_generalization.generate_candidates',side_effect=candidates):
            run=build_automatic_run(s,[3,18],dict(final_kind='polygon',max_levels=2))
        self.assertEqual(len(run.states),2);self.assertEqual(len(run.report['steps']),2)
        self.assertEqual(run.report['steps'][0]['simplification_recovery'][0]['successful_substeps'],2)
        self.assertEqual(run.states[0].z,15.);self.assertEqual(run.states[1].z,25.)
        self.assertIs(s.state,base);self.assertEqual(_core(),before);json.dumps(run.report,allow_nan=False)


if __name__=='__main__':unittest.main()
