"""Immutable, local-coordinate exports of validated live DHE snapshots."""
from dataclasses import dataclass,replace
import json
from math import isfinite
from tempfile import SpooledTemporaryFile
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED

import DHECore as dhe
from generalization_geometry import feature_color
from web_generalization_data import json_value


def encoded(value):
    return json.dumps(json_value(value),ensure_ascii=False,allow_nan=False,separators=(',',':'))


def scene_metadata(payload):
    return {k:payload[k] for k in ('dataset','revision','preview_id','levels','notes','cursor','history',
                                   'operation','automatic_report','mapping_report') if k in payload}


@dataclass(frozen=True,slots=True)
class ExportVertex:
    raw_id:object
    position:tuple
    kind:str


@dataclass(frozen=True,slots=True)
class ExportRecord:
    raw_id:object
    label:str
    kind:str
    V:int
    S:int
    D:int
    NF:int
    NV:int
    render_id:object
    contacts:tuple


@dataclass(frozen=True,slots=True)
class ExportFace:
    owner:int
    seed:int
    part:str
    vertices:tuple


@dataclass(frozen=True)
class ExportSnapshot:
    snapshot_id:str
    metadata_json:str
    vertices:tuple
    records:tuple
    faces:tuple
    cells_json:tuple
    nodes_json:tuple
    links_json:tuple

    @classmethod
    def capture(cls,model,cells,face_records,render_keys,*,workspace,crs,payload,cell_metadata):
        # Use live cell traversals and reciprocal exterior/dual references, never
        # the mutable process-global ledger or coordinate-based welding.
        interior=list(render_keys)
        primal=list(dict.fromkeys([*interior,*(e.Adjacent() for e in interior)]))
        records=list(dict.fromkeys([*primal,*(e.D for e in primal)]));ids={e:i for i,e in enumerate(records)}
        primal_set=set(primal)
        if any(p not in ids for e in records for p in (e.S,e.D,e.NF,e.NV)):
            raise ValueError('Cannot export an incomplete live DHE pointer graph.')
        vertices=list(dict.fromkeys(e.V for e in records));vid={v:i for i,v in enumerate(vertices)}
        dual_vertices={c.dual_vertex for c in cells}
        vertex_rows=tuple(ExportVertex(json_value(v.i),(float(v.x),float(v.y),float(v.z)),
            'exterior_placeholder' if v is model.exterior else 'dual' if v in dual_vertices else 'primal') for v in vertices)
        record_rows=tuple(ExportRecord(json_value(e.i),str(e.info),'primal' if e in primal_set else 'dual',
            vid[e.V],ids[e.S],ids[e.D],ids[e.NF],ids[e.NV],render_keys.get(e),
            tuple(ids[c] for c in getattr(e,'mapping_incident',()))) for e in records)
        faces=[];links=[];interfaces={}
        cell_order={int(c.feature_id):i for i,c in enumerate(cells)}
        for f,seed in sorted(face_records,key=lambda item:cell_order[int(item[0].owner)]):
            ring=dhe.face_edges(seed);fid=len(faces)
            faces.append(ExportFace(int(f.owner),ids[seed],f.part,tuple(vid[e.V] for e in ring)))
            key=frozenset(min(ids[e],ids[e.Adjacent()]) for e in ring)
            if key in interfaces:links[interfaces[key]]['face_uses'].append(fid)
            else:
                target=seed.Adjacent().D.V
                interfaces[key]=len(links)
                links.append(dict(id=f'face{len(links)}',kind='face',relation='DHE_adjacency',source=str(f.owner),
                    target='VINF' if target is model.exterior else str(target.logical_feature_id),
                    face_uses=[fid],records=[ids[seed],ids[seed.Adjacent()]],
                    degenerate=len(faces[-1].vertices)<3,exterior=target is model.exterior))
        for i,c in enumerate(getattr(model,'mapping_contacts',())):
            links.append(dict(id=f'contact{i}',kind='edge' if c['dimension']==1 else 'vertex',
                relation='mapping_contact',primal_dimension=c['dimension'],source=str(c['owners'][0]),
                target=str(c['owners'][1]),records=[ids[c['first']],ids[c['second']]],exterior=False))
        cell_rows=[];nodes=[]
        for cell in cells:
            owner=int(cell.feature_id);color=[round(c*255) for c in feature_color(owner)]
            meta=cell_metadata.get(owner,{})
            cell_rows.append(encoded(dict(id=str(owner),owner=owner,seed=ids[cell.seed],dual_vertex=vid[cell.dual_vertex],
                dimension=getattr(cell,'mapping_dimension',3),color=color,metadata=meta)))
            nodes.append(encoded(dict(id=str(owner),vertex=vid[cell.dual_vertex],exterior=False)))
        nodes.append(encoded(dict(id='VINF',vertex=vid[model.exterior],exterior=True,position_semantics='unbounded')))
        token=uuid4().hex
        meta=dict(schema_version=1,snapshot_id=token,workspace=workspace,crs=crs.to_string(),crs_wkt=crs.to_wkt(),
            origin=list(model.origin),axes=['easting','northing','transition_z'],xy_units='metres',
            z_semantics='Raw transition coordinate; not measured building height',**scene_metadata(payload))
        return cls(token,encoded(meta),vertex_rows,record_rows,tuple(faces),tuple(cell_rows),
                   tuple(nodes),tuple(encoded(link) for link in links))

    def refresh_metadata(self,payload):
        return replace(self,metadata_json=encoded({**json.loads(self.metadata_json),**scene_metadata(payload)}))

    def metadata(self,coordinates,workspace_revision):
        if coordinates not in ('local','projected'):raise ValueError('Choose local or projected coordinates.')
        meta=json.loads(self.metadata_json)
        return {**meta,'coordinates':coordinates,'workspace_revision_at_export':workspace_revision,
                'stale':workspace_revision!=meta['revision']}

    def positions(self,coordinates):
        origin=json.loads(self.metadata_json)['origin'] if coordinates=='projected' else (0.,0.)
        return ((v.position[0]+origin[0],v.position[1]+origin[1],v.position[2]) for v in self.vertices)

    def face_row(self,i,f):
        return dict(id=i,owner=f.owner,seed=f.seed,part=f.part,vertices=list(f.vertices),obj_element=i+1)

    def duality_parts(self,coordinates,workspace_revision):
        yield '{"metadata":'+encoded(self.metadata(coordinates,workspace_revision))
        def table(name,rows):
            yield ',"'+name+'":['
            for i,row in enumerate(rows):yield (',' if i else '')+row
            yield ']'
        positions=tuple(self.positions(coordinates))
        yield from table('vertices',(encoded(dict(id=i,raw_id=v.raw_id,position=positions[i],kind=v.kind)) for i,v in enumerate(self.vertices)))
        yield from table('dhe_records',(encoded(dict(id=i,raw_id=r.raw_id,label=r.label,kind=r.kind,V=r.V,S=r.S,D=r.D,
            NF=r.NF,NV=r.NV,render_halfedge_id=r.render_id,mapping_incident=r.contacts)) for i,r in enumerate(self.records)))
        yield from table('face_uses',(encoded(self.face_row(i,f)) for i,f in enumerate(self.faces)))
        yield from table('cells',self.cells_json)
        yield ',"dual_graph":{'
        yield '"nodes":['
        for i,value in enumerate(self.nodes_json):
            node=json.loads(value);node['position']=None if node['exterior'] else positions[node['vertex']]
            yield (',' if i else '')+encoded(node)
        yield ']'
        yield from table('links',self.links_json)
        yield '}}'

    def duality_chunks(self,coordinates,workspace_revision):
        buffer=[];size=0
        for part in self.duality_parts(coordinates,workspace_revision):
            buffer.append(part);size+=len(part)
            if size>=65536:yield ''.join(buffer).encode('utf8');buffer=[];size=0
        if buffer:yield ''.join(buffer).encode('utf8')

    def primal_zip(self,coordinates,workspace_revision):
        meta=self.metadata(coordinates,workspace_revision);positions=tuple(self.positions(coordinates))
        obj_vertices=[i for i,v in enumerate(self.vertices) if v.kind=='primal'];index={v:i+1 for i,v in enumerate(obj_vertices)}
        by_owner={}
        for i,f in enumerate(self.faces):by_owner.setdefault(f.owner,[]).append((i,f))
        output=SpooledTemporaryFile(max_size=8*1024**2,mode='w+b')
        try:
            with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
                with archive.open('primal.obj','w') as obj:
                    obj.write(b'# Actual bounded DHE cells; original polygon faces; raw Z; Z-up\nmtllib primal.mtl\ns off\n')
                    for v in obj_vertices:obj.write(('v '+' '.join(format(x,'.17g') for x in positions[v])+'\n').encode('ascii'))
                    for value in self.cells_json:
                        cell=json.loads(value);owner=cell['owner']
                        obj.write(f'o cell_{owner}\nusemtl cell_{owner}\n'.encode('ascii'))
                        for _,face in by_owner.get(owner,[]):
                            kind='f' if len(face.vertices)>=3 else 'l' if len(face.vertices)==2 else 'p'
                            obj.write((kind+' '+' '.join(str(index[v]) for v in face.vertices)+'\n').encode('ascii'))
                with archive.open('primal.mtl','w') as mtl:
                    for value in self.cells_json:
                        cell=json.loads(value)
                        mtl.write((f'newmtl cell_{cell["owner"]}\nKd '+' '.join(format(v/255.,'.17g') for v in cell['color'][:3])+'\nd 1\nillum 1\n').encode('ascii'))
                with archive.open('metadata.json','w') as metadata:
                    metadata.write(('{"metadata":'+encoded(meta)+',"obj_vertex_ids":'+encoded(obj_vertices)+',"cells":[').encode('utf8'))
                    for i,value in enumerate(self.cells_json):metadata.write(((',' if i else '')+value).encode('utf8'))
                    metadata.write(b'],"face_uses":[')
                    for i,face in enumerate(self.faces):metadata.write(((',' if i else '')+encoded(self.face_row(i,face))).encode('utf8'))
                    metadata.write(b']}')
            output.seek(0);return output
        except BaseException:output.close();raise

    def edge_obj(self,kind,coordinates,workspace_revision):
        """Exact geometric segments from the frozen primal or finite dual graph."""
        if kind not in ('primal-edges','dual-edges'):raise ValueError('Choose primal-edges or dual-edges.')
        meta=self.metadata(coordinates,workspace_revision);positions=tuple(self.positions(coordinates));segments=set()
        def add(a,b):
            a,b=positions[a],positions[b]
            if not all(isfinite(x) for p in (a,b) for x in p):raise ValueError('OBJ edges require finite XYZ coordinates.')
            if a!=b:segments.add(tuple(sorted((a,b))))
        if kind=='primal-edges':
            for record in self.records:
                if record.kind!='primal':continue
                a,b=record.V,self.records[record.S].V
                if self.vertices[a].kind==self.vertices[b].kind=='primal':add(a,b)
        else:
            nodes={node['id']:node['vertex'] for node in map(json.loads,self.nodes_json) if not node['exterior']}
            for link in map(json.loads,self.links_json):
                if link['kind']!='face' or link['relation']!='DHE_adjacency' or link['exterior']:continue
                add(nodes[link['source']],nodes[link['target']])
        vertices=sorted({p for segment in segments for p in segment});index={p:i+1 for i,p in enumerate(vertices)}
        summary={k:meta[k] for k in ('snapshot_id','workspace','revision','workspace_revision_at_export','stale','coordinates','crs','origin','z_semantics')}
        summary.update(edge_scope='bounded_primal' if kind=='primal-edges' else 'finite_face_adjacency',
                       uniqueness='Exact unordered endpoint XYZ pairs; zero-length segments omitted',
                       vertices=len(vertices),edges=len(segments))
        output=SpooledTemporaryFile(max_size=8*1024**2,mode='w+b')
        try:
            output.write((f'# Unique {kind}; raw transition Z; Z-up\n# '+encoded(summary)+'\n').encode('utf8'))
            for p in vertices:output.write(('v '+' '.join(format(x,'.17g') for x in p)+'\n').encode('ascii'))
            for a,b in sorted(segments):output.write(f'l {index[a]} {index[b]}\n'.encode('ascii'))
            output.seek(0);return output
        except BaseException:output.close();raise


def attach_export_routes(app,workspace):
    from typing import Literal
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask

    @app.get('/api/export/{kind}')
    def download(kind:Literal['primal','duality','primal-edges','dual-edges'],workspace_name:Literal['generalize','mapping'],snapshot_id:str,
                 coordinates:Literal['local','projected']='local'):
        with workspace.lock:
            if workspace_name=='generalize':
                candidates=[workspace.preview_scene,workspace.scene];revision=workspace.revision
            else:
                with workspace.mapping.lock:
                    candidates=[workspace.mapping.preview,workspace.mapping.result];revision=workspace.mapping.revision
            frozen=next((s.export for s in candidates if s is not None and s.export.snapshot_id==snapshot_id),None)
            if frozen is None:raise HTTPException(409,'The displayed snapshot expired or changed. Refresh before exporting.')
        prefix=f'dhe_{workspace_name}_{kind}_{snapshot_id[:8]}'
        headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="{prefix}.json"'}
        if kind=='duality':return StreamingResponse(frozen.duality_chunks(coordinates,revision),media_type='application/json',headers=headers)
        if kind=='primal':data=frozen.primal_zip(coordinates,revision);extension='zip';media_type='application/zip'
        else:data=frozen.edge_obj(kind,coordinates,revision);extension='obj';media_type='model/obj'
        headers.update({'Content-Disposition':f'attachment; filename="{prefix}.{extension}"','Content-Encoding':'identity'})
        return StreamingResponse(iter(lambda:data.read(65536),b''),media_type=media_type,headers=headers,background=BackgroundTask(data.close))
