"""Geographic display and actual plane sections of Mapping DHE primitives."""
import numpy as np
from shapely.geometry import Point,LineString,Polygon
from shapely.ops import unary_union

import DHECore as dhe
from plane_slicing import PlaneSlicer,SlicePlane
from web_generalization_scene import GeographicCoordinates,display_triangles,rgba,WebScene
from web_space_export import ExportSnapshot


class MappingScene:
    def __init__(self,result,revision,preview_id=None):
        self.result=result;self.revision=revision;self.preview_id=preview_id
        self.geo=GeographicCoordinates(result.crs,result.origin)
        model=result.model;cells=[*model.cells,*model.mapping_primitives]
        edges=list(dict.fromkeys(e for c in cells for e in dhe.Cell(c.seed)));keys={e:str(i) for i,e in enumerate(edges)}
        face_records=[*model.generalization_face_records,*model.mapping_face_records]
        frames=[(str(f.owner),f.points) for f,_ in face_records if len(f.points)>=3]
        if frames:self.slicer=PlaneSlicer.from_face_records(frames)
        else:
            self.slicer=PlaneSlicer.__new__(PlaneSlicer);self.slicer.faces={}
            self.slicer.coplanar_owner={};self.slicer.horizontal_owners={}
            self.slicer.vertices=np.array([(e.V.x,e.V.y,e.V.z) for e in edges])
            if len(self.slicer.vertices)==0:raise ValueError('No mapped geometry remains after exclusions.')
            self.slicer.minimum=self.slicer.vertices.min(axis=0);self.slicer.maximum=self.slicer.vertices.max(axis=0)
            self.slicer.center=(self.slicer.minimum+self.slicer.maximum)/2
        # Edge primitives have no face frame, but still extend the actual bounds.
        self.slicer.vertices=np.array([(e.V.x,e.V.y,e.V.z) for e in edges])
        self.slicer.minimum=self.slicer.vertices.min(axis=0);self.slicer.maximum=self.slicer.vertices.max(axis=0)
        self.slicer.center=(self.slicer.minimum+self.slicer.maximum)/2
        self.features={o:g for o,g in result.geometries.items() if result.metadata[o]['layer']=='target'}
        records={};edge_faces={};triangles=[]
        for f,seed in face_records:
            key=tuple(sorted(tuple(p) for p in f.points))
            if key not in records:
                fid=str(len(records));records[key]=dict(id=fid,owners=[],uses=[],ring=[self.geo.point(p) for p in f.points])
                if len(f.points)>=3:
                    triangles.extend(dict(face=fid,polygon=[self.geo.point(p) for p in tri]) for tri in display_triangles(f.points))
            item=records[key]
            if f.owner not in item['owners']:item['owners'].append(f.owner)
            item['uses'].append(dict(owner=f.owner,edge=keys[seed],part=f.part))
            for e in dhe.face_edges(seed):edge_faces[e]=item['id']
        faces=list(records.values())
        for f in faces:
            f.update(owner=min(f['owners']),color=rgba(min(f['owners'])),edge=f['uses'][0]['edge'])
        halfedges=[];segments={};vertices={}
        for e in edges:
            owner=int(e.D.V.logical_feature_id);meta=result.metadata[owner]
            a=self.geo.point((e.V.x,e.V.y,e.V.z));b=self.geo.point((e.S.V.x,e.S.V.y,e.S.V.z))
            halfedges.append(dict(id=keys[e],raw_id=e.i,label=e.info,owner=owner,face=edge_faces[e],source=a,target=b,
                nf=keys.get(e.NF),s=keys.get(e.S),adjacent=keys.get(e.Adjacent()),
                contacts=[keys[c] for c in getattr(e,'mapping_incident',[]) if c in keys],**meta))
            segments.setdefault(tuple(sorted((tuple(a),tuple(b)))),dict(source=a,target=b,owner=owner,color=rgba(owner),edge=keys[e]))
            vertices.setdefault(tuple(a),dict(position=a,owner=owner,color=rgba(owner)))
        duals={int(c.feature_id):dict(id=str(c.feature_id),owner=int(c.feature_id),position=self.geo.point((c.dual_vertex.x,c.dual_vertex.y,c.dual_vertex.z)),color=rgba(int(c.feature_id)),**result.metadata[int(c.feature_id)]) for c in cells}
        ext=model.exterior;exterior=dict(id='VINF',owner=None,position=self.geo.point((ext.x,ext.y,ext.z)),color=[145,67,194,255])
        links=[]
        for f in faces:
            a=duals[f['owners'][0]];b=duals[f['owners'][1]] if len(f['owners'])==2 else exterior
            links.append(dict(id=f['id'],face=f['id'],edge=f['edge'],source=a['position'],target=b['position'],owners=[a['id'],b['id']],exterior=b is exterior,kind='face'))
        for i,contact in enumerate(model.mapping_contacts):
            a,b=(duals[k] for k in contact['owners'])
            links.append(dict(id=f'contact{i}',face=None,edge=keys[contact['first']],source=a['position'],target=b['position'],
                              owners=[a['id'],b['id']],exterior=False,kind='edge' if contact['dimension']==1 else 'vertex'))
        features=[]
        for owner,g in self.features.items():
            m=result.metadata[owner]
            features.append(dict(type='Feature',geometry=self.geo.geometry(g),properties=dict(index=owner,kind=g.geom_type,
                name=m['feature_id'],original_id=m['original_id'],color=rgba(owner),lineage=[],members=m['members'],attributes=m['attributes'],
                label_position=self.geo.point(g.representative_point().coords[0]),dimension=m['dimension'])))
        coordinates=[p['position'] for p in vertices.values()]
        bounds=[min(p[0] for p in coordinates),min(p[1] for p in coordinates),max(p[0] for p in coordinates),max(p[1] for p in coordinates)]
        self.payload=dict(revision=revision,preview_id=preview_id,preview=preview_id is not None,workspace='mapping',
            dataset='Mapping: '+result.report['source']+' → '+result.report['target'],crs=result.crs.to_string(),origin=list(result.origin),
            levels=result.levels,bounds=bounds,features=dict(type='FeatureCollection',features=features),faces=faces,triangles=triangles,
            halfedges=halfedges,edges=list(segments.values()),vertices=list(vertices.values()),duals=[*duals.values(),exterior],dual_edges=links,
            cells=len(cells),cursor=0,history=[dict(index=0,label='Validated two-layer mapping',step_id='mapping',z=result.levels['z_t'])],
            notes=['Actual DHE cells; Classic symbols use typed edge/sheet primitives.'],operation=None,automatic_report=None,mapping_report=result.report)
        self.export=ExportSnapshot.capture(model,cells,face_records,keys,workspace='mapping',crs=result.crs,
                                          payload=self.payload,cell_metadata=result.metadata)
        self.payload['snapshot_id']=self.export.snapshot_id

    def section(self,orientation,value,tilt=45.,azimuth=0.,*,include_sections=True,clamp_position=False):
        answer=WebScene.section(self,orientation,value,tilt,azimuth,include_sections=include_sections,clamp_position=clamp_position)
        value=answer['value']
        plane=SlicePlane.horizontal(value) if orientation=='Horizontal' else SlicePlane.from_angles(self.slicer.center,90. if orientation=='Vertical' else tilt,azimuth,value)
        if include_sections:
            for c in self.result.model.mapping_primitives:
                if c.mapping_dimension!=1:continue
                e=c.seed;points=np.array([(e.V.x,e.V.y,e.V.z),(e.S.V.x,e.S.V.y,e.S.V.z)])
                ds=plane.signed_distance(points);bound=1e-8
                if np.max(np.abs(ds))<=bound:g=dict(type='LineString',coordinates=[self.geo.point(p) for p in points])
                elif ds[0]*ds[1]<=0 or min(abs(ds))<=bound:
                    t=float(ds[0]/(ds[0]-ds[1])) if ds[0]!=ds[1] else 0.
                    g=dict(type='Point',coordinates=self.geo.point(points[0]+max(0.,min(1.,t))*(points[1]-points[0])))
                else:continue
                answer['features']['features'].append(dict(type='Feature',geometry=g,properties=dict(owner=int(c.feature_id),color=rgba(int(c.feature_id)),active=True)))
        present={f['properties']['owner'] for f in answer['features']['features']}
        answer['missing']=sorted(set(self.features)-present) if include_sections and orientation=='Horizontal' and value==self.result.levels['z_t'] else []
        return answer

    def validate_endpoints(self,source,target,groups):
        """Compare actual local sections with the approved immutable inputs."""
        excluded={k for g in groups if g['decision']=='exclude' for k in [*g['sources'],*g['targets']]}
        for side,layer,z in (('source',source,self.result.levels['z_s']),('target',target,self.result.levels['z_t'])):
            actual=self.slicer.sections(SlicePlane.horizontal(z),include_degenerate=True,horizontal_side='below')
            for c in self.result.model.mapping_primitives:
                if c.mapping_dimension==1:
                    e=c.seed
                    if min(e.V.z,e.S.V.z)<=z<=max(e.V.z,e.S.V.z):actual[str(c.feature_id)]=Point(e.V.x,e.V.y)
            for uid,f in layer.features.items():
                if uid in excluded:continue
                owners=[o for o,m in self.result.metadata.items() if m['feature_id']==uid and m['layer']==side]
                measured=unary_union([actual[str(o)] for o in owners if str(o) in actual])
                from shapely.affinity import translate
                expected=translate(f.geometry,xoff=-self.result.origin[0],yoff=-self.result.origin[1])
                if measured.is_empty or measured.hausdorff_distance(expected)>1e-6 or (expected.area>0 and measured.symmetric_difference(expected).area>max(1e-6,expected.area*1e-9)):
                    raise ValueError(f'{uid}: actual {side} endpoint section does not match the approved input.')
        self.result.report['endpoint_validation']='passed'
        self.export=self.export.refresh_metadata(self.payload)
