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
from shapely.affinity import rotate,translate
from shapely.ops import unary_union

import DHECore as dhe
from generalization_model import _core,Face,_node_faces
from mapping_data import (prepare_layer,reproject_layer,overlap_review,resolve_overlap,
                          suggest_groups,validate_groups,route_group,union_overlaps,automatic_review)
from mapping_builders import build_mapping,MappingBuildError
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
               (group(['s1'],['t1']),'smooth'),(group([],['t2'],'appearance'),'classic')]
        for g,expected in cases:self.assertEqual(route_group(g,s,t,'hybrid')[0],expected)
        self.assertEqual(route_group(cases[2][0],s,t,'smooth')[0],'unsupported')


class MappingBuilderTests(unittest.TestCase):
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
        with self.assertRaisesRegex(MappingBuildError,'Smooth-only cannot preserve polygon holes'):
            build([box(0,0,4,4)],[donut],mode='smooth')

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
        self.assertEqual(len(r.model.shared_walls),0)

    def test_collisions_overlaps_and_cancellation_never_publish_bad_geometry(self):
        ss,ts=layers([box(0,0,2,2)],[box(0,0,2,2)]);groups=[group(['s1'],['t1'])]
        before=_core()
        with self.assertRaises(ValueError):build_mapping(ss,ts,groups,{},cancelled=lambda:True)
        self.assertEqual(_core(),before)
        with self.assertRaises(MappingBuildError):build([box(0,0,2,2),box(1,0,3,2)],[box(0,0,3,2)])
        with self.assertRaises(MappingBuildError):
            build([box(0,0,2,2),box(6,0,8,2)],[box(6,0,8,2),box(0,0,2,2)],mode='smooth',
                  groups=[group(['s1'],['t1'],id='a'),group(['s2'],['t2'],id='b')])


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
