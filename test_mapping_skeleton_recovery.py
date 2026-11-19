"""Classified skeleton failures, selective Mapping fallback and original causes."""
import json
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box

from aggregation_geometry import AggregationConstructionError,build_aggregation_preview
from generalization_model import _core
from mapping_builders import MappingBuildError,build_mapping
from mapping_data import prepare_layer,union_overlaps,reproject_layer
from mapping_scene import MappingScene
from shell_conformity import TransitionShellError
from test_mapping import layers,group,build


BACKEND_FAILURE="Antiparallel segments, can't determine if the bisector is inside the left-polygon."
REPORTED_ROOT=Path('C:/University/PhD/Data/Kleczkow Updated')


class SkeletonFailureTests(unittest.TestCase):
    def test_exhaustion_is_a_structured_value_error_with_backend_cause(self):
        failure=ValueError(BACKEND_FAILURE)
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=failure) as skeleton:
            with self.assertRaises(AggregationConstructionError) as caught:
                build_aggregation_preview({'a':box(0,0,2,2),'b':box(4,0,6,2)})
        exc=caught.exception;self.assertIsInstance(exc,ValueError);self.assertIs(exc.__cause__,failure)
        self.assertIn(BACKEND_FAILURE,str(exc));self.assertEqual(skeleton.call_count,6)
        self.assertEqual(exc.report['classification'],'straight_skeleton_aggregation_failure')
        self.assertEqual([a['attempt'] for a in exc.report['attempts']],list(range(1,7)))
        self.assertTrue(all(a['message']==BACKEND_FAILURE and a['exception_type']=='ValueError' for a in exc.report['attempts']))
        json.dumps(exc.report,allow_nan=False)

    def test_invalid_inputs_and_memory_errors_do_not_become_skeleton_failures(self):
        with patch('aggregation_geometry._skeleton_at_margin') as skeleton:
            for sources,tolerance in (({},1e-7),({'a':box(0,0,2,2)},0),
                                      ({'a':box(0,0,2,2),'b':box(1,0,3,2)},1e-7)):
                with self.assertRaises(ValueError) as caught:build_aggregation_preview(sources,tolerance=tolerance)
                self.assertNotIsInstance(caught.exception,AggregationConstructionError)
            skeleton.assert_not_called()
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=MemoryError('Allocation failed')) as skeleton:
            with self.assertRaises(MemoryError):build_aggregation_preview({'a':box(0,0,2,2),'b':box(4,0,6,2)})
            self.assertEqual(skeleton.call_count,1)

    def test_hybrid_retries_only_the_failed_aggregation_group(self):
        gs=[group(['s1','s2'],['t1'],id='broken'),group(['s3'],['t2'],id='stable')]
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=ValueError(BACKEND_FAILURE)) as skeleton:
            r,_=build([box(0,0,2,2),box(4,0,6,2),box(20,0,22,2)],
                      [box(0,0,6,2),box(20,0,22,2)],groups=gs)
        self.assertEqual(skeleton.call_count,6);self.assertEqual(r.report['construction_attempts'],2)
        self.assertEqual([row['builder'] for row in r.report['routing']],['classic','smooth'])
        self.assertNotIn('fallback',r.report['routing'][1]);self.assertEqual(r.report['retry_history'][0]['groups'],['broken'])
        failure=r.report['retry_history'][0]['failures'][0]
        self.assertIn(BACKEND_FAILURE,failure['message']);self.assertEqual(failure['exception_type'],'AggregationConstructionError')
        self.assertEqual({f['feature_id'] for f in failure['features']},{'s1','s2','t1'})
        self.assertEqual(r.report['endpoint_validation'],'passed');json.dumps(r.report,allow_nan=False,default=str)

    def test_timing_failure_uses_group_identities_instead_of_internal_owner_strings(self):
        failure=TransitionShellError('Inconsistent skeleton event','skeleton_time_conflict',owners=['0','1'],stage='growth_vertex_time')
        before=_core()
        with patch('aggregation_layer._plan_upper_shell',side_effect=failure) as plan:
            r,_=build([box(0,0,2,2),box(4,0,6,2)],[box(0,0,6,2)])
        self.assertEqual(plan.call_count,1);self.assertEqual(_core(),before)
        report=r.report['retry_history'][0]['failures'][0]
        self.assertEqual(report['classification'],'skeleton_time_conflict');self.assertEqual(report['owners'],['0','1'])
        self.assertEqual({f['feature_id'] for f in report['features']},{'s1','s2','t1'})
        self.assertEqual(r.report['routing'][0]['builder'],'classic')

    def test_continuous_failure_has_short_headline_and_complete_feature_diagnostics(self):
        sources=[box(i*3,0,i*3+2,2) for i in range(8)];s,t=layers(sources,[box(0,0,23,2)])
        gs=[group(list(s.features),list(t.features))];before=_core()
        with patch('mapping_builders.continuous_polygon_faces',side_effect=ValueError('Continuous surface failure')) as surface:
            with self.assertRaises(MappingBuildError) as caught:build_mapping(s,t,gs,dict(mode='smooth'))
        exc=caught.exception;self.assertEqual(surface.call_count,1);self.assertEqual(_core(),before)
        self.assertIn('g1 (8 sources, 1 target)',str(exc));self.assertIn('Continuous surface failure',str(exc))
        self.assertNotIn('source s8 (original',str(exc));self.assertLess(len(str(exc)),400)
        self.assertEqual(exc.report['cause']['exception_type'],'ValueError')
        self.assertEqual(exc.report['retry_history'],[])
        failure=exc.report['failures'][0]
        self.assertEqual(len(failure['features']),9)
        self.assertTrue(all('original_id' in f for f in failure['features']));json.dumps(exc.report,allow_nan=False)

    def test_plain_planning_errors_keep_the_cause_and_do_not_retry(self):
        s,t=layers([box(0,0,2,2)],[box(0,0,2,2)]);gs=[group(['s1'],['t1'])]
        with patch('mapping_builders.smooth_faces',side_effect=ValueError('Unsupported supplied shape')) as plan:
            with self.assertRaisesRegex(MappingBuildError,'Unsupported supplied shape') as caught:build_mapping(s,t,gs,{})
        self.assertEqual(plan.call_count,1);self.assertEqual(caught.exception.report['retry_history'],[])
        self.assertEqual(caught.exception.report['failures'][0]['classification'],'construction_error')
        self.assertEqual(caught.exception.report['cause']['exception_type'],'ValueError')

    def test_failed_classic_retry_terminates_and_retains_both_causes(self):
        s,t=layers([box(0,0,2,2),box(4,0,6,2)],[box(0,0,6,2)]);gs=[group(['s1','s2'],['t1'])];before=_core()
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=ValueError(BACKEND_FAILURE)) as skeleton, \
             patch('mapping_builders.realize',side_effect=TransitionShellError('Classic shell failed','unmatched_edges',cell=3)) as realize:
            with self.assertRaises(MappingBuildError) as caught:build_mapping(s,t,gs,{})
        self.assertEqual(skeleton.call_count,6);self.assertEqual(realize.call_count,1);self.assertEqual(_core(),before)
        self.assertIn('Classic shell failed',str(caught.exception))
        self.assertIn(BACKEND_FAILURE,caught.exception.report['retry_history'][0]['failures'][0]['message'])

    def test_cancel_before_classic_retry_preserves_core(self):
        s,t=layers([box(0,0,2,2),box(4,0,6,2)],[box(0,0,6,2)]);gs=[group(['s1','s2'],['t1'])]
        stop=Event();before=_core()
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=ValueError(BACKEND_FAILURE)), \
             patch('mapping_builders.realize') as realize:
            with self.assertRaisesRegex(ValueError,'cancelled'):
                build_mapping(s,t,gs,{},cancelled=stop.is_set,
                              progress=lambda message:stop.set() if message.startswith('Retrying g1 through Classic:') else None)
        realize.assert_not_called();self.assertEqual(_core(),before)

    @unittest.skipUnless(all((REPORTED_ROOT/p).is_file() for p in ('osm_dataSimplified3.shp','Regions.shp')),
                         'Local reported Mapping datasets unavailable')
    def test_reported_seventy_source_group_recovers_and_preserves_endpoints(self):
        s=union_overlaps(prepare_layer(gpd.read_file(REPORTED_ROOT/'osm_dataSimplified3.shp'),'source','reported source'))
        t=union_overlaps(reproject_layer(prepare_layer(gpd.read_file(REPORTED_ROOT/'Regions.shp'),'target','reported target'),s.crs))
        ids=('s1 s112 s122 s124 s128 s130 s139 s142 s147 s152 s164 s172 s174 s179 s182 s185 s187 s19 '
             's193 s201 s202 s217 s218 s222 s223 s227 s228 s232 s238 s243 s244 s245 s261 s262 s27 '
             's275 s281 s310 s322 s328 s334 s349 s367 s371 s373 s384 s392 s407 s414 s416 s419 '
             's420 s426 s438 s439 s445 s451 s50 s628 s629 s63 s630 s631 s654 s655 s656 s663 s665 s71 s91').split()
        self.assertEqual(len(ids),70)
        gs=[group(ids,['t25']),group(sorted(set(s.features)-set(ids)),sorted(set(t.features)-{'t25'}),'exclude','other_inputs')]
        before=_core();r=build_mapping(s,t,gs,dict(mode='hybrid',source_z=5.,target_z=15.,geometry_tolerance=.01))
        scene=MappingScene(r,1);scene.validate_endpoints(s,t,gs)
        self.assertEqual(_core(),before);self.assertEqual(len(r.model.cells),71)
        self.assertEqual(r.report['construction_attempts'],2);self.assertEqual(r.report['routing'][0]['builder'],'classic')
        failure=r.report['retry_history'][0]['failures'][0]
        self.assertEqual(failure['classification'],'straight_skeleton_aggregation_failure')
        self.assertEqual(len(failure['features']),71);self.assertEqual(r.report['endpoint_validation'],'passed')


if __name__=='__main__':unittest.main()
