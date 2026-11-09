"""Read-only V/E/F counts of closed portions of realized cell boundaries."""
from collections import defaultdict

import numpy as np
from shapely.geometry import LineString,Polygon,box
from shapely.geometry.polygon import orient

from plane_slicing import _regions_from_lines
from shell_conformity import CoordinateRegistry,check_oriented_shell


def _polygons(geometry):
    if geometry.is_empty:return
    if geometry.geom_type=='Polygon':yield geometry
    elif hasattr(geometry,'geoms'):
        for child in geometry.geoms:yield from _polygons(child)


def _uses(faces):
    uses=defaultdict(list)
    for rings in faces:
        for ring in rings:
            for a,b in zip(ring,ring[1:]+ring[:1]):
                if a!=b:uses[tuple(sorted((a,b)))].append((a,b))
    return uses


def _node(faces,registry):
    """Retain the same boundary nodes on every incident ring, including caps."""
    nodes=np.array(sorted({p for rings in faces for ring in rings for p in ring}))
    result=[]
    for rings in faces:
        output=[]
        for ring in rings:
            points=[]
            for a,b in zip(ring,ring[1:]+ring[:1]):
                av=np.asarray(a);delta=np.subtract(b,a);length2=delta@delta
                if length2==0:continue
                resolution=float(np.max(np.abs(delta)/registry.axis_bounds))
                if resolution<=1:raise ValueError('A clipped edge is below arithmetic resolution.')
                t=(nodes-av)@delta/length2
                hits=np.flatnonzero((t>1/resolution)&(t<1-1/resolution)&
                    np.all(np.abs(nodes-av-t[:,None]*delta)<=registry.axis_bounds,axis=1))
                points.append(a)
                points.extend(tuple(nodes[i]) for i in sorted(hits,key=lambda i:t[i]))
            output.append(points)
        result.append(output)
    return result


def range_counts(faces,lo,hi):
    """Clip immutable oriented faces; count caps without allocating DHE records.

    Each connected polygon counts as one face, including a cap with inner rings.
    Existing coplanar subdivisions are retained. Disconnected solid portions are
    counted together; no shared entities are deduplicated across different cells.
    """
    faces=list(faces)
    if not faces:raise ValueError('The cell has no realized boundary.')
    check_oriented_shell(faces)
    points=[tuple(p) for f in faces for p in f.points]
    lower=max(float(lo),min(p[2] for p in points))
    upper=min(float(hi),max(p[2] for p in points))
    if not np.isfinite([lo,hi]).all() or upper<=lower:
        raise ValueError('The range contains no positive-height solid portion.')
    registry=CoordinateRegistry(points)
    def canonical(p):
        p=tuple(float(v) for v in p)
        return registry.canonical(p,p[2] if p[2] in (lower,upper) else None)
    for p in points:canonical(p)
    # Establish shared cut-edge intersections before independent face projection.
    for a,b in sorted({tuple(sorted((tuple(a),tuple(b)))) for f in faces
                      for a,b in zip(f.points,f.points[1:]+f.points[:1])}):
        for z in (lower,upper):
            if (a[2]-z)*(b[2]-z)<0:
                t=(z-a[2])/(b[2]-a[2])
                canonical((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1]),z))
    clipped=[]
    for face in faces:
        arr=np.asarray(face.points,dtype=float);origin=arr[0];rel=arr-origin
        normal=np.sum(np.cross(rel,np.roll(rel,-1,axis=0)),axis=0)
        if len(set(arr[:,2]))==1:
            z=origin[2]
            if z<lower or z>upper or (z==lower and normal[2]>0) or (z==upper and normal[2]<0):continue
            clipped.append([[canonical(p) for p in arr]])
            continue
        horizontal=np.linalg.norm(normal[:2])
        if horizontal==0:raise ValueError('A nonhorizontal face has no stable plane.')
        across=normal[:2]/horizontal;tangent=np.array([-across[1],across[0]])
        projected=Polygon(np.column_stack((rel[:,:2]@tangent,arr[:,2])))
        if not projected.is_valid:raise ValueError('A realized face projection is invalid.')
        left,_,right,_=projected.bounds;padding=max(1.,right-left)
        geometry=projected.intersection(box(left-padding,lower,right+padding,upper))
        for polygon in _polygons(geometry):
            if polygon.area==0:continue
            # (tangent, increasing Z) has the original face's normal.
            polygon=orient(polygon,1)
            rings=[]
            for ring in (polygon.exterior,*polygon.interiors):
                lifted=[]
                for s,z in list(ring.coords)[:-1]:
                    xy=origin[:2]+s*tangent-across*(normal[2]/horizontal)*(z-origin[2])
                    lifted.append(canonical((*xy,z)))
                rings.append(lifted)
            clipped.append(rings)
    if not clipped:raise ValueError('Clipping produced no boundary faces.')
    clipped=_node(clipped,registry)
    uses=_uses(clipped)
    for z in (lower,upper):
        lines=[LineString([a[:2],b[:2]]) for edge,values in uses.items() if len(values)==1
               for a,b in values if a[2]==b[2]==z]
        if not lines:continue
        cap=_regions_from_lines(lines)
        if cap.is_empty:raise ValueError('Cut boundary could not be closed with caps.')
        for polygon in _polygons(cap):
            polygon=orient(polygon,-1 if z==lower else 1)
            clipped.append([[canonical((*p,z)) for p in list(ring.coords)[:-1]]
                            for ring in (polygon.exterior,*polygon.interiors)])
    clipped=_node(clipped,registry)
    uses=_uses(clipped)
    if any(len(v)!=2 or v[0]!=tuple(reversed(v[1])) for v in uses.values()):
        raise ValueError('The clipped boundary is not consistently oriented and closed.')
    links=defaultdict(lambda:defaultdict(list))
    for rings in clipped:
        for ring in rings:
            if len(set(ring))!=len(ring) or len(ring)<3:
                raise ValueError('A clipped face ring is degenerate.')
            for i,p in enumerate(ring):
                a,b=ring[i-1],ring[(i+1)%len(ring)]
                links[p][a].append(b);links[p][b].append(a)
    for link in links.values():
        reached=set();pending=[next(iter(link))]
        while pending:
            at=pending.pop()
            if at not in reached:reached.add(at);pending.extend(link[at])
        if len(reached)!=len(link) or any(len(v)!=2 for v in link.values()):
            raise ValueError('The clipped boundary has a nonmanifold vertex.')
    return dict(vertices=len({p for rings in clipped for ring in rings for p in ring}),
                edges=len(uses),faces=len(clipped),actual_z_min=lower,actual_z_max=upper)
