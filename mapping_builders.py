"""Coordinate plans and validated DHE construction for independent map layers."""
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from math import isfinite
from copy import deepcopy

import numpy as np
from shapely import constrained_delaunay_triangles,STRtree
from shapely.affinity import translate
from shapely.geometry import Polygon,Point,LineString
from shapely.geometry.polygon import orient
from shapely.ops import unary_union,polygonize,nearest_points

import DHECore as dhe
from generalization_model import (Face,_prism,_polygon_patches,_collapse_patches,_layer_faces,
                                 _conform_footprints,realize,_core,_restore)
from mapping_data import polygons,validate_groups,route_group,overlap_review,member_records,review_summary,validate_id_groups
from shell_conformity import CoordinateRegistry,arithmetic_bound


class MappingBuildError(ValueError):
    def __init__(self,message,**report):
        self.report=dict(classification='mapping_construction',**report);super().__init__(message)


def rigid_correspondence(source,target,tolerance=.01,anchor=None):
    if source.interiors or target.interiors:return None
    a=np.asarray(orient(source,1).exterior.coords[:-1],dtype=float)
    b=np.asarray(orient(target,1).exterior.coords[:-1],dtype=float)
    if len(a)!=len(b):return None
    centered=a-a.mean(axis=0);best=None
    shifts=range(len(b)) if anchor is None else [int(anchor)%len(b)]
    for shift in shifts:
        q=np.roll(b,-shift,axis=0);other=q-q.mean(axis=0)
        u,_,vt=np.linalg.svd(centered.T@other);rotation=u@vt
        if np.linalg.det(rotation)<0:u[:,-1]*=-1;rotation=u@vt
        residual=float(np.max(np.linalg.norm(centered@rotation-other,axis=1)))
        travel=float(np.sum((a-q)**2))
        score=(residual,travel,shift)
        if best is None or score<best[0]:best=(score,a,q)
    if anchor is None and best[0][0]>tolerance:return None
    return dict(source=best[1],target=best[2],residual=best[0][0],anchor=best[0][2])


def direct_faces(owner,pair,lo,hi):
    a,b=pair['source'],pair['target'];result=[]
    for i in range(len(a)):
        j=(i+1)%len(a);p,q,r,s=(*a[i],lo),(*a[j],lo),(*b[j],hi),(*b[i],hi)
        result.extend([Face(owner,[p,q,r],'mapping'),Face(owner,[p,r,s],'mapping')])
    result.extend([Face(owner,[(*p,lo) for p in reversed(a)],'bottom'),
                   Face(owner,[(*p,hi) for p in b],'top')])
    return result


def polygon_faces(owner,source,target,lo,hi,tolerance,anchor=None):
    if source.equals(target):return _prism(owner,source,lo,hi),None
    pair=rigid_correspondence(source,target,tolerance,anchor)
    if pair is not None:return direct_faces(owner,pair,lo,hi),pair
    patches,_=_polygon_patches(owner,source,target,lo,hi)
    faces=_layer_faces({owner:source},{owner:target},patches,lo,hi)
    faces.append(Face(owner,[(*p,lo) for p in orient(source,-1).exterior.coords[:-1]],'bottom'))
    return faces,None


def smooth_faces(owner,sources,target,lo,hi,tolerance,anchor=None):
    if any(g.interiors for g in sources) or getattr(target,'interiors',()):
        raise ValueError('Smooth mapping requires simple hole-free polygon components.')
    if len(sources)>1:
        if target.geom_type!='Polygon':raise ValueError('Smooth aggregation requires one polygon target.')
        from aggregation_geometry import build_aggregation_preview
        from aggregation_layer import _plan_upper_shell
        mid=(lo+hi)/2
        preview=build_aggregation_preview({str(i):g for i,g in enumerate(sources)},tolerance=1e-7)
        plans,_,_=_plan_upper_shell(preview,lo,lo+.8*(mid-lo),mid)
        faces=[Face(owner,list(p.points),'mapping') for p in plans]
        upper,pair=polygon_faces(owner,preview.hull,target,mid,hi,tolerance,anchor)
        # The same-owner hull cap and bottom cancel during coordinate noding.
        return faces+upper,dict(stages=['skeleton_to_hull','hull_to_supplied_target'],stage_z=mid)
    source=sources[0]
    if target.geom_type=='Polygon':return polygon_faces(owner,source,target,lo,hi,tolerance,anchor)
    if target.geom_type=='Point':
        if not source.contains(target) or any(not source.covers(LineString([p,target.coords[0]])) for p in source.exterior.coords[:-1]):
            raise ValueError('The supplied point is not visible from every source boundary vertex.')
    elif target.geom_type=='LineString':
        from generalization_geometry import _valid_ridge
        if not _valid_ridge(source,target):raise ValueError('The supplied line is not a supported interior collapse ridge.')
    else:raise ValueError('Unsupported Smooth target component.')
    patches=_collapse_patches(owner,source,target,lo,hi)
    faces=_layer_faces({owner:source},{},patches,lo,hi)
    faces.append(Face(owner,[(*p,lo) for p in orient(source,-1).exterior.coords[:-1]],'bottom'))
    return faces,None


def centroid_faces(owner,polygon,lo,hi,*,appearance=False):
    """Homothetic cone, including concave bases with an exterior centroid.

    Unlike a supplied interior collapse point, this generated apex describes
    uniform scaling in XY. Its side facets need not project inside the base.
    """
    center=polygon.centroid
    ring=list(orient(polygon,1).exterior.coords[:-1])
    apex=(center.x,center.y,lo if appearance else hi)
    base_z=hi if appearance else lo
    faces=[Face(owner,[(*a,base_z),(*b,base_z),apex],'mapping')
           for a,b in zip(ring,ring[1:]+ring[:1])]
    if appearance:
        faces=[Face(f.owner,list(reversed(f.points)),f.part) for f in faces]
    faces.append(Face(owner,[(*p,base_z) for p in (ring if appearance else reversed(ring))],
                      'top' if appearance else 'bottom'))
    return faces


def _partition_plane(faces,extra_lines=()):
    """Split coplanar faces into common pieces, preserving all incident owners."""
    first=np.asarray(faces[0].points,dtype=float);origin=first[0]
    n=np.sum(np.cross(first-origin,np.roll(first-origin,-1,axis=0)),axis=0);n/=np.linalg.norm(n)
    if all(len({p[2] for p in f.points})==1 for f in faces):
        origin=np.array([0.,0.,first[0,2]]);n=np.array([0.,0.,1.]);u=np.array([1.,0.,0.]);v=np.array([0.,1.,0.])
    elif abs(n[2])<=arithmetic_bound(first):
        # A diagonal 3D basis mixes XY and Z. Its inverse can turn an assigned
        # interface Z=10 into 9.999999999999998, splitting semantic level roles.
        origin=np.array([first[0,0],first[0,1],0.])
        delta=max(first-origin,key=lambda p:np.linalg.norm(p[:2]))[:2]
        u=np.array([delta[0],delta[1],0.]);u/=np.linalg.norm(u)
        v=np.array([0.,0.,1.]);n=np.cross(u,v)
    else:
        u=max(first-origin,key=lambda p:np.linalg.norm(p));u=u/np.linalg.norm(u);v=np.cross(n,u)
    project=lambda p:np.column_stack(((np.asarray(p)-origin)@u,(np.asarray(p)-origin)@v))
    ps=[Polygon(project(f.points)) for f in faces]
    network=[p.boundary for p in ps]
    network.extend(LineString(project(line)) for line in extra_lines)
    if extra_lines:
        points=CoordinateRegistry([p for line in network for p in line.coords],dimension=2)
        nodes=np.asarray(sorted({points.canonical(p) for line in network for p in line.coords}))
        noded=[]
        for line in network:
            coords=[]
            for a,b in zip(line.coords,list(line.coords)[1:]):
                a=np.asarray(a);d=np.subtract(b,a);l2=d@d
                if l2==0:continue
                t=(nodes-a)@d/l2
                hits=np.flatnonzero((t>0)&(t<1)&np.all(np.abs(nodes-a-t[:,None]*d)<=points.axis_bounds,axis=1))
                coords.append(points.canonical(a));coords.extend(tuple(nodes[i]) for i in sorted(hits,key=lambda i:t[i]))
            coords.append(points.canonical(line.coords[-1]));coords=[p for i,p in enumerate(coords) if i==0 or p!=coords[i-1]]
            if len(coords)>1:noded.append(LineString(coords))
        network=noded
    registry=CoordinateRegistry([p for f in faces for p in f.points]+[p for line in extra_lines for p in line])
    for line in extra_lines:
        for p in line:registry.canonical(p)
    for f in faces:
        for p in f.points:registry.canonical(p)
    result=[];tree=STRtree(ps)
    for region in polygonize(unary_union(network)):
        point=region.representative_point();owners=tree.query(point,predicate='covered_by')
        if len(owners)==0:continue
        pieces=list(constrained_delaunay_triangles(region).geoms) if region.interiors else [region]
        for piece in pieces:
            ring=np.array(piece.exterior.coords[:-1]);span=max(np.linalg.norm(p-ring[0]) for p in ring)
            if span==0 or 2*piece.area/span<=registry.bound:continue
            points=[registry.canonical(origin+x*u+y*v) for x,y in orient(piece,1).exterior.coords[:-1]]
            for i in owners:
                arr=np.asarray(faces[i].points)-origin
                normal=np.sum(np.cross(arr,np.roll(arr,-1,axis=0)),axis=0)
                result.append(Face(faces[i].owner,list(points if normal@n>0 else reversed(points)),faces[i].part))
    return result


def conform_interfaces(faces,contact_lines):
    horizontal=defaultdict(list);vertical=defaultdict(list);other=[]
    for f in faces:
        a=np.asarray(f.points,dtype=float);normal=np.sum(np.cross(a-a[0],np.roll(a-a[0],-1,axis=0)),axis=0)
        if len(set(a[:,2]))==1:horizontal[float(a[0,2])].append(f)
        elif abs(normal[2])<=arithmetic_bound(a)*np.linalg.norm(normal):
            delta=max(a-a[0],key=lambda p:np.linalg.norm(p[:2]))[:2];length=np.linalg.norm(delta)
            if length==0:other.append(f);continue
            direction=delta/length
            if tuple(direction)<tuple(-direction):direction=-direction
            n=np.array([-direction[1],direction[0]])
            vertical[(*np.round(n,9),round(float(n@a[0,:2]),7))].append(f)
        else:other.append(f)
    for z,fs in horizontal.items():other.extend(_partition_plane(fs,contact_lines.get(z,())))
    for fs in vertical.values():
        # Plane bins are only candidate acceleration; never a snap tolerance.
        groups=[]
        for f in fs:
            a=np.asarray(f.points);na=np.sum(np.cross(a-a[0],np.roll(a-a[0],-1,axis=0)),axis=0);na/=np.linalg.norm(na)
            for group in groups:
                b=np.asarray(group[0].points);nb=np.sum(np.cross(b-b[0],np.roll(b-b[0],-1,axis=0)),axis=0);nb/=np.linalg.norm(nb)
                if np.max(np.abs((b-a[0])@na))<=arithmetic_bound(a) and np.max(np.abs((a-b[0])@nb))<=arithmetic_bound(b):
                    group.append(f);break
            else:groups.append([f])
        for group in groups:other.extend(_partition_plane(group) if len(group)>1 else group)
    return other


def _make_face(points,dual,exterior,vertices):
    vs=[vertices.setdefault(tuple(p),None) for p in points]
    for i,p in enumerate(points):
        if vs[i] is None:vs[i]=vertices[tuple(p)]=dhe.make_vertex(*p)
    first=dhe.MEVVFS(vs[0],vs[1],dual,exterior);cursor=first
    for vertex in vs[2:]:cursor=dhe.MEV(vertex,cursor.S)
    return dhe.MEF(first,cursor.S).S


def add_primitives(model,specs,metadata):
    """Real two-sided DHE sheets and two-half-edge DHE edge primitives.

    Their contact registry references the SAME primal Vertex objects as the
    solid complex. Edge/vertex incidence is explicit and reciprocal; it is not
    misrepresented as a shared-face adjacency in the legacy solid validator.
    """
    from main import ExtrudedCell
    vertices={tuple((e.V.x,e.V.y,e.V.z)):e.V for e in model.active_edges()}
    primitives=[];records=[]
    for owner,points,dimension in specs:
        center=np.mean(points,axis=0);dual=dhe.make_vertex(*center,ch=str(owner));dual.logical_feature_id=str(owner)
        if dimension==1:
            vs=[]
            for p in points:
                if tuple(p) not in vertices:vertices[tuple(p)]=dhe.make_vertex(*p)
                vs.append(vertices[tuple(p)])
            seed=dhe.MEVVFS(vs[0],vs[1],dual,model.exterior)
            records.append((Face(owner,points,'edge_primitive'),seed))
        else:
            # Include every already-established contact vertex on the boundary.
            # Do not use its coincident-face dissolution for the two sheet sides.
            boundary=list(points);all_vertices=np.asarray(list(vertices))
            ring=[]
            for a,b in zip(boundary,boundary[1:]+boundary[:1]):
                av=np.asarray(a);d=np.subtract(b,a);l2=d@d;ring.append(a)
                if len(all_vertices):
                    t=(all_vertices-av)@d/l2;bound=arithmetic_bound(boundary)
                    hits=np.flatnonzero((t>bound/np.sqrt(l2))&(t<1-bound/np.sqrt(l2))&(np.linalg.norm(all_vertices-av-t[:,None]*d,axis=1)<=bound))
                    ring.extend(tuple(all_vertices[i]) for i in sorted(hits,key=lambda i:t[i]))
            front=_make_face(ring,dual,model.exterior,vertices)
            back=_make_face(list(reversed(ring)),dual,model.exterior,vertices)
            paired=defaultdict(list)
            for s in (front,back):
                for e in dhe.face_edges(s):paired[frozenset((e.V,e.S.V))].append(e)
            for uses in paired.values():dhe.SewComplex(uses[0],uses[1].S)
            seed=front;records.extend([(Face(owner,ring,'sheet_primitive'),front),
                                      (Face(owner,list(reversed(ring)),'sheet_primitive'),back)])
        cell=ExtrudedCell(str(owner),seed,dual);cell.mapping_dimension=dimension;primitives.append(cell)
    all_cells=[*model.cells,*primitives]
    dhe.validate_complex_topology(((c.seed,c.dual_vertex) for c in all_cells),model.exterior)
    for cell in primitives:
        es=dhe.Cell(cell.seed);vs={e.V for e in es};fs=dhe.get_UniqueFace_in_cell(cell.seed)
        if cell.mapping_dimension==1 and (len(vs)!=2 or len(fs)!=1):raise ValueError('Invalid DHE point-extrusion edge primitive.')
        if cell.mapping_dimension==2 and (len(vs)<3 or len(fs)!=2):raise ValueError('Invalid two-sided DHE line-transition sheet.')
    # Explicit lower-dimensional incidences, including containment bridge nodes.
    edge_cells=defaultdict(dict);vertex_cells=defaultdict(dict)
    for cell in all_cells:
        for e in dhe.Cell(cell.seed):
            edge_cells[frozenset((e.V,e.S.V))].setdefault(int(cell.feature_id),e)
            vertex_cells[e.V].setdefault(int(cell.feature_id),e)
    contacts=[];seen=set();primitive_ids={int(c.feature_id) for c in primitives}
    for dimension,registry in ((1,edge_cells),(0,vertex_cells)):
        for entity,uses in registry.items():
            ids=sorted(uses)
            for i,a in enumerate(ids):
                for b in ids[:i]:
                    if not ({a,b}&primitive_ids):continue
                    key=(a,b,dimension)
                    if key in seen:continue
                    seen.add(key);ea,eb=uses[a],uses[b]
                    for first,second in ((ea,eb),(eb,ea)):
                        if not hasattr(first,'mapping_incident'):first.mapping_incident=[]
                        first.mapping_incident.append(second)
                    contacts.append(dict(owners=[a,b],dimension=dimension,first=ea,second=eb))
    model.mapping_primitives=primitives;model.mapping_contacts=contacts
    model.mapping_face_records=records
    return model


@dataclass
class MappingResult:
    model:object
    faces:list
    core:tuple
    metadata:dict
    geometries:dict
    origin:tuple
    crs:object
    levels:dict
    report:dict


def _mapping_failure(exc,metadata,routing,phase,*,group=None):
    """Attach semantic identities without guessing from a transient cell number."""
    evidence=getattr(exc,'report',{})
    failures=[]
    for item in evidence.get('failures',[evidence]):
        owners=set(item.get('owners',[]))
        if item.get('cell') is not None:owners.add(item['cell'])
        owners.update(c[key] for c in item.get('columns',[]) for key in ('old','new') if c.get(key) is not None)
        if group is not None:owners.update(k for k,m in metadata.items() if m['group']==group['id'])
        features=[dict(cell=int(k),feature_id=metadata[k]['feature_id'],original_id=metadata[k]['original_id'],
                       layer=metadata[k]['layer'],group=metadata[k]['group']) for k in sorted(owners) if k in metadata]
        groups={f['group'] for f in features}
        if group is not None:groups.add(group['id'])
        failures.append({**item,'features':features,'groups':sorted(groups)})
    identified=[f for failure in failures for f in failure['features']]
    names=', '.join(dict.fromkeys(f"{f['layer']} {f['feature_id']} (original {f['original_id']}, group {f['group']}, cell {f['cell']})" for f in identified))
    classifications=', '.join(dict.fromkeys(f.get('classification','construction_error') for f in failures))
    message=f'{names}: {classifications} during {phase}.' if names else str(exc)
    return MappingBuildError(message,phase=phase,routing=routing,evidence=evidence,failures=failures)


def _build_mapping_once(source,target,groups,settings,*,forced_classic,validated_shells,progress,cancelled):
    validate_groups(groups,source,target)
    for layer in (source,target):
        if overlap_review(layer):raise MappingBuildError(f'Resolve {layer.side} layer overlaps before construction.',side=layer.side)
    lo=float(settings.get('source_z',5.));hi=float(settings.get('target_z',15.));tol=float(settings.get('geometry_tolerance',.01))
    if not all(isfinite(x) for x in (lo,hi,tol)) or not 0<lo<hi or tol<=0:raise ValueError('Require 0 < source Z < target Z and a positive geometry tolerance.')
    mode=settings.get('mode','hybrid');mid=(lo+hi)/2
    origin=(min(f.geometry.bounds[0] for l in (source,target) for f in l.features.values()),
            min(f.geometry.bounds[1] for l in (source,target) for f in l.features.values()))
    local={f.uid:translate(f.geometry,xoff=-origin[0],yoff=-origin[1]) for l in (source,target) for f in l.features.values()}
    # Conform each polygon component without changing its feature identity.
    components={};parents={};decompositions=[]
    excluded={k for g in groups if g['decision']=='exclude' for k in [*g['sources'],*g['targets']]}
    for uid,g in local.items():
        if uid in excluded:continue
        for i,p in enumerate(polygons(g)):
            pieces=list(constrained_delaunay_triangles(p).geoms) if p.interiors else [p]
            if p.interiors:
                difference=float(unary_union(pieces).symmetric_difference(p).area)
                if any(q.interiors or not q.is_valid for q in pieces) or difference>max(1e-9,p.area*1e-12):
                    raise MappingBuildError(f'{uid}: automatic hole decomposition did not preserve the footprint.',feature=uid)
                decompositions.append(dict(feature=uid,component=i,holes=len(p.interiors),parts=len(pieces),
                                           hole_area=sum(Polygon(r).area for r in p.interiors),area_difference=difference))
            for j,piece in enumerate(pieces):
                key=f'{uid}/{i}/{j}';components[key]=piece;parents[key]=uid
    conformed=_conform_footprints(components) if components else {}
    local_components=defaultdict(list)
    for key,p in conformed.items():local_components[parents[key]].append(p)
    metadata={};geometries={};owner=0;faces=[];specs=[];contacts=defaultdict(list);routing=[]
    def allocate(uid,g,group,layer,dimension=3):
        nonlocal owner
        owner+=1;f=(source if layer=='source' else target).features[uid]
        metadata[owner]=dict(feature_id=uid,original_id=f.original_id,layer=layer,group=group,dimension=dimension,attributes=f.attributes,members=list(f.members),member_records=list(member_records(f)))
        geometries[owner]=g
        return owner
    for index,group in enumerate(groups,1):
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        builder,reason=route_group(group,source,target,mode)
        initial_builder,initial_reason=builder,reason
        if group['id'] in forced_classic:
            builder='classic';reason='Classic retry after '+', '.join(sorted({f['classification'] for f in forced_classic[group['id']]['failures']}))
        row=dict(group=group['id'],sources=group['sources'],targets=group['targets'],builder=builder,reason=reason,
                 initial_builder=initial_builder,initial_reason=initial_reason)
        if group['id'] in forced_classic:row['fallback']=forced_classic[group['id']]
        routing.append(row)
        if builder=='excluded':continue
        if builder=='unsupported':raise MappingBuildError(f"{group['id']}: {reason}",group=group['id'],routing=routing)
        progress(f"Planning mapping group {index}/{len(groups)}: {builder}")
        unmatched=builder=='smooth' and group['decision'] in ('appearance','disappearance')
        side='source' if group['decision']=='disappearance' else 'target'
        present=group['sources'] if side=='source' else group['targets']
        polygon_ids=[uid for uid in present if local[uid].geom_type in ('Polygon','MultiPolygon')] if unmatched else []
        parts=[p for uid in polygon_ids for p in polygons(local[uid])]
        multipart=len(parts)>1
        source_parts=[]
        for uid in group['sources']:
            for g in local_components[uid]:
                o=allocate(uid,g,group['id'],'source');faces.extend(_prism(o,g,0.,lo));source_parts.append(g)
                if builder=='classic':faces.extend(_prism(o,g,lo,mid))
                elif unmatched and multipart:faces.extend(_prism(o,g,lo,mid))
        try:
            if unmatched:
                appearance=group['decision']=='appearance'
                # Intermediate cells carry reviewed provenance but never become
                # extra endpoint features in the scene or the input layers.
                plans=[(polygon_ids,unary_union(parts).convex_hull if multipart else parts[0])] if parts else []
                plans.extend(([uid],local[uid]) for uid in present if uid not in polygon_ids)
                construction=[]
                for ids,geometry in plans:
                    uid=ids[0];center=geometry.centroid
                    staged=multipart and geometry.geom_type=='Polygon'
                    details=dict(direction=group['decision'],feature_ids=ids,centroid=[center.x+origin[0],center.y+origin[1]],
                                 centroid_crs=source.crs.to_string(),
                                 stages=(['centroid_to_hull','classic_to_targets'] if appearance else
                                         ['classic_to_hull','hull_to_centroid']) if staged else
                                        ['centroid_to_target' if appearance else 'source_to_centroid'])
                    if staged:details['stage_z']=mid
                    construction.append(details)
                    if geometry.geom_type=='Point':
                        o=allocate(uid,geometry,group['id'],side,1)
                        specs.append((o,[(center.x,center.y,lo),(center.x,center.y,hi)],1))
                    elif geometry.geom_type=='LineString':
                        apex=(center.x,center.y,lo)
                        for a,b in zip(geometry.coords,list(geometry.coords)[1:]):
                            o=allocate(uid,LineString([a,b]),group['id'],side,2)
                            specs.append((o,[apex,(*a,hi),(*b,hi)],2))
                    else:
                        o=allocate(uid,geometry,group['id'],side)
                        if staged:
                            layer=source if side=='source' else target
                            metadata[o].update(intermediate=True,
                                members=[k for k in ids for k in layer.features[k].members],
                                member_records=[r for k in ids for r in member_records(layer.features[k])])
                        faces.extend(centroid_faces(o,geometry,lo if appearance or not staged else mid,
                                                    mid if appearance and staged else hi,appearance=appearance))
                        if appearance and staged:
                            for uid in ids:
                                for p in local_components[uid]:
                                    o=allocate(uid,p,group['id'],'target');faces.extend(_prism(o,p,mid,hi))
                row['construction']=construction[0] if len(construction)==1 else dict(direction=group['decision'],transitions=construction)
            elif builder=='classic':
                for uid in group['targets']:
                    g=local[uid]
                    if g.geom_type in ('Polygon','MultiPolygon'):
                        for p in local_components[uid]:
                            o=allocate(uid,p,group['id'],'target');faces.extend(_prism(o,p,mid,hi))
                    elif g.geom_type=='Point':
                        o=allocate(uid,g,group['id'],'target',1);specs.append((o,[(g.x,g.y,mid),(g.x,g.y,hi)],1))
                        for p in source_parts:
                            if p.covers(g):
                                for v in p.exterior.coords[:-1]:
                                    line=LineString([v,g.coords[0]])
                                    if p.covers(line):contacts[mid].append([(*v,mid),(g.x,g.y,mid)])
                    else:
                        ring=list(g.coords)
                        for a,b in zip(ring,ring[1:]):
                            o=allocate(uid,LineString([a,b]),group['id'],'target',2)
                            specs.append((o,[(*a,mid),(*b,mid),(*b,hi),(*a,hi)],2))
                        contacts[mid].append([(*p,mid) for p in ring])
                        union=unary_union(source_parts)
                        for end in (ring[0],ring[-1]):
                            if union.contains(Point(end)):
                                boundary=nearest_points(Point(end),union.boundary)[1]
                                contacts[mid].append([(*end,mid),(*boundary.coords[0],mid)])
            else:
                uid=group['targets'][0];target_geometry=local[uid]
                if target_geometry.geom_type=='MultiPolygon':raise ValueError('Smooth mapping requires a single target polygon component.')
                o=allocate(uid,target_geometry,group['id'],'target')
                # Overlay nodes may split corresponding straight edges at different
                # offsets. Pair the supplied rings first; final noding conforms
                # their transition to the lower source cells afterwards.
                supplied_sources=[p for k in group['sources'] for p in polygons(local[k])]
                planned,details=smooth_faces(o,supplied_sources,target_geometry,lo,hi,tol,group.get('anchor'))
                faces.extend(planned)
                if details is not None:
                    row['construction']=({k:v for k,v in details.items() if k not in ('source','target')} if isinstance(details,dict) else details)
        except ValueError as exc:
            failure=_mapping_failure(exc,metadata,routing,'group_planning',group=group)
            raise failure from exc
    saved=_core()
    try:
        progress('Conforming common interfaces and validating the complete mapping')
        faces=conform_interfaces(faces,contacts) if faces else []
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        if faces:model,faces,_=realize(faces,origin,required_vertices=[tuple(p) for _,points,_ in specs for p in points],collect_shell_errors=True,validated_shells=validated_shells)
        else:
            from main import ExtrusionModel
            dhe.reset_globals();ext=dhe.make_vertex(0,0,0,ch='VINF');ext.logical_feature_id='VINF'
            model=ExtrusionModel([],ext,origin,[]);model.generalization_face_records=[]
        if specs:add_primitives(model,specs,metadata)
        else:model.mapping_primitives=[];model.mapping_contacts=[];model.mapping_face_records=[]
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        report=dict(mode=mode,settings=deepcopy(settings),groups=deepcopy(groups),routing=routing,source=source.source,target=target.source,
                    source_repairs=list(source.notes),target_repairs=list(target.notes),cells=len(model.cells),
                    logical_features=dict(source=len({m['feature_id'] for m in metadata.values() if m['layer']=='source'}),target=len({m['feature_id'] for m in metadata.values() if m['layer']=='target'})),
                    decompositions=decompositions,automatic_review=review_summary(dict(source=source,target=target),groups),
                    primitives=len(model.mapping_primitives),contacts=len(model.mapping_contacts),validation='passed',
                    classic_interface_z=mid,provenance_policy='Geometric DHE adjacency is distinct from reviewed semantic correspondence')
        return MappingResult(model,faces,_core(),metadata,geometries,origin,source.crs,
                             dict(z_b=0.,z_s=lo,z_t=hi,z_interface=mid),report)
    except ValueError as exc:
        if isinstance(exc,MappingBuildError):raise
        raise _mapping_failure(exc,metadata,routing,'combined_validation') from exc
    finally:_restore(saved)


# Only diagnosed construction failures qualify. Parameter, cancellation, I/O and
# unexpected allocation errors retain their original failure behavior.
_RETRYABLE_TOPOLOGY={'unmatched_edges','nonmanifold_edge_incidence','inconsistent_orientation',
                    'invalid_vertex_link','disconnected_shell','unsupported_genus','self_intersection',
                    'nonmanifold_interface','interface_orientation_or_ownership','degenerate_edge',
                    'transition_column_incidence'}


def build_mapping(source,target,groups,settings,*,progress=lambda *a:None,cancelled=lambda:False):
    validate_groups(groups,source,target)
    if settings.get('match_by_id',False):validate_id_groups(groups,source,target)
    initial={g['id']:route_group(g,source,target,settings.get('mode','hybrid'))[0] for g in groups}
    forced={};history=[];validated_shells=set()
    while True:
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        try:
            result=_build_mapping_once(source,target,groups,settings,forced_classic=forced,validated_shells=validated_shells,progress=progress,cancelled=cancelled)
            result.report.update(construction_attempts=len(history)+1,retry_history=deepcopy(history))
            return result
        except MappingBuildError as exc:
            if cancelled():raise ValueError('Mapping cancelled; previous models preserved.') from exc
            exc.report['retry_history']=deepcopy(history)
            failures=exc.report.get('failures',[]);eligible=set()
            if settings.get('mode','hybrid')!='hybrid' or not failures:raise
            for failure in failures:
                candidates={g for g in failure['groups'] if initial.get(g)=='smooth' and g not in forced}
                if failure.get('classification') not in _RETRYABLE_TOPOLOGY or not candidates:raise
                eligible.update(candidates)
            attempt=len(history)+1
            history.append(dict(attempt=attempt,groups=sorted(eligible),failures=deepcopy(failures)))
            for group in sorted(eligible):
                evidence=[f for f in failures if group in f['groups']]
                forced[group]=dict(attempt=attempt,failures=deepcopy(evidence))
                progress(f'Retrying {group} through Classic: '+', '.join(sorted({f['classification'] for f in evidence})))
