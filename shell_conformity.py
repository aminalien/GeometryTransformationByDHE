"""Shared shell diagnostics and arithmetic-only coordinate correspondence.

Geometric modelling tolerances must not decide whether two semantic vertices
are identical. The small bounds below cover floating-point evaluation error,
not gaps in the user's geometry or different transition levels.
"""
from collections import defaultdict
from itertools import product
from math import floor
from functools import wraps

import numpy as np


def arithmetic_bound(points):
    values = np.asarray(list(points), dtype=float)
    scale = max(1., float(np.max(np.abs(values)))) if values.size else 1.
    return 64 * np.finfo(float).eps * scale


class CoordinateRegistry:
    """Share arithmetic-equivalent evaluations, separated by semantic level."""
    def __init__(self, points, dimension=3):
        points=list(points)
        values=np.asarray(points,dtype=float)
        scales=np.maximum(1.,np.max(np.abs(values),axis=0)) if values.size else np.ones(dimension)
        self.axis_bounds=64*np.finfo(float).eps*scales
        self.bound = float(np.max(self.axis_bounds))
        self.dimension = dimension
        self.bins = defaultdict(list)
        self.offsets = tuple(product((-1, 0, 1), repeat=dimension))

    def canonical(self, point, role=None):
        point = tuple(float(v) for v in point)
        key = tuple(floor(v / b) for v,b in zip(point,self.axis_bounds))
        for offset in self.offsets:
            bucket = (role, *(k+d for k,d in zip(key, offset)))
            for old in self.bins.get(bucket, ()):
                if all(abs(a-b)<=limit for a,b,limit in zip(point,old,self.axis_bounds)):
                    return old
        self.bins[(role, *key)].append(point)
        return point


class TransitionShellError(ValueError):
    """Backward-compatible ValueError with local, machine-readable evidence."""
    def __init__(self, message, classification, **evidence):
        self.report = dict(classification=classification, **evidence)
        super().__init__(f"{message} [{classification}]")


def diagnosed_transition(method):
    """Attach operation context without changing callers' ValueError contract."""
    @wraps(method)
    def wrapped(self,operation,selected,**parameters):
        selected=list(selected)
        try:
            result=method(self,operation,selected,**parameters)
            return result
        except ValueError as exc:
            report=getattr(exc,'report',dict(classification='planning_constraint',message=str(exc)))
            effective=dict(dz=10.,tolerance=1.,iterations=1,ratio=.25,winner=None)
            effective.update(parameters)
            levels=dict(z_b=min(p[2] for f in self.state.faces for p in f.points),z_s=self.state.z,z_t=self.state.z+effective['dz'])
            if operation=='aggregation':levels['z_m']=self.state.z+.8*effective['dz']
            report.update(operation=operation,selected=selected,parameters=effective,levels=levels,
                          source_z=self.state.z,case={'point':'polygon_to_point','polyline':'polygon_to_polyline',
                          'aggregation':'aggregation'}.get(operation,'polygon_to_polygon'))
            self.last_diagnostic=report
            exc.report=report
            raise
    return wrapped


def transactional_dhe_layer(method):
    """Restore live topology and model metadata on allocation/validation failure.

    The legacy layer builder appends to the existing DHE ledger and reuses old
    primal vertices. Keeping only the ledger reference is therefore not a
    rollback. Snapshot original records and mutable containers explicitly.
    """
    @wraps(method)
    def wrapped(model,*args,**kwargs):
        import DHECore as dhe
        ledger,killed,vertices=dhe.dhe,dhe.killdhe,dhe.killVertexes
        contents=list(ledger),set(killed),list(vertices)
        counters=dhe.Vertex.cntId,dhe.DualHalfEdge.cntId
        records=set();pending=list(ledger)+list(model.active_edges())
        while pending:
            record=pending.pop()
            if record in records:continue
            records.add(record)
            pending.extend(other for name in ('S','D','NF','NV')
                           if (other:=getattr(record,name,None)) is not None and other not in records)
        objects=records | {r.V for r in records if getattr(r,'V',None) is not None}
        attributes={obj:dict(obj.__dict__) for obj in objects}
        containers={}
        for values in [model.__dict__,*attributes.values()]:
            for value in values.values():
                if isinstance(value,(list,dict,set)) and id(value) not in containers:
                    containers[id(value)]=(value,value.copy())
        metadata=dict(model.__dict__)
        try:return method(model,*args,**kwargs)
        except BaseException:
            for value,copy in containers.values():
                if isinstance(value,list):value[:]=copy
                else:value.clear();value.update(copy)
            for obj,values in attributes.items():obj.__dict__.clear();obj.__dict__.update(values)
            model.__dict__.clear();model.__dict__.update(metadata)
            ledger[:]=contents[0];killed.clear();killed.update(contents[1]);vertices[:]=contents[2]
            dhe.dhe,dhe.killdhe,dhe.killVertexes=ledger,killed,vertices
            dhe.Vertex.cntId,dhe.DualHalfEdge.cntId=counters
            raise
    return wrapped


def check_interface_pairs(faces):
    groups=defaultdict(list)
    for i,f in enumerate(faces):groups[tuple(sorted(f.points))].append(i)
    for uses in groups.values():
        if len(uses)>2:
            raise TransitionShellError('An interface has more than two incident cells.','nonmanifold_interface',faces=uses)
        if len(uses)==2:
            a,b=(faces[i] for i in uses)
            ae={(p,q) for p,q in zip(a.points,a.points[1:]+a.points[:1])}
            be={(q,p) for p,q in zip(b.points,b.points[1:]+b.points[:1])}
            if a.owner==b.owner or ae!=be:
                raise TransitionShellError('A shared interface has inconsistent orientation or ownership.',
                                           'interface_orientation_or_ownership',faces=uses,owners=(a.owner,b.owner))


def check_oriented_shell(plans):
    """Check edge incidence, winding, connectivity and vertex links per cell."""
    edges = defaultdict(list)
    for index, plan in enumerate(plans):
        points = [tuple(p) for p in plan.points]
        for a,b in zip(points, points[1:]+points[:1]):
            if a == b:
                raise TransitionShellError("A planned face has a zero-length edge.", "degenerate_edge", face=index, edge=(a,b))
            edges[tuple(sorted((a,b)))].append((index,a,b,getattr(plan,"part",None)))
    bad = []
    for edge,uses in edges.items():
        if len(uses)!=2 or uses[0][1:3] != tuple(reversed(uses[1][1:3])):
            bad.append(dict(edge=edge, incidence=len(uses), faces=[u[0] for u in uses],
                            parts=[u[3] for u in uses], directions=[u[1:3] for u in uses]))
    if bad:
        kind = ("nonmanifold_edge_incidence" if any(b['incidence']>2 for b in bad)
                else "inconsistent_orientation" if any(b['incidence']==2 for b in bad)
                else "unmatched_edges")
        # Nearby coordinates alone do not prove an intended correspondence.
        raise TransitionShellError("The planned transition shell is not a consistently oriented closed manifold.",
                                   kind, unmatched_edge_count=len(bad), edges=bad)
    graph = defaultdict(set)
    for uses in edges.values():
        a,b=uses[0][0],uses[1][0]
        graph[a].add(b);graph[b].add(a)
    reached=set();pending=[0] if plans else []
    while pending:
        at=pending.pop()
        if at not in reached:
            reached.add(at);pending.extend(graph[at]-reached)
    if len(reached)!=len(plans) or not plans:
        raise TransitionShellError("The planned transition shell contains disconnected components.", "disconnected_shell")
    links=defaultdict(lambda:defaultdict(list))
    for plan in plans:
        points=[tuple(p) for p in plan.points]
        for i,p in enumerate(points):
            a,b=points[i-1],points[(i+1)%len(points)]
            links[p][a].append(b);links[p][b].append(a)
    for vertex,link in links.items():
        seen=set();pending=[next(iter(link))]
        while pending:
            at=pending.pop()
            if at not in seen:
                seen.add(at);pending.extend(link[at])
        if len(seen)!=len(link) or any(len(v)!=2 for v in link.values()):
            raise TransitionShellError("A planned vertex has a non-manifold neighbourhood.", "invalid_vertex_link", vertex=vertex)
    vertices={p for edge in edges for p in edge}
    if len(vertices)-len(edges)+len(plans)!=2:
        raise TransitionShellError("The planned transition shell does not have genus-zero topology.", "unsupported_genus")


def check_face_intersections(plans, *, across_cells=False):
    """Reject intersections beyond declared shared edges/vertices.

    Polygon/plane line clipping avoids introducing triangulation into NATR or
    cap faces. This is a numerical check, not an exact-arithmetic certificate.
    """
    from shapely.geometry import Polygon,LineString,Point,GeometryCollection
    from shapely.ops import unary_union
    arrays=[np.asarray(p.points,dtype=float) for p in plans]
    bound=arithmetic_bound([p for a in arrays for p in a])
    frames=[]
    for i,points in enumerate(arrays):
        relative=points-points[0]
        normal=np.sum(np.cross(relative,np.roll(relative,-1,axis=0)),axis=0)
        length=np.linalg.norm(normal)
        if length==0:
            raise TransitionShellError("A planned face has zero three-dimensional area.","degenerate_face",face=i)
        normal/=length
        if np.max(np.abs(relative@normal))>bound*8:
            # Keep the established planarity contract; a broader geometry
            # tolerance is never used to create shared coordinate identities.
            if np.max(np.abs(relative@normal))>1e-7:
                raise TransitionShellError("A planned face is not planar.","nonplanar_face",face=i)
        u=next(v/np.linalg.norm(v) for v in relative[1:] if np.linalg.norm(v)>0)
        v=np.cross(normal,u)
        polygon=Polygon(np.column_stack((relative@u,relative@v)))
        if not polygon.is_valid or polygon.area==0:
            raise TransitionShellError("A planned face has an invalid boundary.","invalid_face",face=i)
        frames.append((normal,u,v,polygon,points.min(axis=0),points.max(axis=0)))

    def segments(geometry,origin,direction,polygon,shared_points):
        def parameter(point):
            point=np.asarray(point)
            # A known vertex/line junction has one exact parameter. GEOS line
            # clipping can otherwise turn a tangency there into a tiny segment.
            # Use incidence on the generating edge, never an expanded radius.
            ring=list(polygon.exterior.coords)
            for first,second in zip(ring,ring[1:]):
                first,second=np.asarray(first),np.asarray(second);edge=second-first
                length=np.linalg.norm(edge)
                cross=abs(edge[0]*direction[1]-edge[1]*direction[0])
                if cross<=64*np.finfo(float).eps*length:continue
                for known in shared_points:
                    if not (np.linalg.norm(first-known)<=bound or np.linalg.norm(second-known)<=bound):continue
                    t=float((point-first)@edge/(length*length))
                    if -bound/length<=t<=1+bound/length and np.linalg.norm(point-first-t*edge)<=bound:
                        return float((known-origin)@direction/(direction@direction))
            return float((point-origin)@direction/(direction@direction))
        if geometry.is_empty:return []
        if geometry.geom_type=='Point':
            t=parameter(geometry.coords[0])
            return [(t,t)]
        if geometry.geom_type=='LineString':
            ts=[parameter(p) for p in geometry.coords]
            return [(min(ts),max(ts))]
        return [piece for g in geometry.geoms for piece in segments(g,origin,direction,polygon,shared_points)]

    minima=np.array([f[4] for f in frames]);maxima=np.array([f[5] for f in frames])
    for i,a in enumerate(arrays):
        na,ua,va,pa,amin,amax=frames[i]
        def project(points):
            rel=np.asarray(points)-a[0]
            return np.column_stack((rel@ua,rel@va))
        aset={tuple(p) for p in a}
        aedges={frozenset((tuple(p),tuple(q))) for p,q in zip(a,np.roll(a,-1,axis=0))}
        candidates=np.flatnonzero(np.all(minima[:i]<=amax+bound,axis=1)&np.all(maxima[:i]>=amin-bound,axis=1))
        for j in candidates:
            if across_cells:
                if plans[i].owner==plans[j].owner:continue
                if frozenset(map(tuple,a))==frozenset(map(tuple,arrays[j])):continue
            b=arrays[j];nb,ub,vb,pb,bmin,bmax=frames[j]
            if np.any(amin>bmax+bound) or np.any(bmin>amax+bound):continue
            da=(b-a[0])@na;db=(a-b[0])@nb
            if da.min()>bound or da.max()<-bound or db.min()>bound or db.max()<-bound:continue
            shared=aset & {tuple(p) for p in b}
            bedges={frozenset((tuple(p),tuple(q))) for p,q in zip(b,np.roll(b,-1,axis=0))}
            common_edges=aedges & bedges
            allowed=[Point(project([p])[0]) for p in shared]
            allowed.extend(LineString(project(list(edge))) for edge in common_edges)
            permitted=unary_union(allowed) if allowed else GeometryCollection()
            def permitted_contact(geometry):
                if geometry.is_empty:return True
                if permitted.is_empty:return False
                if geometry.geom_type=='Point':return permitted.distance(geometry)<=bound*8
                if geometry.geom_type=='LineString':
                    coords=list(geometry.coords)
                    lines=[project(list(edge)) for edge in common_edges]
                    for first,second in zip(coords,coords[1:]):
                        first,second=np.array(first),np.array(second);delta=second-first
                        length=np.linalg.norm(delta)
                        if length<=bound:
                            if permitted.distance(Point(first))>bound*8:return False
                            continue
                        intervals=[]
                        for x,y in lines:
                            tx,ty=float((x-first)@delta/(length*length)),float((y-first)@delta/(length*length))
                            if np.linalg.norm(x-first-tx*delta)<=bound*8 and np.linalg.norm(y-first-ty*delta)<=bound*8:
                                intervals.append((max(0.,min(tx,ty)),min(1.,max(tx,ty))))
                        end=0.
                        for low,high in sorted(intervals):
                            if high<0 or low>1:continue
                            if (low-end)*length>bound*8:return False
                            end=max(end,high)
                        if (1-end)*length>bound*8:return False
                    return True
                if geometry.geom_type=='Polygon':
                    if geometry.area>bound*geometry.length*8:return False
                    return all(permitted.distance(Point(p))<=bound*8 for p in geometry.exterior.coords)
                return all(permitted_contact(g) for g in geometry.geoms)
            direction=np.cross(na,nb);norm=np.linalg.norm(direction)
            if norm<=64*np.finfo(float).eps:
                if np.max(np.abs(da))>bound:continue
                overlap=pa.intersection(Polygon(project(b)))
                if not permitted_contact(overlap):
                    raise TransitionShellError("Planned faces overlap beyond their declared interface.","self_intersection",faces=(int(j),i),coplanar=True,contact=overlap.wkt,permitted=permitted.wkt,
                        face_points=[arrays[j].tolist(),a.tolist()],parts=[getattr(plans[k],'part',None) for k in (j,i)],owners=[getattr(plans[k],'owner',None) for k in (j,i)])
                continue
            direction/=norm
            if common_edges:
                first,second=next(iter(common_edges));origin=np.array(first)
                direction=np.subtract(second,first);direction/=np.linalg.norm(direction)
            elif shared:origin=np.array(next(iter(shared)))
            else:
                cross=np.cross(na,nb)
                origin=a[0]+float(nb@(b[0]-a[0]))*np.cross(cross,na)/(cross@cross)
            ts=np.concatenate(((a-origin)@direction,(b-origin)@direction))
            endpoints=np.array([origin+(ts.min()-1)*direction,origin+(ts.max()+1)*direction])
            ao=project([origin])[0];ad=np.array([direction@ua,direction@va])
            bo=np.array([(origin-b[0])@ub,(origin-b[0])@vb]);bd=np.array([direction@ub,direction@vb])
            ashared=list(project(list(shared))) if shared else []
            sa=segments(pa.intersection(LineString(project(endpoints))),ao,ad,pa,ashared)
            brel=endpoints-b[0]
            bshared=[np.array([(np.array(p)-b[0])@ub,(np.array(p)-b[0])@vb]) for p in shared]
            sb=segments(pb.intersection(LineString(np.column_stack((brel@ub,brel@vb)))),bo,bd,pb,bshared)
            for la,ha in sa:
                for lb,hb in sb:
                    low,high=max(la,lb),min(ha,hb)
                    if high<low-bound:continue
                    if common_edges:
                        intervals=sorted(tuple(sorted(float((np.array(p)-origin)@direction) for p in edge)) for edge in common_edges)
                        end=low
                        for start,stop in intervals:
                            if stop<end-bound:continue
                            if start>end+bound:break
                            end=max(end,stop)
                        if end>=high-bound:continue
                    if high-low<=bound:
                        contact=Point(project([origin+(low+high)/2*direction])[0])
                    else:contact=LineString(project([origin+low*direction,origin+high*direction]))
                    if not permitted_contact(contact):
                        raise TransitionShellError("Planned faces intersect beyond their declared interface.","self_intersection",faces=(int(j),i),coplanar=False,contact=contact.wkt,permitted=permitted.wkt,
                            face_points=[arrays[j].tolist(),a.tolist()],parts=[getattr(plans[k],'part',None) for k in (j,i)],owners=[getattr(plans[k],'owner',None) for k in (j,i)])
