"""Read-only geographic snapshots of actual DHE cells and plane sections."""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
from pyproj import Transformer
from shapely import constrained_delaunay_triangles,transform
from shapely.geometry import Polygon

import DHECore as dhe
from generalization_geometry import feature_color
from plane_slicing import PlaneSlicer,SlicePlane
from web_generalization_data import json_value


def rgba(owner):return [round(v*255) for v in feature_color(int(owner))]


def display_triangles(points):
    """Tessellate a planar DHE face in its own plane, never changing its boundary."""
    a=np.asarray(points,dtype=float);origin=a[0]
    normal=np.sum(np.cross(a-origin,np.roll(a,-1,axis=0)-origin),axis=0)
    normal/=np.linalg.norm(normal)
    u=next(v/np.linalg.norm(v) for v in a[1:]-origin if np.linalg.norm(v)>1e-12)
    v=np.cross(normal,u);xy=np.column_stack(((a-origin)@u,(a-origin)@v))
    polygon=Polygon(xy);lookup={tuple(p):tuple(q) for p,q in zip(xy,a)}
    if not polygon.is_valid:raise ValueError('Cannot render an invalid DHE face.')
    return [[lookup.get(tuple(p),tuple(origin+p[0]*u+p[1]*v)) for p in triangle.exterior.coords[:-1]]
            for triangle in constrained_delaunay_triangles(polygon).geoms]


class GeographicCoordinates:
    def __init__(self,crs,origin):
        self.origin=origin;self.transformer=Transformer.from_crs(crs,4326,always_xy=True)

    def point(self,p):
        lon,lat=self.transformer.transform(p[0]+self.origin[0],p[1]+self.origin[1])
        return [float(lon),float(lat),float(p[2]) if len(p)>2 else 0.]

    def geometry(self,g):
        def reproject(x,y,z=None):
            return self.transformer.transform(np.asarray(x)+self.origin[0],np.asarray(y)+self.origin[1])
        return transform(g,reproject,interleaved=False).__geo_interface__


def geometry_parts(g):
    if g.is_empty:return
    if hasattr(g,'geoms'):
        for child in g.geoms:yield from geometry_parts(child)
    else:yield g


class WebScene:
    def __init__(self,session,revision,*,proposal=None,preview_id=None):
        state=proposal.state if proposal else session.state;model=proposal.model if proposal else session.model
        geo=GeographicCoordinates(session.data.frame.crs,session.origin);self.geo=geo
        self.slicer=PlaneSlicer.from_face_records((str(f.owner),f.points) for f in state.faces)
        self.features=state.features;self.revision=revision;self.preview_id=preview_id
        edges=model.active_edges();keys={e:str(i) for i,e in enumerate(edges)}
        records={};edge_faces={};triangles=[]
        for face,seed in model.generalization_face_records:
            key=tuple(sorted(tuple(p) for p in face.points))
            if key not in records:
                fid=str(len(records));records[key]=dict(id=fid,owners=[],uses=[],ring=[geo.point(p) for p in face.points])
                triangles.extend(dict(face=fid,polygon=[geo.point(p) for p in tri]) for tri in display_triangles(face.points))
            record=records[key];record['owners'].append(face.owner)
            record['uses'].append(dict(owner=face.owner,edge=keys[seed],part=face.part))
            for e in dhe.face_edges(seed):edge_faces[e]=record['id']
        faces=list(records.values())
        for f in faces:
            active=[k for k in f['owners'] if k in state.features]
            f['owner']=min(active or f['owners']);f['color']=rgba(f['owner'])
            f['edge']=next(u['edge'] for u in f['uses'] if u['owner']==f['owner'])
        halfedges=[];segments={};vertices={}
        for edge in edges:
            owner=int(edge.D.V.logical_feature_id)
            source,target=geo.point((edge.V.x,edge.V.y,edge.V.z)),geo.point((edge.S.V.x,edge.S.V.y,edge.S.V.z))
            adjacent=edge.Adjacent()
            record=dict(id=keys[edge],raw_id=edge.i,label=edge.info,owner=owner,face=edge_faces[edge],
                source=source,target=target,nf=keys.get(edge.NF),s=keys.get(edge.S),adjacent=keys.get(adjacent))
            halfedges.append(record)
            key=tuple(sorted((tuple(source),tuple(target))))
            segments.setdefault(key,dict(source=source,target=target,owner=owner,color=rgba(owner),edge=keys[edge]))
            vertices.setdefault(tuple(source),dict(position=source,owner=owner,color=rgba(owner)))
        duals={id(c.dual_vertex):dict(id=str(c.feature_id),owner=int(c.feature_id),
            position=geo.point((c.dual_vertex.x,c.dual_vertex.y,c.dual_vertex.z)),color=rgba(int(c.feature_id))) for c in model.cells}
        ext=model.exterior
        duals[id(ext)]=dict(id='VINF',owner=None,position=geo.point((ext.x,ext.y,ext.z)),color=[145,67,194,255])
        links=[]
        cell_vertices={int(c.feature_id):c.dual_vertex for c in model.cells}
        for face in faces:
            owners=list(dict.fromkeys(face['owners']))
            if len(owners)==2:
                a=cell_vertices[owners[0]];b=cell_vertices[owners[1]]
            else:
                a=cell_vertices[owners[0]];b=ext
            links.append(dict(id=face['id'],face=face['id'],source=duals[id(a)]['position'],target=duals[id(b)]['position'],
                              owners=[duals[id(a)]['id'],duals[id(b)]['id']],exterior=b is ext))
        features=[]
        for key,f in sorted(state.features.items()):
            attributes=json_value(f.attributes)
            members=attributes.get('source_members',[])
            original=attributes.get('original_id',attributes.get('osm_id',attributes.get('id',key)))
            features.append(dict(type='Feature',geometry=geo.geometry(f.geometry),properties=dict(index=key,name=f.label,
                original_id=str(original),kind=f.geometry.geom_type,lineage=list(f.lineage),color=rgba(key),
                label_position=geo.point(f.geometry.representative_point().coords[0]),members=members,attributes=attributes)))
        levels=dict(z_b=float(self.slicer.minimum[2]),z_t=state.z,z_s=state.operation.z_s if state.operation else state.z)
        if state.operation and state.operation.z_m is not None:levels['z_m']=state.operation.z_m
        coordinates=[p['position'] for p in vertices.values()]
        bounds=[min(p[0] for p in coordinates),min(p[1] for p in coordinates),max(p[0] for p in coordinates),max(p[1] for p in coordinates)]
        self.payload=dict(revision=revision,preview_id=preview_id,preview=proposal is not None,
            dataset=session.data.source,crs=session.data.frame.crs.to_string(),origin=list(session.origin),
            notes=list(session.data.rejected)+(list(proposal.notes) if proposal else []),
            levels=levels,bounds=bounds,features=dict(type='FeatureCollection',features=features),
            faces=faces,triangles=triangles,edges=list(segments.values()),halfedges=halfedges,vertices=list(vertices.values()),
            duals=list(duals.values()),dual_edges=links,cells=len(model.cells),
            cursor=session.cursor,history=[dict(index=i,label=s.description,step_id=s.step_id,z=s.z) for i,s in enumerate(session.history)],
            operation=json_value(asdict(state.operation)) if state.operation else None,
            automatic_report=json_value(getattr(session,'last_automatic_report',None)))

    def section(self,orientation,value,tilt=45.,azimuth=0.,*,include_sections=True,clamp_position=False):
        if orientation=='Horizontal':plane=SlicePlane.horizontal(value)
        elif orientation in ('Vertical','Inclined'):
            plane=SlicePlane.from_angles(self.slicer.center,90. if orientation=='Vertical' else tilt,azimuth,value)
        else:raise ValueError('Choose Horizontal, Vertical, or Inclined.')
        lower,upper=(float(self.slicer.minimum[2]),float(self.slicer.maximum[2])) if orientation=='Horizontal' else self.slicer.offset_bounds(plane)
        if not lower-1e-8<=value<=upper+1e-8:
            if not clamp_position:raise ValueError(f'Plane position must be between {lower:g} and {upper:g}.')
            value=max(lower,min(upper,value))
            plane=SlicePlane.horizontal(value) if orientation=='Horizontal' else SlicePlane.from_angles(self.slicer.center,90. if orientation=='Vertical' else tilt,azimuth,value)
        sections=self.slicer.sections(plane,include_degenerate=True,horizontal_side='below') if include_sections else {};features=[]
        for owner,g in sections.items():
            def lift(points):return [self.geo.point(p) for p in plane.lift(np.array(points))]
            for part in geometry_parts(g):
                if part.geom_type=='Polygon':coordinates=[lift(part.exterior.coords),*[lift(r.coords) for r in part.interiors]]
                elif part.geom_type=='Point':coordinates=lift(part.coords)[0]
                else:coordinates=lift(part.coords)
                features.append(dict(type='Feature',geometry=dict(type=part.geom_type,coordinates=coordinates),
                    properties=dict(owner=int(owner),color=rgba(int(owner)),active=int(owner) in self.features)))
        missing=[]
        if include_sections and orientation=='Horizontal' and abs(value-self.payload['levels']['z_t'])<1e-8:
            missing=sorted(set(self.features)-{int(k) for k in sections})
        return dict(revision=self.revision,preview_id=self.preview_id,orientation=orientation,value=value,
            normal=list(plane.normal),sections_computed=include_sections,
            bounds=[float(lower),float(upper)],outline=[self.geo.point(p) for p in self.slicer.plane_outline(plane)],
            features=dict(type='FeatureCollection',features=features),missing=missing)
