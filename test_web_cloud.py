"""Visitor isolation, defaults, Mapping replacement and deployment data integrity."""
from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

import geopandas as gpd
from fastapi import HTTPException
from fastapi.testclient import TestClient
from shapely.geometry import box,Point,Polygon,MultiPolygon

from mapping_data import prepare_layer
from mapping_workspace import MappingWorkspace
from web_generalization_app import Workspace,create_app,create_cloud_app
from web_sessions import WorkspaceRegistry
from web_generalization_data import fetch_mapping_osm
from scripts.prepare_demo_data import prepare
from scripts.fetch_demo_data import install


def frame(size=10):
    return gpd.GeoDataFrame({'osm_id':['one','two']},geometry=[box(500000,5600000,500000+size,5600010),
                            box(500020,5600000,500030,5600010)],crs=32633)


def wait(client,token):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        response=client.get('/api/jobs/'+token)
        if response.status_code!=200:raise AssertionError(response.text)
        job=response.json()
        if job['status']!='running':return job
        time.sleep(.01)
    raise AssertionError('Construction job did not finish.')


class CloudAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.source=self.folder/'osm_data1.shp';self.target=self.folder/'citygml_data1.shp'
        frame().to_file(self.source);frame(9).to_file(self.target)
        self.app=create_app(default_path=self.source,mapping_paths=dict(source=self.source,target=self.target),
                            isolated=True,secure_cookie=True)
        self.client=TestClient(self.app,base_url='https://testserver');self.client.__enter__()
        self.other=TestClient(self.app,base_url='https://testserver')

    def tearDown(self):
        self.other.close();self.client.__exit__(None,None,None);self.temp.cleanup()

    def initialize(self,client):
        response=client.get('/api/config');self.assertEqual(response.status_code,200,response.text)
        state=client.get('/api/state').json()
        if state['job_id']:self.assertEqual(wait(client,state['job_id'])['status'],'complete')
        return response

    def test_defaults_cookie_refresh_and_independent_visitors(self):
        self.assertEqual(self.client.get('/healthz').json(),{'status':'ok'})
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertFalse(self.app.state.registry.entries)
        response=self.initialize(self.client);self.initialize(self.other)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotEqual(response.headers['x-dhe-workspace'],self.client.cookies.get('dhe_visitor'))
        cookie=response.headers['set-cookie'].lower()
        for flag in ('httponly','secure','samesite=lax'):self.assertIn(flag,cookie)
        a=self.client.get('/api/scene').json();b=self.other.get('/api/scene').json()
        self.assertNotEqual(a['snapshot_id'],b['snapshot_id'])
        self.assertEqual(self.client.get('/api/scene').json()['snapshot_id'],a['snapshot_id'])
        mapping=self.client.get('/api/mapping/state').json()
        self.assertEqual(mapping['layers']['source']['name'],'osm_data1.shp')
        self.assertEqual(mapping['layers']['target']['name'],'citygml_data1.shp')
        self.assertEqual(len(mapping['layers']),2);self.assertTrue(mapping['groups'])
        self.assertFalse(mapping['has_result'])
        result=self.client.post('/api/load-default',json=dict(revision=a['revision'],initial_height=7)).json()
        self.assertEqual(wait(self.client,result['job_id'])['status'],'complete')
        self.assertEqual(self.other.get('/api/jobs/'+result['job_id']).status_code,404)
        self.assertEqual(self.other.post('/api/jobs/'+result['job_id']+'/cancel',json={}).status_code,404)
        self.assertEqual(self.other.get('/api/jobs/'+result['job_id']+'/input-preview').status_code,404)
        self.assertEqual(self.other.get('/api/scene').json()['snapshot_id'],b['snapshot_id'])
        self.assertNotEqual(self.client.get('/api/scene').json()['snapshot_id'],a['snapshot_id'])
        query=dict(workspace_name='generalize',snapshot_id=a['snapshot_id'])
        self.assertEqual(self.other.get('/api/export/primal',params=query).status_code,409)
        current=self.client.get('/api/scene').json()
        self.assertEqual(self.client.get('/api/export/primal',params={**query,'snapshot_id':current['snapshot_id']}).status_code,200)

    def test_upload_mapping_upload_and_job_tokens_are_session_scoped(self):
        self.initialize(self.client);self.initialize(self.other)
        for upload_path,load_path in (('/api/uploads','/api/load-upload'),('/api/mapping/uploads','/api/mapping/load')):
            entry=self.client.post(upload_path,files={'file':('replacement.geojson',frame(8).to_json().encode(),'application/geo+json')}).json()
            values=dict(upload_id=entry['upload_id'],layer=entry['layers'][0],accept_declared_crs=True)
            if 'mapping' in upload_path:
                revision=self.other.get('/api/mapping/state').json()['revision']
                payload=dict(revision=revision,parameters=dict(side='source',**values))
            else:payload=dict(revision=self.other.get('/api/state').json()['revision'],**values)
            answer=self.other.post(load_path,json=payload).json()
            self.assertEqual(wait(self.other,answer['job_id'])['status'],'failed')
            # The owning browser can still use its upload after the rejected access.
            answer=self.client.post(load_path,json=payload).json()
            self.assertEqual(wait(self.client,answer['job_id'])['status'],'complete')

    def test_capacity_and_expiry_start_fresh_defaults(self):
        self.initialize(self.client);self.initialize(self.other)
        third=TestClient(self.app,base_url='https://testserver')
        fourth=TestClient(self.app,base_url='https://testserver')
        try:
            self.initialize(third)
            response=fourth.get('/api/config');self.assertEqual(response.status_code,503)
            self.assertEqual(response.headers['retry-after'],'60')
            token=self.client.cookies.get('dhe_visitor');old=self.client.get('/api/scene').json()['snapshot_id']
            identity=self.client.get('/api/state').headers['x-dhe-workspace']
            self.app.state.registry.entries[token].touched-=1801
            self.app.state.registry.prune();self.assertNotIn(token,self.app.state.registry.entries)
            self.initialize(fourth)
            # Release fourth so the expired browser can enter again.
            entry=self.app.state.registry.entries[fourth.cookies.get('dhe_visitor')];entry.touched-=1801
            self.app.state.registry.prune();self.initialize(self.client)
            self.assertNotEqual(self.client.get('/api/scene').json()['snapshot_id'],old)
            self.assertNotEqual(self.client.get('/api/state').headers['x-dhe-workspace'],identity)
        finally:third.close();fourth.close()

    def test_mapping_swap_and_osm_are_atomic_and_leave_generalize_alone(self):
        self.initialize(self.client)
        original=self.client.get('/api/scene').json()['snapshot_id']
        before=self.client.get('/api/mapping/state').json()
        answer=self.client.post('/api/mapping/swap',json=dict(revision=before['revision'],parameters={})).json()
        self.assertEqual(wait(self.client,answer['job_id'])['status'],'complete')
        after=self.client.get('/api/mapping/state').json()
        self.assertEqual(after['layers']['source']['name'],before['layers']['target']['name'])
        self.assertEqual(after['layers']['target']['name'],before['layers']['source']['name'])
        self.assertTrue(all(f['properties']['uid'].startswith('s') for f in after['layers']['source']['features']['features']))
        with patch('mapping_workspace.fetch_mapping_osm',return_value=(frame(6),[])):
            answer=self.client.post('/api/mapping/osm',json=dict(revision=after['revision'],parameters=dict(side='source',bounds=[17,51,17.01,51.01]))).json()
            self.assertEqual(wait(self.client,answer['job_id'])['status'],'complete')
        after=self.client.get('/api/mapping/state').json()
        self.assertIn('OpenStreetMap',after['layers']['source']['name'])
        with patch('mapping_workspace.fetch_mapping_osm',side_effect=ValueError('Provider unavailable')):
            answer=self.client.post('/api/mapping/osm',json=dict(revision=after['revision'],parameters=dict(side='target',bounds=[17,51,17.01,51.01]))).json()
            self.assertEqual(wait(self.client,answer['job_id'])['status'],'failed')
        self.assertEqual(self.client.get('/api/mapping/state').json(),after)
        self.assertEqual(self.client.get('/api/scene').json()['snapshot_id'],original)
        self.assertEqual(self.client.post('/api/mapping/osm',json=dict(revision=after['revision'],parameters=dict(side='target',bounds=[1,2]))).status_code,422)


class WorkerAndMappingTests(unittest.TestCase):
    def test_mapping_osm_keeps_holes_multipart_and_original_ids(self):
        ring=Polygon([(0,0),(10,0),(10,10),(0,10)],holes=[[(2,2),(2,3),(3,3),(3,2)]])
        raw=gpd.GeoDataFrame({'osm_id':['relation/1','relation/2']},geometry=[ring,MultiPolygon([box(20,0,22,2),box(24,0,26,2)])],crs=2180)
        with patch('generalization_geometry.fetch_osm_frame',side_effect=[ValueError('OSM download failed: unavailable'),(raw,[])]) as download:
            result,notes=fetch_mapping_osm([17,51,17.01,51.01])
            self.assertEqual(download.call_count,2);self.assertIs(result,raw)
            layer=prepare_layer(result,'source','OSM')
            self.assertEqual(layer.features['s1'].original_id,'relation/1')
            self.assertEqual(len(layer.features['s1'].geometry.interiors),1)
            self.assertEqual(layer.features['s2'].geometry.geom_type,'MultiPolygon')
        with patch('generalization_geometry.fetch_osm_frame',side_effect=ValueError('Bounding box must be increasing')) as download:
            with self.assertRaisesRegex(ValueError,'Bounding box'):fetch_mapping_osm([1,2,0,3])
            self.assertEqual(download.call_count,1)

    def test_shared_worker_serializes_visitors_and_expiry_protects_busy_and_active_requests(self):
        now=[0.];started=Event();release=Event();second=Event()
        registry=WorkspaceRegistry(lambda executor:Workspace(executor=executor),idle_seconds=10,clock=lambda:now[0])
        def work(w,action,revision,**parameters):
            if not started.is_set():started.set();release.wait(5)
            else:second.set()
            return dict(revision=0)
        try:
            with patch.object(Workspace,'execute',work):
                a,wa=registry.acquire(None);b,wb=registry.acquire(None)
                self.assertIs(wa.pool,wb.pool);self.assertTrue(started.wait(2))
                registry.release(b);now[0]=11;registry.prune()
                self.assertEqual(len(registry.entries),2);self.assertFalse(second.is_set())
                release.set();wa.pool.submit(lambda:None).result(timeout=5)
                registry.prune()
                self.assertIn(a,registry.entries);self.assertNotIn(b,registry.entries)
                registry.release(a);now[0]=22;registry.prune();self.assertFalse(registry.entries)
        finally:release.set();registry.close()

    def test_swap_twice_preserves_provenance_and_rejects_nonpolygon_source(self):
        m=MappingWorkspace()
        try:
            m.layers={'source':prepare_layer(frame(),'source','source'),
                      'target':prepare_layer(frame(8),'target','target')}
            m.input_layers=m.layers.copy();m.settings.update(source_label='OSM',target_label='CityGML')
            original=deepcopy(m.layers)
            for _ in range(2):m.execute('swap',m.revision)
            for side in original:
                for key,feature in original[side].features.items():
                    self.assertEqual(m.layers[side].features[key],feature)
            self.assertEqual(m.settings['source_label'],'OSM');self.assertEqual(m.settings['source_z'],5)
            point=gpd.GeoDataFrame({'id':['p']},geometry=[Point(500000,5600000)],crs=32633)
            m.layers['target']=prepare_layer(point,'target','point')
            revision=m.revision
            with self.assertRaisesRegex(ValueError,'polygon targets'):m.execute('swap',revision)
            self.assertEqual(m.revision,revision)
        finally:m.close()


class DemoDataTests(unittest.TestCase):
    def test_package_roundtrip_and_corruption_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);frame().to_file(folder/'osm_data1.shp');frame(8).to_file(folder/'citygml_data1.shp')
            manifest=prepare(folder,folder/'data.zip',folder/'manifest.json')
            payload=(folder/'data.zip').read_bytes();destination=folder/'installed'
            install(payload,manifest,destination)
            self.assertEqual(len(manifest['files']),10)
            for name in manifest['files']:self.assertEqual((folder/name).read_bytes(),(destination/name).read_bytes())
            with self.assertRaisesRegex(ValueError,'checksum'):install(payload+b'bad',manifest,folder/'bad')
            self.assertFalse((folder/'bad').exists())
            with patch.dict('os.environ',DHE_DEMO_DATA_DIR=str(destination)):
                app=create_cloud_app();self.assertIsNotNone(app.state.registry);app.state.registry.close()
            with patch.dict('os.environ',DHE_DEMO_DATA_DIR=str(folder/'missing')):
                with self.assertRaisesRegex(RuntimeError,'incomplete'):create_cloud_app()


if __name__=='__main__':unittest.main()
