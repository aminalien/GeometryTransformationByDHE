"""Actual web imports, DHE snapshots, API transactions, and plane sections."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
import importlib.util
from unittest.mock import patch
from zipfile import ZipFile

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.geometry import box,Polygon,MultiPolygon,Point,shape
from shapely.ops import unary_union

import DHECore as dhe
from generalization_model import GeneralizationSession
from plane_slicing import PlaneSlicer,SlicePlane
from web_generalization_data import prepare_web_frame,UploadStore,DEFAULT_WEB_DATA,fetch_web_osm
from web_generalization_scene import WebScene,display_triangles
if any(importlib.util.find_spec(name) is None for name in ('fastapi','httpx','multipart')):
    raise unittest.SkipTest('Install requirements-web.txt to run web API tests.')
from web_generalization_app import Workspace,create_app,GoogleTiles,RevisionConflict
from fastapi.testclient import TestClient


def frame(polygons=None):
    polygons=polygons or [box(0,0,10,5),box(14,0,24,5),box(30,0,40,5)]
    return gpd.GeoDataFrame({'osm_id':[f'input-{i}' for i in range(1,len(polygons)+1)]},geometry=polygons,crs=2180)


class WebImportTests(unittest.TestCase):
    def test_direct_shapefile_crs_preflight_and_explicit_override(self):
        store=UploadStore()
        try:
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'wrong.shp'
                raw=gpd.GeoDataFrame({'osm_id':['one']},geometry=[box(17.01,51.13,17.0101,51.1301)],crs=3857)
                raw.to_file(path)
                files=[(p.name,p.read_bytes()) for p in path.parent.glob('wrong.*')]
                original={name:payload for name,payload in files}
                with self.assertRaisesRegex(ValueError,'Missing Shapefile companions'):store.add_files([(path.name,path.read_bytes())])
                entry=store.add_files(files);layer=entry['layers'][0];metadata=entry['metadata'][layer]
                self.assertEqual(metadata['crs'],'EPSG:3857');self.assertTrue(metadata['suspicious'])
                self.assertEqual(metadata['suggested_crs'],'EPSG:4326')
                with self.assertRaisesRegex(ValueError,'Suspicious declared CRS'):store.read(entry['upload_id'],layer)
                corrected=store.read(entry['upload_id'],layer,override_crs='EPSG:4326')
                self.assertEqual(corrected.frame.crs.to_epsg(),32633)
                self.assertGreater(corrected.frame.geometry.iloc[0].area,50)
                self.assertTrue(any('override' in n for n in corrected.rejected))
                retained=store.read(entry['upload_id'],layer,accept_declared_crs=True)
                self.assertEqual(retained.frame.crs.to_epsg(),3857)
                self.assertAlmostEqual(retained.frame.geometry.iloc[0].area,raw.geometry.iloc[0].area)
                self.assertEqual(original,{p.name:p.read_bytes() for p in path.parent.glob('wrong.*')})
        finally:store.close()

    def test_osm_network_fallback_preserves_geometry_errors(self):
        from types import SimpleNamespace
        data=SimpleNamespace(frame=frame(),source='OSM',rejected=[])
        with patch('web_generalization_data.download_osm',side_effect=[ValueError('OSM download failed: HTTP Error 502'),data]) as download:
            result=fetch_web_osm([17,51,17.01,51.01]);self.assertEqual(download.call_count,2)
            self.assertEqual(len(result.frame),3);self.assertTrue(any('fallback' in note for note in result.rejected))
        with patch('web_generalization_data.download_osm',side_effect=ValueError('Bounding box is invalid.')) as download:
            with self.assertRaisesRegex(ValueError,'Bounding box'):fetch_web_osm([1,2,0,3])
            self.assertEqual(download.call_count,1)

    def test_overlap_chain_unions_all_sources_but_keeps_touching_features(self):
        raw=frame([box(0,0,4,4),box(3,0,7,4),box(6,0,10,4),box(0,4,4,6)])
        data=prepare_web_frame(raw);self.assertEqual(list(data.frame['_index']),[1,4])
        a=data.frame.iloc[0];self.assertEqual(a.geometry.area,40)
        self.assertEqual([m['id'] for m in a['source_members']],['input-1','input-2','input-3'])
        self.assertEqual(a['osm_id'],'input-1');self.assertTrue(any('Auto-union' in n for n in data.rejected))
        self.assertEqual(len(GeneralizationSession(data).model.cells),2)
        self.assertEqual(len(raw),4);self.assertEqual(raw.geometry.iloc[1].area,16)

    def test_multipart_missing_crs_and_unsupported_hole_results(self):
        raw=frame([MultiPolygon([box(0,0,2,2),box(5,0,7,2)]),Point(20,20)])
        data=prepare_web_frame(raw);self.assertEqual(set(data.frame['_index']),{1,3})
        self.assertEqual([m['component'] for _,r in data.frame.iterrows() for m in r.source_members],[1,2])
        raw=frame([box(0,0,2,2)]).set_crs(None,allow_override=True)
        with self.assertRaisesRegex(ValueError,'no CRS'):prepare_web_frame(raw)
        self.assertEqual(prepare_web_frame(raw,crs='EPSG:2180').frame.crs.to_epsg(),2180)
        ring=frame([box(0,0,4,1),box(3,0,4,4),box(0,3,4,4),box(0,0,1,4)])
        with self.assertRaisesRegex(ValueError,'holed'):prepare_web_frame(ring)

    def test_upload_formats_layer_selection_and_unsafe_archive(self):
        store=UploadStore()
        try:
            geojson=frame().to_crs(4326).to_json().encode()
            entry=store.add('my.geojson',geojson);data=store.read(entry['upload_id'],entry['layers'][0])
            self.assertEqual(len(data.frame),3)
            with tempfile.TemporaryDirectory() as temp:
                folder=Path(temp);shape_path=folder/'footprints.shp';frame().to_file(shape_path)
                stream=BytesIO()
                with ZipFile(stream,'w') as archive:
                    for file in folder.iterdir():archive.write(file,'nested/'+file.name)
                entry=store.add('footprints.zip',stream.getvalue())
                self.assertEqual(len(store.read(entry['upload_id'],entry['layers'][0],accept_declared_crs=True).frame),3)
                gpkg=folder/'layers.gpkg';frame().to_file(gpkg,layer='one');frame([box(60,0,70,5)]).to_file(gpkg,layer='two')
                entry=store.add('layers.gpkg',gpkg.read_bytes());self.assertEqual(set(entry['layers']),{'one','two'})
                self.assertEqual(len(store.read(entry['upload_id'],'two',accept_declared_crs=True).frame),1)
            stream=BytesIO()
            with ZipFile(stream,'w') as archive:archive.writestr('../outside.shp','bad')
            with self.assertRaisesRegex(ValueError,'Unsafe'):store.add('unsafe.zip',stream.getvalue())
            with self.assertRaisesRegex(ValueError,'missing'):store.read('unknown','layer')
        finally:store.close()


class SceneTests(unittest.TestCase):
    def test_concave_and_vertical_tessellation_is_display_only(self):
        for points,area in [([(0,0,0),(4,0,0),(4,1,0),(1,1,0),(1,4,0),(0,4,0)],7),
                            ([(2,0,0),(2,4,0),(2,4,5),(2,0,5)],20)]:
            triangles=display_triangles(points)
            measured=sum(np.linalg.norm(np.cross(np.subtract(t[1],t[0]),np.subtract(t[2],t[0])))/2 for t in triangles)
            self.assertAlmostEqual(measured,area);self.assertTrue(all(tuple(p) in points for t in triangles for p in t))

    def test_primal_dual_georeferencing_and_sections_are_actual_and_read_only(self):
        s=GeneralizationSession(prepare_web_frame(frame([box(0,0,2,2),box(2,0,4,2)])))
        ledger=tuple(dhe.dhe);counts=(dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        with patch.multiple(dhe,**{name:unittest.mock.Mock(side_effect=AssertionError('Read-only scene mutated DHE')) for name in ('MEVVFS','MEV','MEF','MVE','Sew','JoinByFace')}):
            scene=WebScene(s,1);section=scene.section('Horizontal',2.5)
        self.assertEqual(tuple(dhe.dhe),ledger);self.assertEqual((dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),counts)
        self.assertEqual(scene.payload['cells'],2)
        self.assertEqual(sum(not e['exterior'] for e in scene.payload['dual_edges']),len(s.model.shared_walls))
        inverse=Transformer.from_crs(4326,s.data.frame.crs,always_xy=True)
        p=scene.payload['vertices'][0]['position'];x,y=inverse.transform(p[0],p[1])
        actual=s.model.active_edges()[0].V
        self.assertAlmostEqual(x-s.origin[0],actual.x,places=6);self.assertAlmostEqual(y-s.origin[1],actual.y,places=6)
        self.assertEqual(len(section['features']['features']),2)
        allids={e['id'] for e in scene.payload['halfedges']}
        self.assertTrue(all(e['nf'] in allids and e['s'] in allids for e in scene.payload['halfedges']))
        json.dumps(scene.payload,allow_nan=False)

    def test_all_operations_and_three_plane_orientations_match_real_dhe(self):
        for operation in ('simplification','smoothing','point','polyline','merging','aggregation'):
            with self.subTest(operation=operation):
                polygons=[box(0,0,10,5),box(10 if operation=='merging' else 14,0,20 if operation=='merging' else 24,5),box(30,0,40,5)]
                s=GeneralizationSession(prepare_web_frame(frame(polygons)))
                selected=[1,2] if operation in ('merging','aggregation') else [1]
                s.apply(s.preview(operation,selected,winner=1 if operation=='merging' else None))
                scene=WebScene(s,2);live=PlaneSlicer(s.model)
                for orientation,value,tilt,azimuth in [('Horizontal',10,45,0),('Vertical',0,90,20),('Inclined',0,45,50)]:
                    answer=scene.section(orientation,value,tilt,azimuth)
                    plane=SlicePlane.horizontal(value) if orientation=='Horizontal' else SlicePlane.from_angles(live.center,tilt,azimuth,value)
                    expected=live.sections(plane,include_degenerate=True)
                    self.assertEqual({int(k) for k in expected},{f['properties']['owner'] for f in answer['features']['features']})
                    inverse=Transformer.from_crs(4326,s.data.frame.crs,always_xy=True)
                    for f in answer['features']['features']:
                        arrays=f['geometry']['coordinates']
                        points=arrays if f['geometry']['type']=='LineString' else [arrays] if f['geometry']['type']=='Point' else [p for ring in arrays for p in ring]
                        for lon,lat,z in points:
                            x,y=inverse.transform(lon,lat);local=[x-s.origin[0],y-s.origin[1],z]
                            self.assertAlmostEqual(float(plane.signed_distance(local)),0,places=5)
                terminal=scene.section('Horizontal',15)
                if operation in ('point','polyline'):
                    types={f['geometry']['type'] for f in terminal['features']['features'] if f['properties']['owner']==1}
                    self.assertIn('Point' if operation=='point' else 'LineString',types)
                if operation=='aggregation':self.assertEqual(scene.payload['cells'],4)

    def test_missing_later_terminal_is_disclosed_without_synthesizing_geometry(self):
        s=GeneralizationSession(prepare_web_frame(frame()))
        s.apply(s.preview('point',[1]));s.apply(s.preview('smoothing',[3]))
        scene=WebScene(s,3);answer=scene.section('Horizontal',25)
        self.assertEqual(answer['missing'],[1])
        self.assertNotIn(1,{f['properties']['owner'] for f in answer['features']['features']})
        self.assertIn(1,{f['properties']['index'] for f in scene.payload['features']['features']})


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.workspace=Workspace();self.app=create_app(workspace=self.workspace,autoload=False)
        self.client=TestClient(self.app);self.client.__enter__()

    def tearDown(self):self.client.__exit__(None,None,None)

    def job(self,path,body):
        response=self.client.post(path,json=dict(revision=self.workspace.revision,**body));self.assertEqual(response.status_code,200,response.text)
        token=response.json()['job_id'];deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            data=self.client.get('/api/jobs/'+token).json()
            if data['status']!='running':return data
            time.sleep(.01)
        self.fail('Job did not finish.')

    def load(self,polygons=None):
        with patch('web_generalization_app.read_web_dataset',return_value=prepare_web_frame(frame(polygons))):
            answer=self.job('/api/load-default',{});self.assertEqual(answer['status'],'complete',answer)

    def test_prepared_preview_progress_last_failure_and_hidden_sections(self):
        self.load();w=self.workspace
        token=list(w.jobs)[-1]
        job=self.client.get('/api/jobs/'+token).json()
        self.assertTrue(job['has_input_preview']);self.assertNotIn('input_preview',job)
        preview=self.client.get('/api/jobs/'+token+'/input-preview').json()
        self.assertEqual(len(preview['features']),3)
        self.assertGreater(preview['features'][0]['geometry']['coordinates'][0][0][0],-180)
        with patch.object(w.scene.slicer,'sections',side_effect=AssertionError('Hidden sections should not be computed')):
            for orientation in ('Horizontal','Vertical','Inclined'):
                response=self.client.post('/api/slice',json=dict(revision=1,orientation=orientation,value=0,include_sections=False))
                self.assertEqual(response.status_code,200,response.text);answer=response.json()
                self.assertFalse(answer['sections_computed']);self.assertEqual(answer['features']['features'],[])
                self.assertTrue(answer['outline']);self.assertEqual(answer['missing'],[])
        self.assertEqual(self.client.post('/api/slice',json=dict(revision=1,orientation='Vertical',value=1e6)).status_code,400)
        clamped=self.client.post('/api/slice',json=dict(revision=1,orientation='Vertical',value=1e6,clamp_position=True,include_sections=False)).json()
        self.assertEqual(clamped['value'],clamped['bounds'][1])
        with patch('web_generalization_app.fetch_web_osm',side_effect=ValueError('OSM network test failure')):
            failed=self.job('/api/osm',dict(bounds=[17,51,17.01,51.01]))
        self.assertEqual(failed['status'],'failed');current=self.client.get('/api/state').json()
        self.assertTrue(current['loaded']);self.assertEqual(current['revision'],1)
        self.assertIn('OSM network',current['recent_jobs'][0]['error']);self.assertNotIn('input_preview',current['recent_jobs'][0])

    def test_input_preview_available_before_atomic_replacement(self):
        self.load();original_scene=self.workspace.scene;original_session=self.workspace.session
        entered=Event();release=Event()
        def delayed(data,height):
            entered.set();release.wait(10);return GeneralizationSession(data,height)
        try:
            with patch('web_generalization_app.GeneralizationSession',side_effect=delayed),patch('web_generalization_app.read_web_dataset',return_value=prepare_web_frame(frame([box(1000,1000,1004,1004)]))):
                token=self.client.post('/api/load-default',json=dict(revision=1)).json()['job_id']
                self.assertTrue(entered.wait(5))
                self.assertIs(self.workspace.scene,original_scene);self.assertIs(self.workspace.session,original_session)
                job=self.client.get('/api/jobs/'+token).json()
                self.assertTrue(job['has_input_preview']);self.assertIn('Constructing',job['stage'])
                self.assertEqual(len(self.client.get('/api/jobs/'+token+'/input-preview').json()['features']),1)
                self.assertEqual(self.client.get('/api/scene').json()['cells'],3)
                release.set();deadline=time.monotonic()+10
                while self.client.get('/api/jobs/'+token).json()['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(self.client.get('/api/jobs/'+token).json()['status'],'complete')
                self.assertEqual(self.client.get('/api/scene').json()['cells'],1)
                self.assertEqual(self.workspace.revision,2)
        finally:release.set()

    def test_direct_multipart_shapefile_api(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'small.shp';frame().to_file(path)
            files=[('file',(p.name,p.read_bytes(),'application/octet-stream')) for p in path.parent.glob('small.*')]
            entry=self.client.post('/api/uploads',files=files)
            self.assertEqual(entry.status_code,200,entry.text);entry=entry.json()
            self.assertTrue(entry['metadata'][entry['layers'][0]]['suspicious'])
            failed=self.job('/api/load-upload',dict(upload_id=entry['upload_id'],layer=entry['layers'][0]))
            self.assertEqual(failed['status'],'failed');self.assertFalse(self.workspace.scene)
            loaded=self.job('/api/load-upload',dict(upload_id=entry['upload_id'],layer=entry['layers'][0],accept_declared_crs=True))
            self.assertEqual(loaded['status'],'complete',loaded)
            self.assertEqual(len(self.workspace.session.model.cells),3)

    def test_joint_level_cache_duplicates_history_and_serialization(self):
        self.load()
        body=dict(actions=[dict(operation='point',selected=[1]),dict(operation='smoothing',selected=[3])],dz=10)
        with patch.object(self.workspace.session,'preview_level',wraps=self.workspace.session.preview_level) as build:
            self.assertEqual(self.job('/api/preview-level',body)['status'],'complete')
            self.assertEqual(self.workspace.revision,1)
            self.assertEqual(self.job('/api/apply-level',body)['status'],'complete')
            self.assertEqual(build.call_count,1)
        scene=self.client.get('/api/scene').json()
        self.assertEqual(scene['operation']['name'],'batch');self.assertEqual(len(scene['operation']['actions']),2)
        self.assertEqual(scene['levels']['z_t'],15);self.assertEqual(len(scene['history']),2)
        revision=self.workspace.revision;state=self.workspace.session.state
        bad=self.job('/api/apply-level',dict(actions=[dict(operation='point',selected=[2]),dict(operation='smoothing',selected=[2])]))
        self.assertEqual(bad['status'],'failed');self.assertEqual(self.workspace.revision,revision);self.assertIs(self.workspace.session.state,state)
        self.assertEqual(self.job('/api/history',dict(index=0))['status'],'complete')

    def test_automatic_final_only_publication_and_failed_final_scene_rollback(self):
        self.load([box(0,0,5,5),box(8,0,13,5),box(30,0,35,5)])
        base=self.workspace.session.state;revision=self.workspace.revision
        original_scene=self.workspace.scene
        with patch('web_generalization_app.WebScene',side_effect=ValueError('scene failure before publication')):
            result=self.job('/api/automatic',dict(selected=[1,2,3],settings=dict(final_kind='polygon')))
        self.assertEqual(result['status'],'failed');self.assertIs(self.workspace.session.state,base)
        self.assertIs(self.workspace.scene,original_scene);self.assertEqual(self.workspace.revision,revision)
        result=self.job('/api/automatic',dict(selected=[1,2,3],settings=dict(final_kind='polygon')))
        self.assertEqual(result['status'],'complete',result)
        self.assertEqual(self.workspace.revision,revision+1)
        scene=self.client.get('/api/scene').json()
        self.assertEqual(len(scene['features']['features']),1);self.assertEqual(scene['automatic_report']['status'],'complete')
        self.assertEqual(len(scene['history']),len(result['result']['automatic_report']['steps'])+1)
        self.assertEqual(self.job('/api/history',dict(index=0))['status'],'complete')
        self.assertEqual(len(self.client.get('/api/scene').json()['features']['features']),3)

    def test_automatic_cancel_preserves_loaded_scene(self):
        self.load([box(0,0,5,5),box(8,0,13,5)])
        entered=Event();release=Event();base=self.workspace.session.state;revision=self.workspace.revision
        from automatic_generalization import build_automatic_run
        def delayed(*args,**kwargs):
            entered.set();release.wait(10);return build_automatic_run(*args,**kwargs)
        try:
            with patch('automatic_generalization.build_automatic_run',side_effect=delayed):
                token=self.client.post('/api/automatic',json=dict(revision=revision,selected=[1,2])).json()['job_id']
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.client.get('/api/state').json()['revision'],revision)
                self.assertEqual(len(self.client.get('/api/scene').json()['features']['features']),2)
                self.assertEqual(self.client.post('/api/jobs/'+token+'/cancel',json={}).status_code,200)
                release.set();deadline=time.monotonic()+15
                while self.client.get('/api/jobs/'+token).json()['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(self.client.get('/api/jobs/'+token).json()['status'],'failed')
        finally:release.set()
        self.assertEqual(self.workspace.revision,revision);self.assertIs(self.workspace.session.state,base)

    def test_preview_apply_cache_undo_failed_load_and_stale_requests(self):
        self.load();w=self.workspace;source=w.session.state
        params=dict(operation='smoothing',selected=[1],dz=10,tolerance=1,iterations=1,ratio=.25,winner=None)
        with patch.object(w.session,'preview',wraps=w.session.preview) as build:
            preview=self.job('/api/preview',params);self.assertEqual(preview['status'],'complete')
            token=preview['result']['preview_id'];self.assertIs(w.session.state,source);self.assertEqual(w.revision,1)
            self.assertTrue(self.client.get('/api/scene?preview_id='+token).json()['preview'])
            self.assertFalse(self.client.get('/api/scene').json()['preview'])
            answer=self.job('/api/apply',params);self.assertEqual(answer['status'],'complete',answer);self.assertEqual(build.call_count,1)
        self.assertEqual(w.session.cursor,1);self.assertEqual(w.revision,2)
        scene=self.client.get('/api/scene').json();self.assertEqual(scene['cursor'],1);self.assertEqual(len(scene['history']),2)
        self.assertEqual(self.client.post('/api/history',json=dict(revision=1,index=0)).status_code,409)
        self.assertEqual(self.client.post('/api/slice',json=dict(revision=2,orientation='Horizontal',value=10)).status_code,200)
        ledger=tuple(dhe.dhe)
        with patch('web_generalization_app.read_web_dataset',side_effect=ValueError('Bad input')):
            failed=self.job('/api/load-default',{});self.assertEqual(failed['status'],'failed')
        self.assertEqual(w.revision,2);self.assertEqual(tuple(dhe.dhe),ledger)
        answer=self.job('/api/history',dict(index=0));self.assertEqual(answer['status'],'complete');self.assertEqual(w.session.cursor,0)
        self.assertEqual(self.client.get('/').status_code,200);self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertNotIn('google_key',self.client.get('/api/config').json())

    def test_upload_osm_selection_and_invalid_values(self):
        payload=frame([box(0,0,4,4),box(3,0,7,4)]).to_json().encode()
        uploaded=self.client.post('/api/uploads',files={'file':('data.geojson',payload,'application/geo+json')}).json()
        result=self.job('/api/load-upload',dict(upload_id=uploaded['upload_id'],layer=uploaded['layers'][0],accept_declared_crs=True))
        self.assertEqual(result['status'],'complete',result);self.assertEqual(len(self.workspace.session.state.features),1)
        with patch('web_generalization_app.fetch_web_osm',return_value=prepare_web_frame(frame())):
            self.assertEqual(self.job('/api/osm',dict(bounds=[17,51,17.01,51.01]))['status'],'complete')
        scene=self.client.get('/api/scene').json()
        selected=self.client.post('/api/select-box',json=dict(revision=self.workspace.revision,bounds=scene['bounds']))
        self.assertEqual(set(selected.json()['indices']),{1,2,3})
        self.assertEqual(self.client.post('/api/apply',json=dict(revision=self.workspace.revision,operation='smoothing',selected=[1],dz=-2)).status_code,422)
        self.assertEqual(self.client.post('/api/slice',json=dict(revision=self.workspace.revision,orientation='invalid',value=0)).status_code,400)

    def test_only_one_mutation_job_can_run(self):
        entered=Event();release=Event();original=self.workspace.execute
        def delayed(*args,**kwargs):entered.set();release.wait(10);return original(*args,**kwargs)
        try:
            with patch.object(self.workspace,'execute',side_effect=delayed),patch('web_generalization_app.read_web_dataset',return_value=prepare_web_frame(frame())):
                token=self.client.post('/api/load-default',json=dict(revision=0)).json()['job_id'];self.assertTrue(entered.wait(5))
                self.assertEqual(self.client.post('/api/load-default',json=dict(revision=0)).status_code,409)
                release.set();deadline=time.monotonic()+20
                while self.client.get('/api/jobs/'+token).json()['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(self.client.get('/api/jobs/'+token).json()['status'],'complete')
        finally:release.set()

    @unittest.skipUnless(Path(DEFAULT_WEB_DATA).is_file(),'Local default dataset not available')
    def test_requested_default_dataset_and_actual_thirty_two_cells(self):
        answer=self.job('/api/load-default',{});self.assertEqual(answer['status'],'complete',answer)
        scene=self.client.get('/api/scene').json();self.assertEqual(scene['cells'],32);self.assertEqual(len(scene['features']['features']),32)


class GoogleAdapterTests(unittest.TestCase):
    def test_session_expiry_tile_headers_attribution_and_no_key(self):
        provider=GoogleTiles('test-key')
        def request(path,params,body=None):
            if path=='v1/createSession':return json.dumps(dict(session='session',expiry=str(time.time()+3600))).encode(),{}
            if path=='tile/v1/viewport':return b'{"copyright":"Test provider"}',{}
            return b'PNG',{'Content-Type':'image/png','Cache-Control':'private, max-age=3600'}
        with patch.object(provider,'request',side_effect=request) as network:
            content,headers=provider.tile('roadmap',1,0,0);self.assertEqual(content,b'PNG');self.assertIn('private',headers['cache-control'])
            self.assertEqual(provider.attribution('roadmap',1,52,51,18,17)['copyright'],'Test provider')
            self.assertEqual(sum(c.args[0]=='v1/createSession' for c in network.call_args_list),1)
        with self.assertRaisesRegex(ValueError,'GOOGLE_MAPS_API_KEY'):GoogleTiles('').session('roadmap')


if __name__=='__main__':unittest.main()
