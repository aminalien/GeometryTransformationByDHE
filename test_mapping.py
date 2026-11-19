"""Independent-layer correspondence, DHE solids/primitives and atomic web jobs."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np

import geopandas as gpd
from shapely.geometry import box,Polygon,MultiPolygon,Point,LineString
from shapely.affinity import rotate,translate,scale
from shapely.ops import unary_union

import DHECore as dhe
from generalization_model import _core,_restore,Face,_node_faces,_prism,realize
from mapping_data import (prepare_layer,reproject_layer,overlap_review,resolve_overlap,
                          suggest_groups,validate_groups,route_group,union_overlaps,automatic_review,
                          id_groups,matching_id,validate_id_groups)
from mapping_builders import build_mapping,MappingBuildError,_partition_plane,smooth_faces,centroid_faces
from shell_conformity import TransitionShellError,check_face_intersections
from mapping_scene import MappingScene
from plane_slicing import SlicePlane
from web_generalization_app import Workspace,create_app
from fastapi.testclient import TestClient


def layers(ss,ts):
    s=prepare_layer(gpd.GeoDataFrame({'osm_id':[str(i) for i in range(len(ss))]},geometry=ss,crs=2180),'source','source')
    t=prepare_layer(gpd.GeoDataFrame({'gml_id':[str(i) for i in range(len(ts))]},geometry=ts,crs=2180),'target','target')
    return s,t


def group(ss,ts,decision='map',id='g1'):
    return dict(id=id,sources=ss,targets=ts,decision=decision,confirmed=True,anchor=None)


def build(ss,ts,mode='hybrid',groups=None,**settings):
    s,t=layers(ss,ts)
    if groups is None:groups=[group(list(s.features),list(t.features))]
    before=_core();result=build_mapping(s,t,groups,dict(mode=mode,**settings))
    assert _core()==before
    snapshot=MappingScene(result,1);snapshot.validate_endpoints(s,t,groups)
    return result,snapshot


def column_incidence_pair():
    """Uploaded g452, in its original local coordinates to retain the roundoff case."""
    source=Polygon([(2363.7959625789663,372.2462239218876),(2395.423565129633,385.8105986500159),
                    (2397.237442058744,381.63545775972307),(2402.938486811996,368.33560357708484),
                    (2388.935286090942,362.38957109488547),(2394.1817785343155,350.1585506685078),
                    (2394.728644794901,348.87590237054974),(2404.423820657481,326.2593891294673),
                    (2387.0544989314512,318.5006736610085),(2377.156339039444,341.37846569437534),
                    (2376.701194608584,342.4250780399889),(2375.230645991338,345.8235570276156),
                    (2371.3926687092753,354.69113831501454)])
    target=Polygon([(2363.7959625789663,372.2462239218876),(2395.423565129633,385.8105986500159),
                    (2402.938486811996,368.33560357708484),(2388.935286090942,362.38957109488547),
                    (2404.423820657481,326.2593891294673),(2387.0544989314512,318.5006736610085)])
    return source,target


class MappingDataTests(unittest.TestCase):
    def test_automatic_union_chains_containment_duplicates_and_provenance(self):
        gs=[box(0,0,4,4),box(3,0,7,4),box(6,0,10,4),box(10,0,12,4),box(1,1,2,2),box(3,0,7,4)]
        source,_=layers(gs,[box(0,0,12,4)]);saved=[g.wkb for g in gs]
        result=union_overlaps(source)
        self.assertEqual(list(result.features),['s1','s4']);self.assertFalse(overlap_review(result))
        f=result.features['s1'];self.assertEqual(f.geometry.area,40)
        self.assertEqual(set(f.members),{'s1','s2','s3','s5','s6'})
        self.assertEqual({r['original_id'] for r in f.member_records},{'0','1','2','4','5'})
        self.assertTrue(all(r['attributes']['osm_id']==r['original_id'] for r in f.member_records))
        self.assertTrue(result.notes[-1]['automatic']);self.assertIs(union_overlaps(result),result)
        self.assertEqual([g.wkb for g in gs],saved);self.assertEqual(len(source.features),6)
        bigger,_=layers([box(0,0,1,1),box(.5,0,4,3)],[box(0,0,4,3)])
        self.assertEqual(list(union_overlaps(bigger).features),['s2'])

    def test_automatic_review_accepts_strong_links_not_proximity_and_preserves_edits(self):
        s,t=layers([box(0,0,2,2),box(6,0,8,2)],[box(.2,0,2.2,2),box(9,0,11,2)])
        gs,info=suggest_groups(s,t);accepted=automatic_review(gs,s,t)
        self.assertEqual([g['decision'] for g in accepted],['map','disappearance','appearance'])
        self.assertTrue(all(g['confirmed'] for g in accepted));self.assertTrue(info['proximity_candidates'])
        self.assertFalse(gs[0]['confirmed'])
        accepted[0].update(id='custom',anchor=1,confirmed=False);accepted[-1]['decision']='exclude'
        edited=automatic_review(accepted,s,t)
        self.assertEqual(edited[0]['id'],'custom');self.assertEqual(edited[0]['anchor'],1)
        self.assertEqual(edited[-1]['decision'],'exclude')

    def test_per_feature_citygml_repair_preserves_ids_and_cross_feature_overlap(self):
        raw=gpd.GeoDataFrame({'gml_id':['a','b']},geometry=[MultiPolygon([box(0,0,2,2),box(0,0,2,2)]),box(1,0,3,2)],crs=2180)
        saved=[g.wkb for g in raw.geometry];layer=prepare_layer(raw,'target','test')
        self.assertEqual(len(layer.features),2);self.assertEqual(layer.features['t1'].original_id,'a')
        self.assertEqual(layer.features['t1'].geometry.area,4)
        self.assertEqual(len(layer.notes),1);self.assertEqual(len(overlap_review(layer)),1)
        self.assertEqual([g.wkb for g in raw.geometry],saved)

    def test_explicit_overlap_resolutions_and_crs(self):
        s,t=layers([box(0,0,2,2),box(1,0,3,2)],[box(0,0,3,2)])
        assigned=resolve_overlap(s,['s1','s2'],'assign','s1');self.assertFalse(overlap_review(assigned))
        self.assertEqual(assigned.features['s2'].geometry.area,2)
        merged=resolve_overlap(s,['s1','s2'],'union','s2');self.assertEqual(len(merged.features),1)
        self.assertEqual(merged.features['s2'].members,('s1','s2'))
        self.assertEqual(len(resolve_overlap(s,['s2'],'exclude').features),1)
        self.assertEqual(len(s.features),2)
        actual=prepare_layer(gpd.GeoDataFrame(geometry=[box(360000,364000,360010,364010)],crs=2180).to_crs(4326),'target','geo')
        back=reproject_layer(actual,s.crs)
        self.assertLess(back.features['t1'].geometry.hausdorff_distance(box(360000,364000,360010,364010)),1e-6)
        raw=gpd.GeoDataFrame(geometry=[box(360000,364000,360010,364010)],crs=3857)
        overridden=prepare_layer(raw,'source','wrong declaration',override_crs='EPSG:2180')
        self.assertEqual(overridden.crs.to_epsg(),2180);self.assertTrue(overridden.notes[0]['explicit_review'])
        self.assertEqual(raw.crs.to_epsg(),3857)

    def test_suggestions_keep_unmatched_proximity_as_review_and_validate_coverage(self):
        s,t=layers([box(0,0,2,2),box(6,0,8,2)],[box(.2,0,2.2,2),box(9,0,11,2)])
        groups,info=suggest_groups(s,t)
        self.assertEqual(len(groups),3);self.assertTrue(info['proximity_candidates'])
        self.assertTrue(any(g['decision']=='unresolved' for g in groups))
        validate_groups(groups,s,t,require_confirmed=False)
        with self.assertRaises(ValueError):validate_groups(groups,s,t)
        for g in groups:
            g['confirmed']=True
            if not g['targets']:g['decision']='disappearance'
            if not g['sources']:g['decision']='appearance'
        validate_groups(groups,s,t)
        with self.assertRaises(ValueError):validate_groups(groups[:-1],s,t)
        with self.assertRaises(ValueError):validate_groups([*groups,groups[0]],s,t)

    def test_hybrid_routing_cardinality_and_adjacency(self):
        s,t=layers([box(0,0,2,2),box(2,0,4,2),box(8,0,10,2)],[box(0,0,10,2),box(20,0,22,2)])
        cases=[(group(['s1','s2'],['t1']),'classic'),(group(['s1','s3'],['t1']),'smooth'),
               (group(['s1'],['t1','t2']),'classic'),(group(['s1','s2'],['t1','t2']),'classic'),
               (group(['s1'],['t1']),'smooth'),(group([],['t2'],'appearance'),'smooth')]
        for g,expected in cases:self.assertEqual(route_group(g,s,t,'hybrid')[0],expected)
        self.assertEqual(route_group(cases[2][0],s,t,'smooth')[0],'unsupported')


class MappingIdTests(unittest.TestCase):
    def identified(self,ss,ts):
        result=[]
        for side,records in (('source',ss),('target',ts)):
            result.append(prepare_layer(gpd.GeoDataFrame({'osm_id' if side=='source' else 'gml_id':[k for k,g in records]},
                geometry=[g for k,g in records],crs=2180),side,side))
        return result

    def accepted(self,s,t):
        gs,info=id_groups(s,t)
        self.assertEqual(info,dict(edges=[],proximity_candidates=[]))
        return automatic_review(gs,s,t)

    def test_exact_ids_ignore_location_and_batch_duplicate_ids(self):
        s,t=self.identified([('A',box(0,0,2,2)),('B',box(10,0,12,2)),('A',box(20,0,22,2))],
                            [('B',box(0,0,2,2)),('A',box(100,0,102,2)),('A',box(110,0,112,2))])
        gs=self.accepted(s,t)
        self.assertEqual([(g['sources'],g['targets']) for g in gs],[(['s1','s3'],['t2','t3']),(['s2'],['t1'])])
        validate_id_groups(gs,s,t)
        spatial,_=suggest_groups(s,t)
        self.assertNotEqual([(g['sources'],g['targets']) for g in spatial],[(g['sources'],g['targets']) for g in gs])
        before=_core();r=build_mapping(s,t,gs,dict(mode='classic',match_by_id=True))
        self.assertEqual(_core(),before)
        snapshot=MappingScene(r,1);snapshot.validate_endpoints(s,t,gs)
        self.assertTrue(r.report['settings']['match_by_id']);self.assertEqual(len(r.report['groups']),2)

    def test_missing_blank_case_and_leading_zeros_do_not_create_false_matches(self):
        ids=[None,'','  ','001','1','A','a',' A',0]
        s,t=self.identified([(k,box(i*10,0,i*10+2,2)) for i,k in enumerate(ids)],
                            [(k,box(i*10,0,i*10+2,2)) for i,k in enumerate(ids)])
        gs=self.accepted(s,t)
        self.assertEqual(sum(g['decision']=='map' for g in gs),6)
        for i in range(1,4):
            self.assertIsNone(matching_id(s.features[f's{i}']))
            self.assertTrue(any(g['sources']==[f's{i}'] and not g['targets'] for g in gs))
            self.assertTrue(any(g['targets']==[f't{i}'] and not g['sources'] for g in gs))
        for i in range(4,10):self.assertTrue(any(g['sources']==[f's{i}'] and g['targets']==[f't{i}'] for g in gs))
        fallback=prepare_layer(gpd.GeoDataFrame(geometry=[box(0,0,2,2)],crs=2180),'source','no fields')
        self.assertIsNone(matching_id(fallback.features['s1']))

    def test_unmatched_ids_and_symbols_keep_appearance_disappearance(self):
        s,t=self.identified([('source-only',box(0,0,2,2)),('symbol',box(10,0,12,2))],
                            [('target-only',box(0,0,2,2)),('symbol',Point(11,1)),('symbol',LineString([(10,0),(12,0)]))])
        gs=self.accepted(s,t)
        self.assertEqual([g['decision'] for g in gs],['disappearance','disappearance','appearance','appearance','appearance'])

    def test_id_unions_keep_different_and_missing_ids_separate(self):
        s,t=self.identified([('A',box(0,0,4,4)),('B',box(1,0,5,4)),('A',box(3,0,7,4)),(None,box(2,0,6,4))],
                            [('A',box(0,0,7,4))])
        result=union_overlaps(s,match_by_id=True)
        self.assertEqual(set(result.features),{'s1','s2','s4'})
        self.assertEqual(result.features['s1'].members,('s1','s3'))
        self.assertEqual(matching_id(result.features['s1']),'A')
        self.assertTrue(overlap_review(result));self.assertEqual(len(s.features),4)
        spatial=union_overlaps(s)
        self.assertEqual(len(spatial.features),1)
        self.assertIsNone(matching_id(next(iter(spatial.features.values()))))

    def test_direct_build_rejects_cross_id_and_partial_id_groups(self):
        s,t=self.identified([('A',box(0,0,2,2)),('A',box(4,0,6,2)),('B',box(10,0,12,2))],
                            [('A',box(0,0,6,2)),('B',box(10,0,12,2))])
        bad=[[group(['s1','s2'],['t2']),group(['s3'],['t1'],id='g2')],
             [group(['s1'],['t1']),group(['s2'],[],'disappearance','g2'),group(['s3'],['t2'],id='g3')]]
        before=_core()
        for gs in bad:
            with self.assertRaisesRegex(ValueError,'ID-only'):build_mapping(s,t,gs,dict(mode='classic',match_by_id=True))
            self.assertEqual(_core(),before)


class MappingBuilderTests(unittest.TestCase):
    def test_independent_intersections_are_reported_together(self):
        fs=[]
        for base,owner in ((0,1),(10,3)):
            fs.extend([Face(owner,[(base,0,0),(base+2,0,0),(base+2,0,2),(base,0,2)]),
                       Face(owner+1,[(base+1,-1,0),(base+1,1,0),(base+1,1,2),(base+1,-1,2)])])
        with self.assertRaises(TransitionShellError) as caught:
            check_face_intersections(fs,across_cells=True,collect_failures=True)
        report=caught.exception.report
        self.assertEqual(report['classification'],'invalid_combined_interfaces')
        self.assertEqual({frozenset(f['owners']) for f in report['failures']},{frozenset((1,2)),frozenset((3,4))})
        with self.assertRaises(TransitionShellError) as strict:check_face_intersections(fs,across_cells=True)
        self.assertEqual(strict.exception.report['classification'],'self_intersection')

    def test_validation_cache_reuses_only_exact_shells_and_always_checks_interfaces(self):
        shell=_prism(1,box(0,0,2,2),0,5);cache=set();before=_core()
        try:
            with patch('generalization_model.check_face_intersections',wraps=check_face_intersections) as check:
                realize(shell,(0,0),validated_shells=cache)
                reordered=[Face(f.owner,f.points[1:]+f.points[:1],f.part) for f in reversed(shell)]
                realize(reordered,(0,0),validated_shells=cache)
                self.assertEqual(sum(not call.kwargs.get('across_cells',False) for call in check.call_args_list),1)
                self.assertEqual(sum(call.kwargs.get('across_cells',False) for call in check.call_args_list),2)
                realize(_prism(1,box(0,0,2,2),0,6),(0,0),validated_shells=cache)
                self.assertEqual(sum(not call.kwargs.get('across_cells',False) for call in check.call_args_list),2)
                wrong=[Face(f.owner,f.points,'transition' if f.part=='top' else f.part) for f in shell]
                with self.assertRaises(TransitionShellError):realize(wrong,(0,0),validated_shells=cache)
        finally:_restore(before)

    def test_vertical_overlay_preserves_assigned_levels_at_new_intersections(self):
        a=np.array([1074.187115843004,294.62390437187673]);b=a+[6.3,9.7];c=a+.6*(b-a)
        xyz=lambda p,z:(float(p[0]),float(p[1]),z)
        faces=[Face(1,[xyz(a,5.),xyz(b,5.),xyz(c,15.),xyz(a,15.)],'wall'),
               Face(2,[xyz(a,0.),xyz(b,0.),xyz(b,10.),xyz(a,10.)],'wall'),
               Face(3,[xyz(a,0.),xyz(b,0.),xyz(b,10.+1e-9),xyz(a,10.+1e-9)],'wall')]
        result=_partition_plane(faces)
        levels={p[2] for f in result for p in f.points}
        self.assertEqual(levels,{0.,5.,10.,10.+1e-9,15.})
        # The slanted boundary introduces vertices that were absent from inputs.
        original={p for f in faces for p in f.points}
        self.assertTrue(any(p not in original and p[2]==10. for f in result for p in f.points))

    def test_hybrid_retries_actual_colliding_transitions_through_classic(self):
        gs=[group(['s1'],['t1'],id='a'),group(['s2'],['t2'],id='b')]
        r,s=build([box(0,0,2,2),box(6,0,8,2)],[box(6,0,8,2),box(0,0,2,2)],groups=gs)
        self.assertTrue(r.report['retry_history']);self.assertEqual(r.report['endpoint_validation'],'passed')
        self.assertEqual({row['builder'] for row in r.report['routing']},{'classic'})
        self.assertTrue(all(row['initial_builder']=='smooth' and row['fallback'] for row in r.report['routing']))
        failures=r.report['retry_history'][0]['failures']
        # Coincident moving side walls are rejected at interface ownership,
        # before the later face-intersection check is needed.
        self.assertTrue(any(f['classification']=='interface_orientation_or_ownership' for f in failures))
        self.assertTrue(all(f['features'] for f in failures))

    def test_actual_column_incidence_retries_only_affected_group_and_identifies_inputs(self):
        source,target=column_incidence_pair();stable=box(0,0,2,2)
        gs=[group(['s1'],['t1'],id='g452'),group(['s2'],['t2'],id='stable')]
        r,s=build([source,stable],[target,stable],groups=gs)
        self.assertEqual(r.origin,(0.,0.))
        self.assertEqual(r.report['construction_attempts'],2)
        self.assertEqual([g['builder'] for g in r.report['routing']],['classic','smooth'])
        self.assertEqual(r.report['retry_history'][0]['groups'],['g452'])
        failure=r.report['retry_history'][0]['failures'][0]
        self.assertEqual(failure['classification'],'transition_column_incidence')
        self.assertEqual(failure['incidence'],3)
        self.assertEqual({(f['layer'],f['feature_id'],f['original_id']) for f in failure['features']},
                         {('source','s1','0'),('target','t1','0')})
        self.assertEqual(r.report['endpoint_validation'],'passed')
        self.assertNotIn('fallback',r.report['routing'][1])
        before=_core()
        with self.assertRaises(MappingBuildError) as caught:build([source,stable],[target,stable],mode='smooth',groups=gs)
        self.assertEqual(_core(),before)
        report=caught.exception.report
        self.assertEqual(report['phase'],'group_planning');self.assertEqual(report['retry_history'],[])
        self.assertEqual(report['failures'][0]['classification'],'transition_column_incidence')
        self.assertIn('source s1 (original 0, group g452',str(caught.exception))
        self.assertIn('target t1 (original 0, group g452',str(caught.exception))

    def test_cancel_after_actual_column_incidence_preserves_core(self):
        source,target=column_incidence_pair();stable=box(0,0,2,2)
        s,t=layers([source,stable],[target,stable])
        gs=[group(['s1'],['t1'],id='g452'),group(['s2'],['t2'],id='stable')]
        stop=Event();messages=[];before=_core()
        def progress(message):
            messages.append(message)
            if message.startswith('Retrying g452 through Classic: transition_column_incidence'):stop.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            build_mapping(s,t,gs,dict(mode='hybrid'),progress=progress,cancelled=stop.is_set)
        self.assertTrue(stop.is_set());self.assertEqual(_core(),before)
        self.assertFalse(any('Conforming common interfaces' in message for message in messages))

    def test_independent_shell_failures_are_batched_and_valid_smooth_group_remains(self):
        def incomplete(owner,sources,target,*args,**kwargs):
            fs,details=smooth_faces(owner,sources,target,*args,**kwargs)
            return (fs[:-1] if sources[0].bounds[0]<10 else fs),details
        sources=[box(0,0,2,2),box(5,0,7,2),box(12,0,14,2)]
        targets=[translate(p,xoff=.25) for p in sources]
        gs=[group([f's{i}'],[f't{i}'],id=f'g{i}') for i in range(1,4)]
        with patch('mapping_builders.smooth_faces',side_effect=incomplete):r,s=build(sources,targets,groups=gs)
        self.assertEqual(r.report['construction_attempts'],2)
        self.assertEqual(r.report['retry_history'][0]['groups'],['g1','g2'])
        self.assertEqual([g['builder'] for g in r.report['routing']],['classic','classic','smooth'])

    def test_retry_terminates_and_does_not_hide_strict_or_unrelated_errors(self):
        s,t=layers([box(0,0,2,2)],[box(.25,0,2.25,2)]);gs=[group(['s1'],['t1'])]
        before=_core()
        for mode,count in (('hybrid',2),('smooth',1),('classic',1)):
            with patch('mapping_builders.realize',side_effect=lambda *a,**k:(_ for _ in ()).throw(
                    TransitionShellError('Test unpaired edge','unmatched_edges',cell=2,stage='conforming_boundary'))) as construct:
                with self.assertRaises(MappingBuildError) as caught:build_mapping(s,t,gs,dict(mode=mode))
                self.assertEqual(construct.call_count,count)
                self.assertIn('target t1',str(caught.exception));self.assertIn('group g1',str(caught.exception))
            self.assertEqual(_core(),before)
        with patch('mapping_builders.realize',side_effect=RuntimeError('Unexpected allocation error')) as construct:
            with self.assertRaisesRegex(RuntimeError,'Unexpected allocation'):build_mapping(s,t,gs,{})
            self.assertEqual(construct.call_count,1)

    def test_cancel_between_retry_attempts_preserves_core(self):
        s,t=layers([box(0,0,2,2)],[box(.25,0,2.25,2)]);gs=[group(['s1'],['t1'])]
        stop=Event();before=_core()
        def progress(message):
            if message.startswith('Retrying '):stop.set()
        with patch('mapping_builders.realize',side_effect=TransitionShellError('Test edge','unmatched_edges',cell=2)) as construct:
            with self.assertRaisesRegex(ValueError,'cancelled'):build_mapping(s,t,gs,{},progress=progress,cancelled=stop.is_set)
            self.assertEqual(construct.call_count,1)
        self.assertEqual(_core(),before)

    def test_holed_source_and_target_preserve_empty_interiors_and_logical_identity(self):
        donut=Polygon(box(0,0,4,4).exterior.coords,[box(1,1,3,3).exterior.coords])
        for source,target in ((box(0,0,4,4),donut),(donut,box(0,0,4,4)),(donut,donut)):
            with self.subTest(source_holes=len(source.interiors),target_holes=len(target.interiors)):
                r,s=build([source],[target])
                self.assertEqual(r.report['routing'][0]['builder'],'classic')
                self.assertIn('Hole-preserving',r.report['routing'][0]['reason'])
                self.assertEqual(r.report['logical_features'],dict(source=1,target=1))
                self.assertTrue(r.report['decompositions']);self.assertEqual(r.report['endpoint_validation'],'passed')
                for side,z,expected in (('source',5,source),('target',15,target)):
                    actual=s.slicer.sections(SlicePlane.horizontal(z),include_degenerate=True)
                    measured=unary_union([actual[str(k)] for k,m in r.metadata.items() if m['layer']==side and str(k) in actual])
                    self.assertLess(measured.symmetric_difference(expected).area,1e-8)
                    if expected.interiors:self.assertEqual(measured.intersection(box(1,1,3,3)).area,0)
        r,_=build([box(0,0,4,4)],[donut],mode='smooth')
        self.assertEqual(r.report['routing'][0]['construction']['method'],'continuous_boundary_distance')

    def test_indexed_noding_preserves_exhaustive_three_dimensional_incidence(self):
        fs=[Face(1,[(0,0,0),(8,0,8),(0,4,0)]),
            Face(2,[(0,0,0),(0,0,8),(0,-2,8),(0,-2,0)]),
            Face(3,[(4,0,4),(0,0,4),(4,1e-6,4)])]
        fs.extend(Face(i+4,[(20+i*3,0,0),(21+i*3,0,1),(20+i*3,1,0)]) for i in range(350))
        indexed=_node_faces(fs)
        with patch('generalization_model.STRtree',side_effect=lambda ps:SimpleNamespace(query=lambda _:np.arange(len(ps)))):
            exhaustive=_node_faces(fs)
        self.assertEqual(indexed,exhaustive)
        self.assertIn((4.,0.,4.),indexed[0].points)
        self.assertIn((0.,0.,4.),indexed[1].points)
        self.assertNotIn((4.,1e-6,4.),indexed[0].points)

    def test_classic_overlay_split_and_many_to_many_real_face_links(self):
        r,s=build([box(0,0,4,2)],[box(0,0,2,2),box(2,0,4,2)])
        self.assertEqual(r.report['routing'][0]['builder'],'classic')
        self.assertEqual(len(r.model.cells),3);self.assertEqual(len(r.model.shared_walls),3)
        r,s=build([box(0,0,2,4),box(2,0,4,4)],[box(0,0,4,2),box(0,2,4,4)])
        self.assertEqual(len(r.model.cells),4)
        cross=[(a,b) for a,b in r.model.shared_walls if r.metadata[int(a.D.V.i)]['layer']!=r.metadata[int(b.D.V.i)]['layer']]
        self.assertEqual(len(cross),4)
        self.assertTrue(s.payload['dual_edges']);self.assertEqual(r.report['endpoint_validation'],'passed')

    def test_displacement_rotation_and_general_boundary_target(self):
        source=box(0,0,4,2);target=translate(rotate(source,25,origin='centroid'),xoff=7,yoff=1)
        r,s=build([source],[target],mode='smooth')
        self.assertIn('anchor',r.report['routing'][0]['construction'])
        self.assertTrue(s.section('Horizontal',10)['features']['features'])
        r,s=build([source],[translate(source,xoff=1)],mode='smooth')
        self.assertIn('anchor',r.report['routing'][0]['construction'])
        target=Polygon([(0,0),(4,0),(4,1.5),(3.5,2),(0,2)])
        r,s=build([source],[target],mode='smooth');self.assertEqual(r.report['endpoint_validation'],'passed')

    def test_smooth_supplied_collapse_targets_and_no_silent_fallback(self):
        for target in (Point(2,1),LineString([(1,1),(3,1)])):
            r,s=build([box(0,0,4,2)],[target],mode='smooth')
            top=s.section('Horizontal',15)
            self.assertEqual(top['features']['features'][0]['geometry']['type'],target.geom_type)
        with self.assertRaises(MappingBuildError):build([box(0,0,4,2)],[Point(9,9)],mode='smooth')
        with self.assertRaises(MappingBuildError):build([box(0,0,4,2)],[box(0,0,2,2),box(2,0,4,2)],mode='smooth')

    def test_classic_edge_sheet_and_bent_line_use_actual_primal_incidence(self):
        for target in (Point(2,1),LineString([(1,1),(3,1)]),LineString([(1,1),(2,2),(3,1)])):
            with self.subTest(target=target.wkt):
                r,s=build([box(0,0,4,3)],[target],mode='classic')
                self.assertTrue(r.model.mapping_primitives);self.assertTrue(r.model.mapping_contacts)
                if target.geom_type=='LineString':self.assertIn(1,[c['dimension'] for c in r.model.mapping_contacts])
                for c in r.model.mapping_contacts:
                    a,b=c['first'],c['second'];self.assertIn(b,a.mapping_incident);self.assertIn(a,b.mapping_incident)
                    if c['dimension']==1:self.assertEqual({a.V,a.S.V},{b.V,b.S.V})
                    else:self.assertIs(a.V,b.V)
                self.assertTrue(s.section('Horizontal',15)['features']['features'])
                for orientation,value in (('Vertical',0.),('Inclined',0.)):
                    self.assertTrue(s.section(orientation,value,clamp_position=True)['sections_computed'])

    def test_two_stage_aggregation_reaches_non_hull_target_in_one_upper_cell(self):
        ss=[box(0,0,2,2),box(4,0,6,2)];target=box(-.5,-.5,6.5,2.5)
        r,s=build(ss,[target]);self.assertEqual(len(r.model.cells),3)
        row=r.report['routing'][0];self.assertEqual(row['construction']['stage_z'],10)
        section=s.slicer.sections(SlicePlane.horizontal(10),include_degenerate=True)
        upper=[str(k) for k,v in r.metadata.items() if v['layer']=='target'][0]
        hull=translate(unary_union(ss).convex_hull,xoff=-r.origin[0],yoff=-r.origin[1])
        self.assertLess(section[upper].symmetric_difference(hull).area,1e-6)

    def test_mixed_hybrid_and_explicit_unmatched(self):
        groups=[group(['s1','s2'],['t1'],id='a'),group(['s3'],['t2'],id='b')]
        r,s=build([box(0,0,2,2),box(2,0,4,2),box(10,0,12,2)],
                  [box(0,0,4,2),box(11,0,13,2)],groups=groups)
        self.assertEqual([g['builder'] for g in r.report['routing']],['classic','smooth'])
        r,s=build([box(0,0,2,2)],[box(8,0,10,2)],groups=[group(['s1'],[],'disappearance','a'),group([],['t1'],'appearance','b')])
        self.assertTrue(all(r.metadata[int(a.D.V.i)]['group']==r.metadata[int(b.D.V.i)]['group'] for a,b in r.model.shared_walls))
        self.assertEqual([g['builder'] for g in r.report['routing']],['smooth','smooth'])

    def test_collisions_overlaps_and_cancellation_never_publish_bad_geometry(self):
        ss,ts=layers([box(0,0,2,2)],[box(0,0,2,2)]);groups=[group(['s1'],['t1'])]
        before=_core()
        with self.assertRaises(ValueError):build_mapping(ss,ts,groups,{},cancelled=lambda:True)
        self.assertEqual(_core(),before)
        with self.assertRaises(MappingBuildError):build([box(0,0,2,2),box(1,0,3,2)],[box(0,0,3,2)])
        with self.assertRaises(MappingBuildError):
            build([box(0,0,2,2),box(6,0,8,2)],[box(6,0,8,2),box(0,0,2,2)],mode='smooth',
                  groups=[group(['s1'],['t1'],id='a'),group(['s2'],['t2'],id='b')])


class MappingCentroidTests(unittest.TestCase):
    def unmatched(self,geometry,appearance,mode='smooth'):
        other=box(-30,-30,-28,-28)
        return build([other] if appearance else [geometry],[geometry] if appearance else [other],mode=mode,
                     groups=[group([] if appearance else ['s1'],['t1'] if appearance else [],
                                   'appearance' if appearance else 'disappearance'),
                             group(['s1'] if appearance else [],[] if appearance else ['t1'],'exclude','excluded')])

    def section(self,result,scene,z):
        sections=scene.slicer.sections(SlicePlane.horizontal(z),include_degenerate=True)
        return unary_union(list(sections.values()))

    def test_uniform_centroid_scaling_and_terminal_point_in_both_modes(self):
        # The L's true centroid is outside its footprint. It must still scale
        # uniformly instead of inheriting supplied-point visibility conditions.
        shapes=[box(100,200,108,204),Polygon([(0,0),(6,0),(6,1),(1,1),(1,6),(0,6)])]
        self.assertFalse(shapes[1].covers(shapes[1].centroid))
        for geometry in shapes:
            for appearance in (False,True):
                for mode in ('smooth','hybrid'):
                    with self.subTest(geometry=geometry.wkt,appearance=appearance,mode=mode):
                        r,s=self.unmatched(geometry,appearance,mode)
                        local=translate(geometry,xoff=-r.origin[0],yoff=-r.origin[1])
                        for z in (7.5,10.,12.5):
                            factor=(z-5)/10 if appearance else (15-z)/10
                            expected=scale(local,xfact=factor,yfact=factor,origin='centroid')
                            actual=self.section(r,s,z)
                            self.assertLess(actual.symmetric_difference(expected).area,1e-7)
                            self.assertAlmostEqual(actual.area,geometry.area*factor**2)
                        apex=self.section(r,s,5 if appearance else 15)
                        self.assertEqual(apex.geom_type,'Point');self.assertLess(apex.distance(local.centroid),1e-7)
                        self.assertEqual(r.report['routing'][0]['builder'],'smooth')
                        np.testing.assert_allclose(r.report['routing'][0]['construction']['centroid'],geometry.centroid.coords[0],rtol=0,atol=1e-7)
                        self.assertEqual(r.report['endpoint_validation'],'passed')
                        self.assertEqual(len(s.payload['features']['features']),int(appearance))

    def test_multipart_classic_hull_stage_and_reverse_appearance(self):
        geometry=MultiPolygon([box(0,0,2,2),box(6,0,10,2)])
        for appearance in (False,True):
            for mode in ('smooth','hybrid'):
                with self.subTest(appearance=appearance,mode=mode):
                    r,s=self.unmatched(geometry,appearance,mode)
                    hull=translate(geometry.convex_hull,xoff=-r.origin[0],yoff=-r.origin[1])
                    # Below/above ownership selects the union cell at the interface.
                    sections=s.slicer.sections(SlicePlane.horizontal(10),horizontal_side='below' if appearance else 'above')
                    self.assertLess(unary_union(list(sections.values())).symmetric_difference(hull).area,1e-7)
                    expected=scale(hull,xfact=.5,yfact=.5,origin='centroid')
                    self.assertLess(self.section(r,s,7.5 if appearance else 12.5).symmetric_difference(expected).area,1e-7)
                    self.assertEqual(r.report['routing'][0]['construction']['stage_z'],10)
                    self.assertEqual(len(s.features),2 if appearance else 0)
                    self.assertTrue(all(not r.metadata[o].get('intermediate') for o in s.features))
                    self.assertEqual(r.report['endpoint_validation'],'passed')

    def test_multiple_features_keep_union_provenance(self):
        for appearance in (False,True):
            for touching in (False,True):
                parts=[box(0,0,2,2),box(2 if touching else 6,0,10,2)];other=[box(30,0,32,2)]
                ids=['t1','t2'] if appearance else ['s1','s2']
                r,s=build(other if appearance else parts,parts if appearance else other,mode='smooth',
                          groups=[group([] if appearance else ids,ids if appearance else [],'appearance' if appearance else 'disappearance'),
                                  group(['s1'] if appearance else [],[] if appearance else ['t1'],'exclude','excluded')])
                union=next(m for m in r.metadata.values() if m.get('intermediate'))
                self.assertEqual(union['members'],ids)
                self.assertEqual({m['uid'] for m in union['member_records']},set(ids))
                self.assertEqual(r.report['logical_features']['target' if appearance else 'source'],2)
                self.assertEqual(len(s.features),2 if appearance else 0)

    def test_grouped_polygon_and_symbol_appearances_keep_all_targets(self):
        targets=[box(0,0,2,2),box(4,0,6,2),Point(12,3),LineString([(15,0),(17,2)])]
        r,s=build([box(-30,-30,-28,-28)],targets,mode='smooth',
                  groups=[group([],['t1','t2','t3','t4'],'appearance'),group(['s1'],[],'exclude','excluded')])
        self.assertEqual({r.metadata[o]['feature_id'] for o in s.features},{'t1','t2','t3','t4'})
        self.assertEqual(len(r.report['routing'][0]['construction']['transitions']),3)
        self.assertEqual(r.report['endpoint_validation'],'passed')

    def test_generated_shell_failures_use_hybrid_fallback_and_smooth_stays_strict(self):
        def incomplete(*args,**kwargs):return centroid_faces(*args,**kwargs)[:-1]
        for appearance in (False,True):
            with patch('mapping_builders.centroid_faces',side_effect=incomplete):
                r,s=self.unmatched(box(0,0,4,2),appearance,'hybrid')
                self.assertEqual(r.report['construction_attempts'],2)
                self.assertEqual(r.report['routing'][0]['builder'],'classic')
                self.assertEqual(r.report['routing'][0]['initial_builder'],'smooth')
                self.assertTrue(r.report['routing'][0]['fallback'])
                with self.assertRaises(MappingBuildError):self.unmatched(box(0,0,4,2),appearance,'smooth')

    def test_point_and_line_appearances_are_actual_primitives(self):
        for geometry in (Point(4,3),LineString([(0,0),(6,0)]),LineString([(0,0),(2,3),(6,0)])):
            for mode in ('smooth','hybrid'):
                with self.subTest(geometry=geometry.wkt,mode=mode):
                    r,s=self.unmatched(geometry,True,mode)
                    local=translate(geometry,xoff=-r.origin[0],yoff=-r.origin[1])
                    expected=scale(local,xfact=.5,yfact=.5,origin='centroid')
                    middle=s.section('Horizontal',10)['features']['features']
                    self.assertTrue(middle)
                    actual=self.section(r,s,10) if geometry.geom_type!='Point' else Point(r.model.mapping_primitives[0].seed.V.x,r.model.mapping_primitives[0].seed.V.y)
                    self.assertLess(actual.hausdorff_distance(expected),1e-7)
                    self.assertEqual(r.report['endpoint_validation'],'passed')
                    if geometry.geom_type=='LineString':
                        self.assertTrue(all(len(f.points)==3 for f,_ in r.model.mapping_face_records))
                        self.assertLess(self.section(r,s,5).distance(local.centroid),1e-7)

    def test_unmatched_holes_keep_existing_routing(self):
        geometry=Polygon(box(0,0,8,8).exterior.coords,[box(2,2,6,6).exterior.coords])
        for appearance in (False,True):
            r,s=self.unmatched(geometry,appearance,'hybrid')
            self.assertEqual(r.report['routing'][0]['builder'],'classic')
            with self.assertRaisesRegex(MappingBuildError,'holes'):
                self.unmatched(geometry,appearance,'smooth')


class MappingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        for side,geometries in (('source',[box(0,0,4,2)]),('target',[box(1,0,5,2)])):
            gpd.GeoDataFrame({'id':[side]},geometry=geometries,crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.w=Workspace(root/'source.geojson',mapping_paths={side:root/(side+'.geojson') for side in ('source','target')})
        self.client=TestClient(create_app(workspace=self.w,autoload=False));self.client.__enter__()

    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()

    def command(self,action,parameters=None,success=True):
        answer=self.client.post('/api/mapping/'+action,json=dict(revision=self.w.mapping.revision,parameters=parameters or {}))
        self.assertEqual(answer.status_code,200,answer.text);token=answer.json()['job_id'];deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            job=self.client.get('/api/jobs/'+token).json()
            if job['status']!='running':break
            time.sleep(.01)
        self.assertNotEqual(job['status'],'running')
        if success:self.assertEqual(job['status'],'complete',job)
        else:self.assertEqual(job['status'],'failed',job)
        return job

    def ready(self):
        self.command('load',{'side':'source'});self.command('load',{'side':'target'});self.command('analyze')
        gs=deepcopy(self.w.mapping.groups)
        for g in gs:g['confirmed']=True
        self.command('review',{'groups':gs,'settings':{'mode':'classic'}})

    def test_actual_column_retry_preview_publication_strict_failure_and_cancel_are_atomic(self):
        source,target=column_incidence_pair();stable=box(0,0,2,2);root=Path(self.temp.name)
        for side,geometry in (('source',source),('target',target)):
            gpd.GeoDataFrame({'id':['failing','stable']},geometry=[geometry,stable],crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.ready();self.command('build');previous=self.w.mapping.result
        gs=[group(['s1'],['t1'],id='g452'),group(['s2'],['t2'],id='stable')]
        self.command('review',{'groups':gs,'settings':{'mode':'hybrid'}})
        self.command('build',{'preview':True})
        self.assertIs(self.w.mapping.result,previous)
        preview=self.w.mapping.preview
        report=self.client.get('/api/mapping/report',params={'preview_id':preview.preview_id}).json()
        self.assertEqual(report['endpoint_validation'],'passed')
        self.assertEqual(report['retry_history'][0]['failures'][0]['classification'],'transition_column_incidence')
        state=self.client.get('/api/mapping/state').json()
        self.assertEqual([g['builder'] for g in state['groups']],['classic','smooth'])
        self.assertEqual(state['groups'][0]['initial_builder'],'smooth');self.assertTrue(state['groups'][0]['fallback'])
        self.command('build');published=self.w.mapping.result
        self.assertIsNone(self.w.mapping.preview)
        self.assertEqual(published.result.report['endpoint_validation'],'passed')
        stop=Event();m=self.w.mapping;revision=m.revision;inputs=m.layers;groups=m.groups
        with self.assertRaisesRegex(ValueError,'cancelled'):
            m.execute('build',revision,progress=lambda message:stop.set() if message.startswith('Retrying ') else None,cancelled=stop.is_set)
        self.assertTrue(stop.is_set());self.assertIs(m.result,published)
        self.assertEqual(m.revision,revision);self.assertIs(m.layers,inputs);self.assertIs(m.groups,groups)
        self.command('review',{'groups':gs,'settings':{'mode':'smooth'}})
        revision=m.revision;job=self.command('build',success=False)
        self.assertEqual(job['diagnostic']['failures'][0]['classification'],'transition_column_incidence')
        self.assertEqual(job['diagnostic']['retry_history'],[])
        self.assertIn('original failing, group g452',job['error'])
        self.assertEqual(m.revision,revision);self.assertIs(m.result,published)

    def test_skeleton_fallback_preview_report_export_strict_failure_and_cancel_are_atomic(self):
        from test_mapping_skeleton_recovery import BACKEND_FAILURE
        root=Path(self.temp.name)
        for side,geometries in (('source',[box(0,0,2,2),box(4,0,6,2)]),('target',[box(0,0,6,2)])):
            gpd.GeoDataFrame({'id':list(range(len(geometries)))},geometry=geometries,crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.ready();self.command('build');m=self.w.mapping;previous=m.result
        gs=[group(['s1','s2'],['t1'])]
        self.command('review',{'groups':gs,'settings':{'mode':'hybrid'}})
        with patch('aggregation_geometry._skeleton_at_margin',side_effect=ValueError(BACKEND_FAILURE)):
            self.command('build',{'preview':True});preview=m.preview
            self.assertIs(m.result,previous)
            report=self.client.get('/api/mapping/report',params={'preview_id':preview.preview_id}).json()
            self.assertIn(BACKEND_FAILURE,report['routing'][0]['construction']['attempts'][0]['message'])
            self.assertEqual(report['routing'][0]['construction']['method'],'medial_axis')
            exported=self.client.get('/api/export/duality',params={'workspace_name':'mapping','snapshot_id':preview.payload['snapshot_id']})
            self.assertEqual(exported.status_code,200)
            self.assertEqual(exported.json()['metadata']['mapping_report']['retry_history'],report['retry_history'])
            state=self.client.get('/api/mapping/state').json()
            self.assertEqual(state['groups'][0]['builder'],'smooth');self.assertEqual(state['groups'][0]['initial_builder'],'smooth')
            self.command('build');published=m.result;self.assertIsNone(m.preview)
            stop=Event();revision=m.revision;inputs=m.layers;groups=m.groups
            with self.assertRaisesRegex(ValueError,'cancelled'):
                m.execute('build',revision,progress=lambda message:stop.set() if message.startswith('Conforming common interfaces') else None,cancelled=stop.is_set)
            self.assertEqual(m.revision,revision);self.assertIs(m.result,published);self.assertIs(m.layers,inputs);self.assertIs(m.groups,groups)
            self.command('review',{'groups':gs,'settings':{'mode':'smooth'}});revision=m.revision
            self.command('build')
            self.assertEqual(m.revision,revision+1)
            self.assertEqual(m.result.result.report['routing'][0]['construction']['method'],'medial_axis')
            self.assertEqual(m.result.result.report['retry_history'],[])

    def test_continuous_smooth_hole_preview_export_publication_failure_and_cancel(self):
        from mapping_builders import continuous_polygon_faces
        root=Path(self.temp.name);target=Polygon(box(0,0,6,6).exterior.coords,[box(2,2,4,4).exterior.coords])
        for side,geometries in (('source',[box(0,0,2,2),box(4,0,6,2)]),('target',[target])):
            gpd.GeoDataFrame({'id':list(range(len(geometries)))},geometry=geometries,crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.w.execute('default',0);generalize=self.w.scene;core=_core()
        self.ready();self.command('build');m=self.w.mapping;old=m.result
        self.command('review',{'groups':m.groups,'settings':{'mode':'smooth'}});revision=m.revision
        self.command('build',{'preview':True});self.assertIs(m.result,old);self.assertEqual(m.revision,revision)
        token=m.preview.preview_id;payload=self.client.get('/api/mapping/scene',params={'preview_id':token}).json()
        report=payload['mapping_report'];construction=report['routing'][0]['construction']
        self.assertIn(construction['method'],('straight_skeleton','medial_axis'))
        self.assertGreater(construction['hull_to_target']['upper_cells'],1)
        self.assertEqual(report['logical_features'],dict(source=2,target=1));self.assertEqual(report['endpoint_validation'],'passed')
        export=self.client.get('/api/export/duality',params={'workspace_name':'mapping','snapshot_id':payload['snapshot_id']})
        self.assertEqual(export.status_code,200)
        self.assertEqual(export.json()['metadata']['mapping_report']['routing'][0]['construction'],construction)
        self.assertIs(self.w.scene,generalize);self.assertEqual(_core(),core)
        self.command('build');published=m.result;self.assertIsNone(m.preview);self.assertEqual(m.revision,revision+1)
        revision=m.revision;inputs=m.layers;groups=m.groups
        with patch('mapping_builders.continuous_polygon_faces',side_effect=ValueError('Test continuous failure')):
            failed=self.command('build',success=False)
        self.assertIn('Test continuous failure',failed['error']);self.assertEqual(failed['diagnostic']['retry_history'],[])
        stop=Event()
        def cancel_after_surface(*args,**kwargs):
            result=continuous_polygon_faces(*args,**kwargs);stop.set();return result
        with patch('mapping_builders.continuous_polygon_faces',side_effect=cancel_after_surface):
            with self.assertRaisesRegex(ValueError,'cancelled'):m.execute('build',revision,cancelled=stop.is_set)
        self.assertEqual(m.revision,revision);self.assertIs(m.result,published);self.assertIs(m.layers,inputs);self.assertIs(m.groups,groups)
        self.assertIs(self.w.scene,generalize);self.assertEqual(_core(),core)

    def test_id_setting_before_import_batch_build_and_switch_back(self):
        root=Path(self.temp.name)
        for side,ids in (('source',['A','B']),('target',['B','A'])):
            gpd.GeoDataFrame({'id':ids},geometry=[box(0,0,2,2),box(10,0,12,2)],crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.assertFalse(self.w.mapping.settings['match_by_id'])
        self.command('analyze',{'settings':{'match_by_id':True,'mode':'classic'}})
        self.assertTrue(self.w.mapping.settings['match_by_id']);self.assertEqual(self.w.mapping.groups,[])
        for side in ('source','target'):self.command('load',{'side':side})
        pairs=lambda:[(g['sources'],g['targets']) for g in self.w.mapping.groups]
        self.assertEqual(pairs(),[(['s1'],['t2']),(['s2'],['t1'])])
        self.command('build',{'preview':True});self.assertIsNotNone(self.w.mapping.preview)
        self.command('build');old=self.w.mapping.result
        self.assertTrue(old.result.report['settings']['match_by_id'])
        self.assertEqual(old.result.report['endpoint_validation'],'passed')
        self.command('analyze',{'settings':{'match_by_id':False}})
        self.assertEqual(pairs(),[(['s1'],['t1']),(['s2'],['t2'])])
        self.assertIs(self.w.mapping.result,old)
        self.assertTrue(self.client.get('/api/mapping/scene').json()['stale'])
        self.command('build',{'preview':True})
        self.command('analyze',{'settings':{'match_by_id':True}})
        self.assertIsNone(self.w.mapping.preview)
        self.assertEqual(pairs(),[(['s1'],['t2']),(['s2'],['t1'])])
        bad=[group(['s1'],['t1']),group(['s2'],['t2'],id='g2')]
        revision=self.w.mapping.revision
        self.command('review',{'groups':bad},success=False)
        self.assertEqual(self.w.mapping.revision,revision)
        self.command('analyze',{'settings':{'match_by_id':'true'}},success=False)
        self.assertEqual(self.w.mapping.revision,revision)

    def test_id_mode_restores_inputs_and_keeps_explicit_overlap_resolution(self):
        root=Path(self.temp.name)
        gpd.GeoDataFrame({'id':['A','B']},geometry=[box(0,0,4,4),box(3,0,7,4)],crs=2180).to_file(root/'source.geojson',driver='GeoJSON')
        gpd.GeoDataFrame({'id':['A']},geometry=[box(0,0,4,4)],crs=2180).to_file(root/'target.geojson',driver='GeoJSON')
        for side in ('source','target'):self.command('load',{'side':side})
        self.assertEqual(len(self.w.mapping.layers['source'].features),1)
        originals={k:f.geometry.wkb for k,f in self.w.mapping.input_layers['source'].features.items()}
        saved_layers=self.w.mapping.layers;revision=self.w.mapping.revision
        with self.assertRaisesRegex(ValueError,'cancelled'):
            self.w.mapping.execute('analyze',revision,settings={'match_by_id':True},cancelled=lambda:True)
        self.assertIs(self.w.mapping.layers,saved_layers);self.assertFalse(self.w.mapping.settings['match_by_id'])
        self.command('analyze',{'settings':{'match_by_id':True,'mode':'classic'}})
        self.assertEqual({k:f.geometry.wkb for k,f in self.w.mapping.layers['source'].features.items()},originals)
        self.assertTrue(self.client.get('/api/mapping/state').json()['layers']['source']['overlaps'])
        self.command('build',success=False)
        self.command('overlap',{'side':'source','ids':['s1','s2'],'resolution':'union','keeper':'s1'},success=False)
        self.command('overlap',{'side':'source','ids':['s1','s2'],'resolution':'assign','keeper':'s1'})
        resolved={k:f.geometry.wkb for k,f in self.w.mapping.layers['source'].features.items()}
        self.assertFalse(overlap_review(self.w.mapping.layers['source']))
        self.command('build')
        for value in (False,True):
            self.command('analyze',{'settings':{'match_by_id':value}})
            self.assertEqual({k:f.geometry.wkb for k,f in self.w.mapping.layers['source'].features.items()},resolved)

    def test_automatic_unmatched_centroid_preview_build_and_slicing(self):
        root=Path(self.temp.name)
        for side,geometry in (('source',box(0,0,4,2)),('target',box(20,0,24,2))):
            gpd.GeoDataFrame({'id':[side]},geometry=[geometry],crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        for side in ('source','target'):self.command('load',{'side':side})
        for mode in ('smooth','hybrid'):
            self.command('review',{'groups':deepcopy(self.w.mapping.groups),'settings':{'mode':mode}})
            state=self.client.get('/api/mapping/state').json()
            self.assertEqual([g['decision'] for g in state['groups']],['disappearance','appearance'])
            self.assertTrue(all(g['builder']=='smooth' for g in state['groups']))
            self.command('build',{'preview':True})
            preview=self.w.mapping.preview
            self.assertEqual(preview.result.report['endpoint_validation'],'passed')
            response=self.client.post('/api/mapping/slice',json=dict(revision=self.w.mapping.revision,preview_id=preview.preview_id,value=10))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['features']['features']),2)
            self.command('build')
            self.assertTrue(all(g['builder']=='smooth' for g in self.w.mapping.result.result.report['routing']))

    def test_load_and_build_automatically_union_review_and_preserve_union_hole(self):
        a=unary_union([box(0,0,4,1),box(0,0,1,4),box(3,0,4,4)])
        root=Path(self.temp.name)
        for side,gs in (('source',[box(0,0,4,4)]),('target',[a,box(0,3,4,4)])):
            gpd.GeoDataFrame({'id':[f'{side}-{i}' for i in range(len(gs))]},geometry=gs,crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        self.command('load',{'side':'source'});self.command('load',{'side':'target'})
        state=self.client.get('/api/mapping/state').json()
        self.assertEqual(state['layers']['target']['count'],1)
        self.assertEqual(state['automatic_review']['union_groups'],1)
        self.assertEqual(state['automatic_review']['holed_features'],1)
        self.assertTrue(all(g['confirmed'] for g in state['groups']))
        self.assertEqual(state['groups'][0]['builder'],'classic')
        self.command('build') # No analyze, manual review or confirmation.
        report=self.client.get('/api/mapping/report').json()
        self.assertEqual(report['endpoint_validation'],'passed')
        target_records=[m for m in report['cell_metadata'].values() if m['layer']=='target']
        self.assertGreater(len(target_records),1)
        self.assertTrue(all({r['original_id'] for r in m['member_records']}=={'target-0','target-1'} for m in target_records))
        self.assertTrue(all(d['area_difference']<1e-9 for d in report['decompositions']))

    def test_optional_manual_groups_survive_save_and_build(self):
        self.command('load',{'side':'source'});self.command('load',{'side':'target'})
        gs=deepcopy(self.w.mapping.groups);gs[0].update(id='custom',anchor=1,confirmed=False)
        self.command('review',{'groups':gs,'settings':{'mode':'classic'}})
        self.assertEqual(self.w.mapping.groups[0]['id'],'custom')
        self.assertTrue(self.w.mapping.groups[0]['confirmed'])
        self.command('build')
        self.assertEqual(self.w.mapping.result.result.report['groups'][0]['anchor'],1)
        # Matching settings explicitly recompute groups; old reports stay frozen.
        self.command('review',{'groups':self.w.mapping.groups,'settings':{'overlap_fraction':.5}})
        self.assertNotEqual(self.w.mapping.groups[0]['id'],'custom')
        self.assertTrue(all(g['confirmed'] for g in self.w.mapping.groups))
        self.assertEqual(self.w.mapping.result.result.report['groups'][0]['id'],'custom')

    def test_hybrid_final_routes_are_visible_for_preview_and_stale_reviews_reset_them(self):
        root=Path(self.temp.name)
        for side,gs in (('source',[box(0,0,2,2),box(6,0,8,2)]),('target',[box(6,0,8,2),box(0,0,2,2)])):
            gpd.GeoDataFrame({'id':[str(i) for i in range(2)]},geometry=gs,crs=2180).to_file(root/(side+'.geojson'),driver='GeoJSON')
        for side in ('source','target'):self.command('load',{'side':side})
        gs=[group(['s1'],['t1'],id='a'),group(['s2'],['t2'],id='b')]
        self.command('review',{'groups':gs})
        self.command('build',{'preview':True})
        state=self.client.get('/api/mapping/state').json()
        self.assertTrue(all(g['builder']=='classic' and g['initial_builder']=='smooth' and g['fallback'] for g in state['groups']))
        report=self.client.get('/api/mapping/report',params={'preview_id':state['preview_id']}).json()
        self.assertEqual(report['endpoint_validation'],'passed');self.assertTrue(report['retry_history'])
        self.command('review',{'groups':state['groups'],'settings':{'source_label':'new review'}})
        self.assertTrue(all(g['builder']=='smooth' and 'fallback' not in g for g in self.client.get('/api/mapping/state').json()['groups']))

    def test_failed_build_preparation_preserves_inputs_and_published_result(self):
        self.ready();self.command('build');previous=self.w.mapping.result
        s,t=layers([box(0,0,4,2),box(3,0,5,2)],[box(1,0,5,2)])
        raw=dict(source=s,target=t);self.w.mapping.layers=raw;self.w.mapping.groups=[]
        rev=self.w.mapping.revision
        def fail(source,target,groups,*args,**kwargs):
            self.assertEqual(len(source.features),1);self.assertTrue(all(g['confirmed'] for g in groups))
            raise ValueError('Test failure after automatic preparation')
        with patch('mapping_workspace.build_mapping',side_effect=fail):self.command('build',success=False)
        self.assertIs(self.w.mapping.layers,raw);self.assertEqual(self.w.mapping.revision,rev)
        self.assertIs(self.w.mapping.result,previous)
        self.command('build');self.assertEqual(len(self.w.mapping.layers['source'].features),1)

    def test_separate_revisions_preview_publication_slicing_and_report_provenance(self):
        self.w.execute('default',0);old=self.client.get('/api/scene').json();old_core=_core()
        self.ready();self.command('build',{'preview':True})
        self.assertEqual(self.client.get('/api/scene').json(),old);self.assertEqual(_core(),old_core)
        preview=self.w.mapping.preview.preview_id
        response=self.client.get('/api/mapping/scene',params={'preview_id':preview});self.assertEqual(response.status_code,200)
        p=dict(revision=self.w.mapping.revision,preview_id=preview,value=15)
        self.assertTrue(self.client.post('/api/mapping/slice',json=p).json()['features']['features'])
        self.command('build');self.assertIsNone(self.w.mapping.preview);old_result=self.w.mapping.result
        report=self.client.get('/api/mapping/report').json();saved_groups=deepcopy(report['groups'])
        self.command('review',{'groups':self.w.mapping.groups,'settings':{'source_label':'later edit'}})
        self.assertEqual(self.client.get('/api/mapping/report').json()['groups'],saved_groups)
        self.assertTrue(self.client.get('/api/mapping/scene').json()['stale'])
        stale=self.client.post('/api/mapping/analyze',json=dict(revision=0,parameters={}))
        self.assertEqual(stale.status_code,409)
        self.command('review',{'groups':self.w.mapping.groups,'settings':{'source_z':20,'target_z':15}},success=False)
        self.assertIs(self.w.mapping.result,old_result)
        self.assertEqual(self.client.get('/api/scene').json(),old)

    def test_failed_build_and_cancel_preserve_published_mapping_and_shared_worker(self):
        self.ready();self.command('build');previous=self.w.mapping.result
        self.w.mapping.layers['target']=replace(self.w.mapping.layers['target'],features={'t1':replace(self.w.mapping.layers['target'].features['t1'],geometry=Point(9,9))})
        self.command('review',{'groups':self.w.mapping.groups,'settings':{'mode':'smooth'}})
        self.command('build',success=False);self.assertIs(self.w.mapping.result,previous)
        entered=Event()
        def waiting(*args,**kwargs):
            entered.set()
            while not kwargs['cancelled']():time.sleep(.01)
            raise ValueError('Mapping cancelled; previous model preserved.')
        with patch('mapping_workspace.build_mapping',side_effect=waiting):
            token=self.client.post('/api/mapping/build',json=dict(revision=self.w.mapping.revision,parameters={})).json()['job_id']
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.client.post('/api/load-default',json=dict(revision=self.w.revision)).status_code,409)
            self.assertEqual(self.client.post('/api/jobs/'+token+'/cancel').status_code,200)
            deadline=time.monotonic()+3
            while self.client.get('/api/jobs/'+token).json()['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertIs(self.w.mapping.result,previous)

    def test_mapping_symbol_upload_and_missing_default(self):
        body=gpd.GeoDataFrame(geometry=[Point(2,1)],crs=2180).to_json().encode()
        upload=self.client.post('/api/mapping/uploads',files=[('file',('point.geojson',body,'application/geo+json'))])
        self.assertEqual(upload.status_code,200,upload.text);data=upload.json()
        self.command('load',{'side':'target','upload_id':data['upload_id'],'layer':data['layers'][0],'accept_declared_crs':True})
        self.assertEqual(self.w.mapping.layers['target'].features['t1'].geometry.geom_type,'Point')
        self.w.mapping.paths['source']=Path(self.temp.name)/'missing.shp';self.command('load',{'side':'source'},success=False)
        self.assertIn('target',self.client.get('/api/mapping/state').json()['layers'])


if __name__=='__main__':unittest.main()
