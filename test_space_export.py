"""Original OBJ boundaries, full live DHE references, and snapshot-pinned API."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import geopandas as gpd
from shapely.geometry import box,Polygon,Point,LineString

from generalization_model import GeneralizationSession,_core
from web_generalization_data import prepare_web_frame
from web_generalization_scene import WebScene
from web_generalization_app import Workspace,create_app
from web_space_export import ExportSnapshot
from test_mapping import build
from fastapi.testclient import TestClient


def scene(geometries=None):
    geometries=geometries or [box(100,200,102,202),box(102,200,104,202)]
    data=prepare_web_frame(gpd.GeoDataFrame({'osm_id':[f'original-{i}' for i in range(len(geometries))]},geometry=geometries,crs=2180))
    session=GeneralizationSession(data)
    return session,WebScene(session,3)


def parsed(frozen,coordinates='local',revision=3):
    return json.loads(b''.join(frozen.duality_chunks(coordinates,revision)))


def obj_files(frozen,coordinates='local',revision=3):
    with frozen.primal_zip(coordinates,revision) as file,ZipFile(file) as archive:
        return archive.read('primal.obj').decode(),json.loads(archive.read('metadata.json')),archive.read('primal.mtl').decode()


class SpaceExportTests(unittest.TestCase):
    def check_pointers(self,data):
        rs=data['dhe_records'];vs=data['vertices'];self.assertEqual([r['id'] for r in rs],list(range(len(rs))))
        for r in rs:
            self.assertLess(r['V'],len(vs))
            for key in ('S','D','NF','NV'):self.assertIn(r[key],range(len(rs)))
            self.assertEqual(rs[r['S']]['S'],r['id']);self.assertEqual(rs[r['D']]['D'],r['id'])
            self.assertEqual(rs[r['NF']]['V'],rs[r['S']]['V']);self.assertEqual(rs[r['NV']]['V'],r['V'])
            for contact in r['mapping_incident']:self.assertIn(r['id'],rs[contact]['mapping_incident'])
        self.assertTrue(any(r['kind']=='dual' for r in rs))
        self.assertTrue(any(v['kind']=='exterior_placeholder' for v in vs))
        exterior=next(n for n in data['dual_graph']['nodes'] if n['exterior']);self.assertIsNone(exterior['position'])
        for f in data['face_uses']:
            edge=f['seed'];ring=[]
            while True:
                ring.append(rs[edge]['V']);edge=rs[edge]['NF']
                if edge==f['seed']:break
                self.assertLess(len(ring),len(rs))
            self.assertEqual(ring,f['vertices'])

    def test_obj_preserves_cells_polygon_faces_and_shared_interface_orientations(self):
        session,s=scene();before=_core();obj,meta,mtl=obj_files(s.export)
        lines=obj.splitlines();vertices=[tuple(map(float,line.split()[1:])) for line in lines if line.startswith('v ')]
        self.assertEqual(len(vertices),12);self.assertEqual(sum(l.startswith('o ') for l in lines),2)
        faces=[list(map(int,l.split()[1:])) for l in lines if l.startswith('f ')]
        self.assertEqual(len(faces),12);self.assertTrue(all(len(f)==4 for f in faces))
        self.assertTrue(all(1<=v<=len(vertices) for f in faces for v in f))
        self.assertEqual(sum(l.startswith('newmtl ') for l in mtl.splitlines()),2)
        uses=meta['face_uses'];shared=[f for f in uses if all(vertices[meta['obj_vertex_ids'].index(v)][0]==2. for v in f['vertices'])]
        self.assertEqual(len(shared),2)
        edges=lambda f:{(a,b) for a,b in zip(f['vertices'],f['vertices'][1:]+f['vertices'][:1])}
        self.assertEqual(edges(shared[0]),{(b,a) for a,b in edges(shared[1])})
        data=parsed(s.export);self.check_pointers(data)
        internal=[l for l in data['dual_graph']['links'] if not l['exterior']]
        self.assertEqual(len(internal),1);self.assertEqual(len(internal[0]['face_uses']),2)
        self.assertEqual(_core(),before)

    def test_coordinate_modes_raw_z_precision_and_metadata(self):
        session,s=scene();local=parsed(s.export);absolute=parsed(s.export,'projected')
        self.assertEqual(local['metadata']['coordinates'],'local');self.assertEqual(absolute['metadata']['crs'],'EPSG:2180')
        for a,b in zip(local['vertices'],absolute['vertices']):
            self.assertEqual(b['position'],[a['position'][0]+session.origin[0],a['position'][1]+session.origin[1],a['position'][2]])
        self.assertEqual(max(v['position'][2] for v in local['vertices'] if v['kind']=='primal'),5.)
        obj,meta,_=obj_files(s.export,'projected')
        self.assertEqual(meta['metadata']['snapshot_id'],s.payload['snapshot_id'])
        self.assertIn('100 200',obj);self.assertIn('transition',meta['metadata']['z_semantics'])
        self.assertTrue(meta['metadata']['crs_wkt'])
        self.assertEqual(json.loads(s.export.cells_json[0])['metadata']['attributes']['osm_id'],'original-0')

    def test_concave_faces_remain_polygons_and_snapshot_is_frozen(self):
        polygon=Polygon([(0,0),(4,0),(4,1),(1,1),(1,4),(0,4)])
        session,s=scene([polygon]);obj,_,_=obj_files(s.export)
        self.assertEqual(sum(l.startswith('f ') for l in obj.splitlines()),8)
        self.assertEqual(sum(l.startswith('f ') and len(l.split())==7 for l in obj.splitlines()),2)
        before=parsed(s.export);vertex=session.model.cells[0].seed.V;old=vertex.x
        try:
            vertex.x=999.;s.payload['dataset']='changed after snapshot'
            after=parsed(s.export)
            self.assertEqual(before,after)
        finally:vertex.x=old

    def test_mapping_edges_sheets_contacts_and_no_fake_terminal_geometry(self):
        for target in (Point(2,1),LineString([(1,1),(3,1)])):
            r,s=build([box(0,0,4,3)],[target],mode='classic');data=parsed(s.export,revision=1)
            self.check_pointers(data);obj,meta,_=obj_files(s.export,revision=1)
            self.assertTrue(any(l['relation']=='mapping_contact' for l in data['dual_graph']['links']))
            self.assertEqual(data['metadata']['mapping_report']['endpoint_validation'],'passed')
            expected=1 if target.geom_type=='Point' else 2
            primitive=next(c for c in data['cells'] if c['dimension']==expected)
            uses=[f for f in data['face_uses'] if f['owner']==primitive['owner']]
            self.assertEqual(len(uses),expected)
            if expected==1:self.assertEqual(sum(l.startswith('l ') for l in obj.splitlines()),1)
            else:self.assertEqual(len(uses[0]['vertices']),len(uses[1]['vertices']))
        session,s=scene([box(0,0,4,3)])
        p=session.preview('point',[1]);session.apply(p)
        self.check_pointers(parsed(WebScene(session,4).export,revision=4))

    def test_holed_features_keep_multiple_cells_and_empty_top(self):
        donut=Polygon(box(0,0,4,4).exterior.coords,[box(1,1,3,3).exterior.coords])
        r,s=build([box(0,0,4,4)],[donut]);data=parsed(s.export,revision=1)
        targets={c['owner'] for c in data['cells'] if c['metadata'].get('layer')=='target'}
        self.assertGreater(len(targets),1);area=0.
        for face in data['face_uses']:
            ps=[data['vertices'][v]['position'] for v in face['vertices']]
            if face['owner'] in targets and all(p[2]==15 for p in ps):area+=Polygon([(p[0],p[1]) for p in ps]).area
        self.assertAlmostEqual(area,donut.area)


class SpaceExportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'data.geojson'
        gpd.GeoDataFrame({'osm_id':['input']},geometry=[box(100,200,104,203)],crs=2180).to_file(self.path,driver='GeoJSON')
        self.w=Workspace(self.path,mapping_paths={'source':self.path,'target':self.path})
        self.client=TestClient(create_app(workspace=self.w,autoload=False));self.client.__enter__();self.w.execute('default',0)

    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()

    def request(self,kind='duality',snapshot=None,workspace='generalize',**extra):
        return self.client.get('/api/export/'+kind,params=dict(workspace_name=workspace,snapshot_id=snapshot or self.w.scene.export.snapshot_id,**extra))

    def test_downloads_validation_and_no_construction_on_export(self):
        before=_core();revision=self.w.revision
        with patch('web_generalization_app.GeneralizationSession',side_effect=AssertionError('Export rebuilt model')):
            data=self.request();archive=self.request('primal',coordinates='projected')
        self.assertEqual(data.status_code,200);self.assertEqual(archive.status_code,200)
        self.assertTrue(data.headers['content-type'].startswith('application/json'));self.assertIn('attachment',archive.headers['content-disposition'])
        with ZipFile(BytesIO(archive.content)) as z:self.assertEqual(set(z.namelist()),{'primal.obj','primal.mtl','metadata.json'})
        self.assertEqual(self.w.revision,revision);self.assertEqual(_core(),before)
        self.assertEqual(self.request(coordinates='degrees').status_code,422)
        self.assertEqual(self.request(snapshot='expired').status_code,409)
        self.assertEqual(self.request(workspace='unknown').status_code,422)

    def test_preview_identity_and_apply_metadata(self):
        original=self.w.scene.export.snapshot_id
        result=self.w.execute('preview',self.w.revision,operation='point',selected=[1])
        preview=self.w.preview_scene.export.snapshot_id
        data=self.request(snapshot=preview).json();self.assertEqual(data['metadata']['preview_id'],result['preview_id'])
        self.assertEqual(self.request(snapshot=original).status_code,200)
        self.w.execute('apply',self.w.revision,operation='point',selected=[1])
        applied=self.request().json()['metadata']
        self.assertIsNone(applied['preview_id']);self.assertEqual(applied['cursor'],1);self.assertEqual(len(applied['history']),2)
        self.assertEqual(self.request(snapshot=preview).status_code,409);self.assertEqual(self.request(snapshot=original).status_code,409)

    def test_download_remains_pinned_if_model_changes_after_lookup(self):
        old=self.w.scene.export.snapshot_id;original=ExportSnapshot.duality_chunks
        def replaced(frozen,*args,**kwargs):
            self.w.execute('default',self.w.revision)
            yield from original(frozen,*args,**kwargs)
        with patch.object(ExportSnapshot,'duality_chunks',replaced):answer=self.request(snapshot=old)
        self.assertEqual(answer.status_code,200);self.assertEqual(answer.json()['metadata']['snapshot_id'],old)
        self.assertNotEqual(self.w.scene.export.snapshot_id,old);self.assertEqual(self.request(snapshot=old).status_code,409)

    def test_mapping_export_is_independent_and_stale_result_is_explicit(self):
        for side in ('source','target'):self.w.execute('mapping:load',self.w.mapping.revision,side=side)
        self.w.execute('mapping:build',self.w.mapping.revision)
        snapshot=self.w.mapping.result.export.snapshot_id
        data=self.request(workspace='mapping',snapshot=snapshot).json()
        self.assertEqual(data['metadata']['workspace'],'mapping');self.assertFalse(data['metadata']['stale'])
        self.w.execute('mapping:review',self.w.mapping.revision,groups=self.w.mapping.groups,settings={'source_label':'new label'})
        stale=self.request(workspace='mapping',snapshot=snapshot).json()['metadata']
        self.assertTrue(stale['stale']);self.assertEqual(stale['mapping_report']['settings']['source_label'],'')
        self.assertEqual(self.request(snapshot=snapshot).status_code,409)


if __name__=='__main__':unittest.main()
