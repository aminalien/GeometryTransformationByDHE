"""Transactional, repeated DHE generalization with an explicit exterior.

Paper workflows remain unchanged. Boundary-chain strips and collapse ridges
are reused; a conforming vertical-interval arrangement integrates their faces
with exterior space, other sources, and the preceding external boundary.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from types import SimpleNamespace

import numpy as np
from shapely import constrained_delaunay_triangles, union_all
from shapely.affinity import translate
from shapely.geometry import Polygon, Point, LineString
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

import DHECore as dhe
from generalization_geometry import TOL, polygon_target, visible_point, skeleton_ridge, feature_color
from shell_conformity import (CoordinateRegistry, arithmetic_bound, TransitionShellError,
                             check_face_intersections, check_interface_pairs, diagnosed_transition)


def xyz(p):
    return tuple(float(v) for v in p)


def _lerp(a,b,t):
    return a if t==0 or a==b else b if t==1 else a+(b-a)*t


def parts(g):
    if g.geom_type == "Polygon":
        if not g.is_empty:
            yield g
    elif hasattr(g, "geoms"):
        for child in g.geoms:
            yield from parts(child)


def _conform_footprints(geometries):
    """Insert shared boundary nodes without moving a neighbouring boundary."""
    all_points=[tuple(p) for g in geometries.values() for p in g.exterior.coords[:-1]]
    registry=CoordinateRegistry(all_points,dimension=2)
    points=np.array(sorted({registry.canonical(p) for p in all_points}))
    result={}
    for owner,geometry in geometries.items():
        ring=[registry.canonical(p) for p in orient(geometry,1).exterior.coords[:-1]]
        output=[]
        for a,b in zip(ring,ring[1:]+ring[:1]):
            av,bv=np.asarray(a),np.asarray(b);d=bv-av;length=np.linalg.norm(d)
            t=(points-av)@d/(length*length)
            indices=np.flatnonzero((t>registry.bound/length)&(t<1-registry.bound/length)&
                                  (np.linalg.norm(points-av-t[:,None]*d,axis=1)<=registry.bound))
            output.append(a)
            output.extend(tuple(points[i]) for i in sorted(indices,key=lambda i:t[i]))
        result[owner]=Polygon(output)
    return result


def _conform_input_interfaces(geometries):
    """Record real input boundary intersections before subtracting the XY origin.

    A point on a long edge in projected CRS can cease to be exactly collinear
    after independent floating-point translations. Insert the SAME intersection
    endpoint on both input rings first; do not infer adjacency from proximity.
    """
    nodes={k:set() for k in geometries}
    def coordinates(g):
        if g.is_empty:return []
        if g.geom_type in ('Point','LineString','LinearRing'):return list(g.coords)
        return [p for child in g.geoms for p in coordinates(child)]
    owners=list(geometries)
    for i,k in enumerate(owners):
        for j in owners[:i]:
            points=coordinates(geometries[k].boundary.intersection(geometries[j].boundary))
            nodes[k].update(points);nodes[j].update(points)
    result={}
    for owner,g in geometries.items():
        ring=list(g.exterior.coords)[:-1];existing=set(ring)
        insertions=defaultdict(list)
        segments=[LineString([a,b]) for a,b in zip(ring,ring[1:]+ring[:1])]
        for point in nodes[owner]-existing:
            index=min(range(len(segments)),key=lambda i:segments[i].distance(Point(point)))
            if segments[index].distance(Point(point))>arithmetic_bound(ring):
                raise TransitionShellError('An input interface junction is not on its generating boundary.',
                    'input_interface_nonconformity',cell=owner,point=point)
            insertions[index].append((segments[index].project(Point(point)),point))
        output=[]
        for i,p in enumerate(ring):
            output.append(p);output.extend(q for _,q in sorted(insertions[i]))
        result[owner]=Polygon(output)
    return result


@dataclass
class Feature:
    index: int
    geometry: object
    attributes: dict
    lineage: tuple[int, ...]
    label: str


@dataclass
class Face:
    owner: int
    points: list[tuple]
    part: str = "boundary"


@dataclass
class Patch:
    source: int
    points: list[tuple]
    geometry: Polygon = field(init=False)
    _height_cache: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self.points = [xyz(p) for p in self.points]
        self.geometry = Polygon([p[:2] for p in self.points])

    def height(self, point):
        point=tuple(point)
        if point in self._height_cache:
            return self._height_cache[point]
        bound=arithmetic_bound(self.points)
        # A real patch endpoint or constant-level boundary edge carries an
        # explicit level. Do not reconstruct it through a barycentric solve.
        for vertex in self.points:
            if max(abs(point[k]-vertex[k]) for k in (0,1))<=bound:
                self._height_cache[point]=vertex[2]
                return vertex[2]
        for a,b in zip(self.points,self.points[1:]+self.points[:1]):
            if a[2]!=b[2]:
                continue
            av,bv=np.array(a[:2]),np.array(b[:2]);d=bv-av
            length=np.linalg.norm(d)
            t=float((np.asarray(point)-av)@d/(length*length))
            if -bound/length<=t<=1+bound/length and np.linalg.norm(np.asarray(point)-av-t*d)<=bound:
                self._height_cache[point]=a[2]
                return a[2]
        a, b, c = np.asarray(self.points)
        u, v = np.linalg.solve(np.column_stack((b[:2]-a[:2], c[:2]-a[:2])), np.asarray(point)-a[:2])
        value=float(a[2]+u*(b[2]-a[2])+v*(c[2]-a[2]))
        self._height_cache[point]=value
        return value


@dataclass
class OperationRecord:
    name: str
    selected: tuple[int, ...]
    affected: tuple[int, ...]
    z_b: float
    z_s: float
    z_t: float
    winner: int | None = None
    retired: tuple[int, ...] = ()
    parameters: dict = field(default_factory=dict)


@dataclass
class State:
    features: dict[int, Feature]
    faces: list[Face]
    z: float
    description: str
    changes: tuple[int, ...] = ()
    operation: OperationRecord | None = None


@dataclass
class Proposal:
    base: State
    state: State
    model: object
    core: tuple
    targets: dict
    affected: tuple[int, ...]
    notes: list[str]


def _core():
    return dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId


def _restore(core):
    dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId = core


def _node_faces(faces):
    """Node all 3D face edges, including historical edges and vertical junctions."""
    cleaned = []
    registry=CoordinateRegistry([p for f in faces for p in f.points])
    levels = {p[2] for face in faces if face.part in ("top","bottom") for p in face.points}
    def canonical(point):
        point = xyz(point)
        role=point[2] if point[2] in levels else None
        return registry.canonical(point,role)
    # Exact horizontal levels take precedence over near-level interpolations.
    # Otherwise canonicalization could tilt an intended polygon cap by 1e-8 m.
    for face in sorted(faces, key=lambda f: 0 if f.part in ("top", "bottom") else 1):
        if len({p[2] for p in face.points}) == 1:
            for point in face.points:
                canonical(point)
    for face in faces:
        points = []
        for p in face.points:
            p = canonical(p)
            if not points or p != points[-1]:
                points.append(p)
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) >= 3:
            arr = np.asarray(points)
            if np.linalg.norm(np.sum(np.cross(arr-arr[0], np.roll(arr-arr[0], -1, axis=0)), axis=0)) > TOL*TOL:
                cleaned.append(Face(face.owner, points, face.part))
    vertices = np.array(sorted({p for f in cleaned for p in f.points}))
    result = []
    for face in cleaned:
        points = []
        for a, b in zip(face.points, face.points[1:]+face.points[:1]):
            a, b = np.asarray(a), np.asarray(b)
            d = b-a
            length = np.linalg.norm(d)
            resolution=float(np.max(np.abs(d)/registry.axis_bounds))
            if resolution <= 1:
                raise ValueError("Geometry contains a boundary segment below the construction tolerance.")
            delta = vertices-a
            t = delta@d/(length*length)
            mask = (t > 1/resolution) & (t < 1-1/resolution)
            indices = np.flatnonzero(mask & np.all(np.abs(delta-t[:,None]*d)<=registry.axis_bounds,axis=1))
            points.append(xyz(a))
            points.extend(xyz(vertices[i]) for i in sorted(indices, key=lambda i: t[i]))
        result.append(Face(face.owner, points, face.part))
    # Dissolve coincident faces within a newly merged logical cell.
    groups = defaultdict(list)
    for face in result:
        groups[face.owner, tuple(sorted(face.points))].append(face)
    surviving = []
    for uses in groups.values():
        if len(uses) == 1:
            surviving.extend(uses)
        elif len(uses) != 2:
            raise ValueError("More than two coincident faces within a cell.")
        else:
            a, b = uses
            edges = {(p,q) for p,q in zip(a.points, a.points[1:]+a.points[:1])}
            if edges != {(q,p) for p,q in zip(b.points, b.points[1:]+b.points[:1])}:
                raise ValueError("Coincident same-cell faces do not have opposite orientations.")
    return surviving


def _vertical(face):
    points=np.asarray(face.points)
    normal=np.sum(np.cross(points-points[0],np.roll(points-points[0],-1,axis=0)),axis=0)
    length=np.linalg.norm(normal)
    return length>0 and abs(normal[2])<=arithmetic_bound(points)*length


def _remove_redundant_walls(faces):
    """Dissolve only adjacent coplanar walls with the same interface owner."""
    matching=defaultdict(list)
    for i,face in enumerate(faces):matching[tuple(sorted(face.points))].append(i)
    neighbours={i:tuple(sorted(faces[j].owner for j in matching[tuple(sorted(f.points))] if j!=i))
                for i,f in enumerate(faces)}
    eligible={i for i,f in enumerate(faces) if f.part in ('wall','boundary','base_wall') and _vertical(f)}
    edge_faces=defaultdict(list)
    for i in eligible:
        points=faces[i].points
        for a,b in zip(points,points[1:]+points[:1]):edge_faces[tuple(sorted((a,b)))].append(i)
    adjacency=defaultdict(set)
    for uses in edge_faces.values():
        for i in uses:
            for j in uses:
                if i==j or faces[i].owner!=faces[j].owner or neighbours[i]!=neighbours[j]:continue
                a=np.array(faces[i].points);normal=np.sum(np.cross(a-a[0],np.roll(a-a[0],-1,axis=0)),axis=0)
                normal/=np.linalg.norm(normal)
                if np.max(np.abs((np.asarray(faces[j].points)-a[0])@normal))<=arithmetic_bound(a):
                    adjacency[i].add(j)
    visited=set();result=[f for i,f in enumerate(faces) if i not in eligible]
    for start in sorted(eligible):
        if start in visited:continue
        group=set();pending=[start]
        while pending:
            i=pending.pop()
            if i not in group:group.add(i);pending.extend(adjacency[i]-group)
        visited.update(group)
        uses=defaultdict(list)
        for i in group:
            p=faces[i].points
            for a,b in zip(p,p[1:]+p[:1]):uses[tuple(sorted((a,b)))].append((a,b))
        boundary=[v[0] for v in uses.values() if len(v)==1]
        forward={a:b for a,b in boundary}
        cycle=[];at=boundary[0][0]
        while at not in cycle and at in forward:
            cycle.append(at);at=forward[at]
        if len(forward)==len(boundary)==len(cycle) and at==cycle[0]:
            result.append(Face(faces[start].owner,cycle,faces[start].part))
        else:result.extend(faces[i] for i in sorted(group))
    # A node remains if ANY incident face needs a true corner there. This also
    # retains old inclined transitions and interface ownership junctions.
    required=set()
    for face in result:
        p=face.points
        for i,b in enumerate(p):
            a,c=np.array(p[i-1]),np.array(p[(i+1)%len(p)])
            d=c-a;length=np.linalg.norm(d)
            if length==0 or np.linalg.norm(np.cross(np.array(b)-a,d))/length>arithmetic_bound(p):
                required.add(b)
    return [Face(f.owner,[p for p in f.points if p in required],f.part) for f in result]


def _lift_unchanged_isolated(state, old, new, lo, hi):
    """Raise candidate cap vertices directly when roof-incident walls are vertical."""
    lifted={}
    for owner,polygon in old.items():
        if owner not in new or not polygon.equals(new[owner]):continue
        if any(other!=owner and polygon.intersects(g) for other,g in old.items()):continue
        shell=[f for f in state.faces if f.owner==owner]
        roof_incident=[f for f in shell if any(p[2]==lo for p in f.points)]
        if not roof_incident or any(f.part!='top' and (f.part=='transition' or not _vertical(f)) for f in roof_incident):continue
        lifted[owner]=[Face(owner,[(x,y,hi if z==lo else z) for x,y,z in f.points],f.part) for f in shell]
    return lifted


def realize(faces, origin):
    """Realize checked shells with the existing Euler and sewing operators."""
    from main import ExtrudedCell, ExtrusionModel, validate_model
    from aggregation_layer import _check_vertex_links
    from transition_layer import _check_shell, _planned_volume
    from plane_slicing import _face_frame
    faces = _node_faces(faces)
    raw_groups=defaultdict(list)
    for face in faces:raw_groups[face.owner].append(face)
    for owner,shell in raw_groups.items():
        try:_check_shell(shell)
        except TransitionShellError as exc:
            exc.report.update(cell=owner,stage='conforming_boundary')
            exc.args=(f"Feature {owner}: {exc}",)
            raise
    faces = _remove_redundant_walls(faces)
    groups = defaultdict(list)
    for face in faces:
        groups[face.owner].append(face)
    for owner, shell in groups.items():
        try:
            _check_shell(shell)
            _check_vertex_links(shell)
            for face in shell:
                if face.part=='transition' and len({p[2] for p in face.points})==1:
                    raise TransitionShellError('An inclined transition facet has only one assigned level.',
                        'horizontal_transition_facet',stage='facet_levels',cell=owner,points=face.points)
                _face_frame(face.points)
            check_face_intersections(shell)
            if _planned_volume(shell) <= TOL*TOL:
                raise ValueError("non-positive oriented volume")
        except ValueError as exc:
            if isinstance(exc,TransitionShellError):
                exc.report['cell']=owner
                exc.args=(f"Feature {owner}: {exc}",)
                raise
            raise ValueError(f"Feature {owner}: {exc}") from exc
    check_interface_pairs(faces)
    check_face_intersections(faces,across_cells=True)
    saved = _core()
    try:
        dhe.reset_globals()
        exterior = dhe.make_vertex(0, 0, 0, ch="VINF", clr=(.55,.2,.75,1))
        exterior.logical_feature_id = "VINF"
        vertices, matches, cells, records = {}, defaultdict(list), [], []
        for owner, shell in groups.items():
            center = np.mean([p for f in shell for p in f.points], axis=0)
            dual = dhe.make_vertex(*center, ch=str(owner), clr=feature_color(owner))
            pairs = defaultdict(list)
            seeds = []
            for face in shell:
                vs = []
                for point in face.points:
                    if point not in vertices:
                        vertices[point] = dhe.make_vertex(*point)
                    vs.append(vertices[point])
                first = dhe.MEVVFS(vs[0], vs[1], dual, exterior)
                cursor = first
                for vertex in vs[2:]:
                    cursor = dhe.MEV(vertex, cursor.S)
                seed = dhe.MEF(first, cursor.S).S
                seeds.append(seed)
                records.append((face, seed))
                matches[tuple(sorted(face.points))].append(seed)
                for edge in dhe.face_edges(seed):
                    pairs[frozenset((edge.V, edge.S.V))].append(edge)
            for uses in pairs.values():
                if len(uses) != 2:
                    raise ValueError(f"Feature {owner} has an unpaired DHE edge.")
                dhe.Sew(uses[0], uses[1].S)
            cells.append(ExtrudedCell(str(owner), seeds[0], dual))
        shared = []
        for seeds in matches.values():
            if len(seeds) > 2:
                raise ValueError("A face belongs to more than two cells.")
            if len(seeds) == 2:
                a, b = seeds
                aligned = next(e for e in dhe.face_edges(b) if e.V is a.S.V and e.S.V is a.V)
                dhe.JoinByFace(a, aligned)
                shared.append((a, aligned))
        model = ExtrusionModel(cells, exterior, origin, shared)
        validate_model(model)
        model.generalization_faces = faces
        model.generalization_face_records = records
        return model, faces, _core()
    except Exception:
        _restore(saved)
        raise


def _prism(owner, polygon, bottom, top):
    ring = [xyz(p) for p in orient(polygon, 1).exterior.coords[:-1]]
    faces = [Face(owner, [(*p,bottom) for p in reversed(ring)], "bottom"), Face(owner, [(*p,top) for p in ring], "top")]
    for a, b in zip(ring, ring[1:]+ring[:1]):
        faces.append(Face(owner, [(*a,bottom),(*b,bottom),(*b,top),(*a,top)]))
    return faces


def _polygon_patches(owner, source, target, lo, hi):
    """Use the paper strip whenever possible; otherwise a boundary-distance lift."""
    from active_region_triangulation import VertexRole
    from top_face_overlay import RegionKind
    from transition_layer import _strip
    patches, extensions = [], 0
    for geometry, kind in ((source.difference(target), RegionKind.ACTIVE_SOURCE_ONLY), (target.difference(source), RegionKind.ACTIVE_TARGET_ONLY)):
        for region in parts(geometry):
            if region.area <= TOL*TOL:
                continue
            triangles = None
            if not region.interiors:
                @dataclass(frozen=True)
                class XY:
                    x: float
                    y: float
                vertices = [XY(*p[:2]) for p in list(region.exterior.coords)[:-1]]
                roles = {}
                for v in vertices:
                    p = Point(v.x,v.y)
                    bound=arithmetic_bound([*source.exterior.coords,*target.exterior.coords])
                    s, t = source.boundary.distance(p)<=bound, target.boundary.distance(p)<=bound
                    roles[v] = VertexRole.BOTH if s and t else VertexRole.SOURCE if s else VertexRole.TARGET if t else VertexRole.NEITHER
                context = SimpleNamespace(source_geometry=source, target_geometry=target, tolerance=TOL,
                                          triangulation=SimpleNamespace(vertex_roles=roles))
                try:
                    triangles = _strip(SimpleNamespace(boundary_vertices=vertices, geometry=region, kind=kind,
                                                       region_id=f'{owner}/active/{len(patches)}'), context, lo, hi)
                except ValueError:
                    pass
            if triangles is None:
                extensions += 1
                triangles = []
                for triangle in constrained_delaunay_triangles(region).geoms:
                    points = []
                    for p in list(triangle.exterior.coords)[:-1]:
                        ds, dt = source.boundary.distance(Point(p)), target.boundary.distance(Point(p))
                        bound=arithmetic_bound([*source.exterior.coords,*target.exterior.coords])
                        fraction = ds/(ds+dt) if ds+dt > bound else .5
                        points.append((*p, lo+(hi-lo)*fraction))
                    if max(p[2] for p in points)-min(p[2] for p in points) <= TOL:
                        center = triangle.centroid
                        mid = (center.x,center.y,(lo+hi)/2)
                        triangles.extend([[a,b,mid] for a,b in zip(points, points[1:]+points[:1])])
                    else:
                        triangles.append(points)
            patches.extend(Patch(owner, points) for points in triangles if Polygon([p[:2] for p in points]).area > TOL*TOL)
    return patches, extensions


def _collapse_patches(owner, source, target, lo, hi):
    from transition_targets import _end_extensions
    ring = list(orient(source,1).exterior.coords)[:-1]
    if target.geom_type == "Point":
        apex = (target.x,target.y,hi)
        return [Patch(owner,[(*a,lo),(*b,lo),apex]) for a,b in zip(ring,ring[1:]+ring[:1])]
    ends = _end_extensions(source,target)
    boundary = source.exterior
    ordered = sorted({xyz(p) for p in [*ring,*ends]}, key=lambda p: boundary.project(Point(p)))
    i = min(range(len(ordered)), key=lambda i: Point(ordered[i]).distance(Point(ends[0])))
    ring = ordered[i:]+ordered[:i]
    j = min(range(len(ring)), key=lambda i: Point(ring[i]).distance(Point(ends[1])))
    chains = [ring[:j+1], list(reversed(ring[j:]+ring[:1]))]
    upper = [(*p,hi) for p in target.coords]
    result = []
    for chain in chains:
        region = Polygon(chain+list(reversed(list(target.coords))))
        if not region.is_valid:
            raise ValueError("Ridge does not divide the polygon into two valid strips.")
        lower=[(*p,lo) for p in chain]
        # Validate the same admissible moves at every Z interval; otherwise a
        # target passing at 0..1 may choose folded moves at a different height.
        triangles=_ridge_strip(lower,upper,region)
        result.extend(Patch(owner, tri) for tri in triangles)
    return result


def _ridge_strip(lower, upper, region):
    """Paper strip DP with zero-projection moves excluded.

    The interval arrangement cannot represent a zero-volume folded flap. This
    extension retains every ridge endpoint in real inclined boundary facets.
    """
    from transition_layer import _signed_xy
    sign=1 if _signed_xy(lower+list(reversed(upper)))>0 else -1
    states={(0,0):(0.,[])}
    for i in range(len(lower)):
        for j in range(len(upper)):
            if (i,j) not in states:continue
            cost,triangles=states[i,j]
            moves=[]
            if i+1<len(lower):moves.append((i+1,j,[lower[i],lower[i+1],upper[j]]))
            if j+1<len(upper):moves.append((i,j+1,[lower[i],upper[j+1],upper[j]]))
            for ni,nj,triangle in moves:
                cross=_signed_xy(triangle)
                if cross*sign<=TOL*TOL:continue
                if not region.buffer(TOL).covers(Polygon([p[:2] for p in triangle])):continue
                value=cost+sum((lower[ni][k]-upper[nj][k])**2 for k in range(3))
                if (ni,nj) not in states or value<states[ni,nj][0]:
                    states[ni,nj]=(value,triangles+[triangle])
    if (len(lower)-1,len(upper)-1) not in states:
        raise ValueError("Ridge requires folded or zero-projection facets; choose another target.")
    triangles=states[len(lower)-1,len(upper)-1][1]
    if abs(sum(Polygon([p[:2] for p in t]).area for t in triangles)-region.area)>max(1e-6,region.area*1e-7):
        raise ValueError("Ridge strip does not cover its transition region.")
    return triangles


def _recipient(patch, sources, selected, lo):
    source = sources[patch.source]
    lower = [p[:2] for p in patch.points if p[2]==lo]
    boundary = list(source.exterior.coords)
    segments = [LineString([a,b]) for a,b in zip(boundary,boundary[1:])]
    if len(lower) >= 2:
        segment = LineString(lower[:2])
    else:
        # Same deterministic local boundary ownership as a one-lower-vertex strip.
        at = Point(lower[0]) if lower else patch.geometry.representative_point()
        segment = min(segments, key=lambda e: (e.distance(at), e.distance(patch.geometry.centroid)))
    def contains_edge(polygon):
        if polygon.boundary.covers(segment):return True
        points=np.asarray(segment.coords)
        if segment.length<=arithmetic_bound(points):return False
        ring=list(polygon.exterior.coords)
        for a,b in zip(ring,ring[1:]):
            av,bv=np.array(a),np.array(b);d=bv-av;length=np.linalg.norm(d)
            t=(points-av)@d/(length*length)
            bound=arithmetic_bound([a,b,*points])
            if t.min()>=-bound/length and t.max()<=1+bound/length and np.max(np.linalg.norm(points-av-t[:,None]*d,axis=1))<=bound:
                return True
        return False
    candidates = [owner for owner, polygon in sources.items() if owner != patch.source and owner not in selected
                  and contains_edge(polygon)]
    return min(candidates) if candidates else None


@dataclass
class Column:
    points: list[tuple]
    old: int | None
    new: int | None
    heights: list[float]


def _layer_faces(old, new, patches, lo, hi):
    boundaries = [p.boundary for p in [*old.values(), *new.values()]] + [p.geometry.boundary for p in patches]
    network = union_all(boundaries)
    xy_nodes=CoordinateRegistry([p[:2] for patch in patches for p in patch.points]+
                                [p for g in [*old.values(),*new.values()] for polygon in parts(g)
                                 for ring in [polygon.exterior,*polygon.interiors] for p in ring.coords],dimension=2)
    domain = unary_union([*old.values(),*new.values()])
    columns = []
    seen_triangles = set()
    faces = []
    for region in polygonize(network):
        if region.area == 0:
            continue
        point = region.representative_point()
        if not domain.covers(point):
            continue
        before = next((k for k,g in old.items() if g.covers(point)),None)
        after = next((k for k,g in new.items() if g.covers(point)),None)
        matching = [p for p in patches if p.geometry.covers(point)]
        if not matching:
            matching=[p for p in patches if p.geometry.distance(point)<=xy_nodes.bound]
        patch = next((p for p in matching if p.source == before), next((p for p in matching if p.source == after), matching[0] if matching else None))
        if before != after and patch is None:
            raise ValueError(f"No transition surface for ownership change {before} → {after}.")
        if before==after:
            for ring in [orient(region,1).exterior,*orient(region,1).interiors]:
                xy=[xy_nodes.canonical(p) for p in list(ring.coords)[:-1]]
                xy=[p for i,p in enumerate(xy) if p!=xy[i-1]]
                if len(xy)<3:continue
                columns.append(Column(xy,before,after,[hi]*len(xy)))
            continue
        for triangle in constrained_delaunay_triangles(region).geoms:
            if not region.covers(triangle.representative_point()):
                continue
            xy = [xyz(xy_nodes.canonical(p)) for p in orient(triangle,1).exterior.coords[:-1]]
            if len(set(xy)) != 3 or Polygon(xy).area == 0:
                continue
            span=max(np.linalg.norm(np.subtract(a,b)) for a,b in zip(xy,xy[1:]+xy[:1]))
            if 2*Polygon(xy).area/span<=xy_nodes.bound:
                continue  # zero projected area within the arithmetic error bound
            triangle_key = tuple(sorted(xy))
            if triangle_key in seen_triangles:
                continue
            seen_triangles.add(triangle_key)
            h = [patch.height(p) for p in xy] if before != after else [hi]*3
            if min(h)<lo-xy_nodes.bound or max(h)>hi+xy_nodes.bound:
                raise TransitionShellError('A transition facet violates its assigned interval.',
                                           'invalid_level_assignment',source=patch.source,points=xy,heights=h,interval=(lo,hi))
            column = Column(xy,before,after,h)
            columns.append(column)
            if before != after:
                points = [xyz((*p,z)) for p,z in zip(xy,h)]
                if before is not None:
                    faces.append(Face(before, points, "transition"))
                if after is not None:
                    faces.append(Face(after, list(reversed(points)), "transition"))
    # Vertical curtains between neighbouring interval columns. Heights may differ
    # across a cut; split their crossings before subtracting same-owner intervals.
    edges = defaultdict(list)
    for column in columns:
        for i in range(len(column.points)):
            j=(i+1)%len(column.points)
            a,b = column.points[i],column.points[j]
            za,zb = column.heights[i],column.heights[j]
            key = tuple(sorted((a,b)))
            edges[key].append((column, (za,zb) if a == key[0] else (zb,za), a == key[0]))
    # CDT preserves boundary vertices, but independently triangulated polygon
    # components can have T junctions. Node XY edges globally before pairing.
    nodes = sorted({p for c in columns for p in c.points})
    atomic = defaultdict(list)
    array = np.array(nodes)
    for (a,b), uses in edges.items():
        av,bv = np.array(a),np.array(b); d=bv-av; l2=d@d
        ts=(array-av)@d/l2
        bound=xy_nodes.bound
        length=np.sqrt(l2)
        hits=np.flatnonzero((ts>bound/length)&(ts<1-bound/length)&(np.linalg.norm(array-av-ts[:,None]*d,axis=1)<bound))
        split=[(0.,a),*sorted([(float(ts[i]),nodes[i]) for i in hits]),(1.,b)]
        for (t,p),(u,q) in zip(split,split[1:]):
            for column,(ha,hb),forward in uses:
                atomic[p,q].append((column,(_lerp(ha,hb,t),_lerp(ha,hb,u)),forward))
    for (a,b), uses in atomic.items():
        if len(uses)>2:
            raise ValueError(f"Transition columns overlap near {a}. Adjust the operation parameters.")
        left=next((u for u in uses if u[2]),None)
        right=next((u for u in uses if not u[2]),None)
        functions=[(lo,lo),(hi,hi)]+[u[1] for u in uses]
        cuts={0.,1.}
        for i,f in enumerate(functions):
            for g in functions[:i]:
                da,db=f[0]-g[0],f[1]-g[1]
                if da*db < 0:
                    cuts.add(da/(da-db))
        cuts=sorted(cuts)
        def label(use,t,z):
            if use is None:
                return None
            c,h,_=use
            return c.old if z < _lerp(h[0],h[1],t) else c.new
        for ta,tb in zip(cuts,cuts[1:]):
            tm=(ta+tb)/2
            ordered=sorted(functions,key=lambda f:_lerp(f[0],f[1],tm))
            for lower,upper in zip(ordered,ordered[1:]):
                l=_lerp(lower[0],lower[1],tm); h=_lerp(upper[0],upper[1],tm)
                if h-l<=arithmetic_bound([(lo,hi)]):
                    continue
                ownleft,ownright=label(left,tm,(l+h)/2),label(right,tm,(l+h)/2)
                if ownleft == ownright:
                    continue
                p=xy_nodes.canonical(tuple(_lerp(a[k],b[k],ta) for k in range(2)))
                q=xy_nodes.canonical(tuple(_lerp(a[k],b[k],tb) for k in range(2)))
                points=[(*p,_lerp(lower[0],lower[1],ta)),(*q,_lerp(lower[0],lower[1],tb)),
                        (*q,_lerp(upper[0],upper[1],tb)),(*p,_lerp(upper[0],upper[1],ta))]
                if ownleft is not None:
                    faces.append(Face(ownleft,points,"wall"))
                if ownright is not None:
                    faces.append(Face(ownright,list(reversed(points)),"wall"))
    for owner, polygon in new.items():
        faces.append(Face(owner,[(*p,hi) for p in orient(polygon,1).exterior.coords[:-1]],"top"))
    return faces


class GeneralizationSession:
    def __init__(self, data, initial_height=5.):
        if not isfinite(initial_height) or initial_height <= 0:
            raise ValueError("Initial height must be positive and finite.")
        self.data=data
        self.last_diagnostic=None
        self.origin=tuple(float(v) for v in data.frame.total_bounds[:2])
        features={}
        faces=[]
        inputs=_conform_input_interfaces({int(row['_index']):row.geometry for _,row in data.frame.iterrows()})
        for _,row in data.frame.iterrows():
            index=int(row["_index"])
            geometry=translate(inputs[index],xoff=-self.origin[0],yoff=-self.origin[1])
            attrs={k:v for k,v in row.items() if k not in (data.frame.geometry.name,"_index")}
            features[index]=Feature(index,geometry,attrs,(index,),f"Feature {index}")
            faces.extend(_prism(index,geometry,0.,initial_height))
        self.model,faces,self.core=realize(faces,self.origin)
        self.history=[State(features,faces,initial_height,"Initial extrusion")]
        self.cursor=0

    @property
    def state(self):
        return self.history[self.cursor]

    @diagnosed_transition
    def preview(self, operation, selected, *, dz=10., tolerance=1., iterations=1, ratio=.25, winner=None):
        selected=tuple(sorted(set(map(int,selected))))
        state=self.state
        if not selected:
            raise ValueError("Select at least one polygon.")
        if not isfinite(dz) or dz <= TOL:
            raise TransitionShellError(f"Height increment must be finite and greater than {TOL:g} transition units.",
                                       'unsupported_transition_interval',stage='level_assignment',increment=dz)
        if any(k not in state.features or state.features[k].geometry.geom_type != "Polygon" for k in selected):
            raise ValueError("Only current polygon features can be generalized; collapsed symbols are terminal.")
        lo,hi=state.z,state.z+dz
        if not isfinite(hi) or hi<=lo:
            raise TransitionShellError('The requested upper transition level is not representably above the source level.',
                                       'unresolvable_transition_interval',stage='level_assignment',source_z=lo,target_z=hi)
        sources={k:f.geometry for k,f in state.features.items() if f.geometry.geom_type == "Polygon"}
        # Ownership changes must be split where a neighbour starts or stops along
        # an edge, even if the input ring omits that degree-two boundary node.
        sources=_conform_footprints(sources)
        targets,patches,notes={},[],[]
        remap={}
        aggregate=None
        if operation in ("merging","aggregation"):
            if len(selected)<2:
                raise ValueError("Select at least two polygons for merging or aggregation.")
            union=unary_union([sources[k] for k in selected])
            if operation == "merging":
                if winner is None or int(winner) not in selected:
                    raise ValueError("Choose a selected feature as the merge winner.")
                aggregate=int(winner)
                if union.geom_type != "Polygon" or union.interiors:
                    raise ValueError("Merge requires an edge-connected, hole-free union. Use aggregation for separated polygons.")
                target=union
                patches,extended=_polygon_patches(aggregate,sources[aggregate],target,lo,hi)
                notes.append("Winner grows by P2P facets; losing cells shrink below the shared interfaces and retire at the target level. Historical ownership is preserved.")
                if extended:
                    notes.append(f"{extended} nested/complex active regions use the existing conforming boundary-distance lift.")
            else:
                aggregate=max([*state.features,*[f for s in self.history for f in s.features]])+1
                from aggregation_geometry import build_aggregation_preview
                from aggregation_layer import _plan_shell
                preview=build_aggregation_preview({str(k):sources[k] for k in selected},tolerance=TOL)
                plans,_,_=_plan_shell(preview,lo-1,lo,lo+.8*dz,hi)
                patches=[Patch(aggregate,p.points) for p in plans if p.part == "connecting"]
                target=preview.hull
                notes.append("Straight-skeleton growth completes at 80% of the new interval; hull cap at the top.")
            targets[aggregate]=target
            if operation == "aggregation":
                remap={k:aggregate for k in selected}
        else:
            for k in selected:
                source=sources[k]
                if operation in ("simplification","smoothing"):
                    target=polygon_target(state.features[k].geometry,operation,tolerance,iterations,ratio)
                    ps,extended=_polygon_patches(k,source,target,lo,hi)
                    patches.extend(ps)
                    if extended:
                        notes.append(f"Feature {k}: {extended} nested/complex active regions use a conforming boundary-distance lift (new workspace extension).")
                elif operation == "point":
                    target=visible_point(source)
                    patches.extend(_collapse_patches(k,source,target,lo,hi))
                elif operation == "polyline":
                    target,method=skeleton_ridge(source)
                    notes.append(f"Feature {k}: {method}.")
                    patches.extend(_collapse_patches(k,source,target,lo,hi))
                else:
                    raise ValueError("Unknown generalization operation.")
                targets[k]=target
            if all(g.geom_type=="Polygon" and g.symmetric_difference(sources[k]).area<TOL*TOL for k,g in targets.items()):
                notes.append("These parameters leave all selected footprints unchanged; this step adds a vertical interval only.")
        explicit={k:g for k,g in targets.items() if g.geom_type == "Polygon"}
        for i,(k,g) in enumerate(explicit.items()):
            for j,h in list(explicit.items())[:i]:
                if g.intersection(h).area > TOL*TOL:
                    raise ValueError(f"Selected targets {j} and {k} overlap; adjust parameters or use separate steps.")
        occupied=unary_union(list(explicit.values()))
        new={k:g.difference(occupied) for k,g in sources.items() if k not in selected}
        if aggregate is None:
            gains=defaultdict(list)
            for patch in patches:
                region=patch.geometry.intersection(sources[patch.source]).difference(occupied)
                if region.area > TOL*TOL:
                    recipient=_recipient(patch,sources,selected,lo)
                    if recipient is not None:
                        gains[recipient].append(region)
            for k,regions in gains.items():
                new[k]=unary_union([new[k],*regions])
        new.update(explicit)
        for k,g in new.items():
            if g.is_empty or g.geom_type != "Polygon" or not g.is_valid or g.interiors:
                raise TransitionShellError(f"Feature {k}: neighbour update creates an empty, split, invalid or holed footprint. Change the selection or parameters.",
                    'non_simple_neighbour_footprint',stage='neighbour_union',cell=k,geometry_type=g.geom_type,
                    valid=g.is_valid,component_areas=[p.area for p in parts(g)],proposed_boundary=g.wkt,
                    original_boundary=sources[k].wkt if k in sources else None,role='neighbour_top_cap')
        for i,(k,g) in enumerate(new.items()):
            for j,h in list(new.items())[:i]:
                if g.intersection(h).area > TOL*TOL:
                    raise ValueError(f"Updated footprints {j} and {k} overlap.")
        # Map selected source identities before dissolving historical interfaces.
        old={}
        for k,g in sources.items():
            owner=remap.get(k,k)
            old[owner]=unary_union([old.get(owner,Polygon()),g])
        lifted=_lift_unchanged_isolated(state,old,new,lo,hi) if not remap else {}
        if lifted:
            notes.append(f"{len(lifted)} unchanged isolated features: roof lifted without a new wall band.")
        historical=[Face(remap.get(f.owner,f.owner),list(f.points),f.part) for f in state.faces
                    if f.owner not in lifted and not all(p[2]==lo for p in f.points)]
        historical.extend(f for shell in lifted.values() for f in shell)
        layer=_layer_faces({k:g for k,g in old.items() if k not in lifted},
                           {k:g for k,g in new.items() if k not in lifted},patches,lo,hi)
        saved=_core()
        try:
            model,faces,core=realize([*historical,*layer],self.origin)
            final_sections=self._validate_sections(model,old,new,lo,hi)
            contacts={k:g for k,g in targets.items() if g.geom_type != "Polygon"}
            if contacts:
                actual=final_sections
                for k,g in contacts.items():
                    if str(k) not in actual or actual[str(k)].hausdorff_distance(g)>TOL*10:
                        raise ValueError(f"Feature {k}: actual final contact does not match the collapse target.")
            # The next operation starts from the realized cap, including its
            # conforming nodes, rather than an independently stored target copy.
            for k in new:
                actual=final_sections[str(k)]
                if actual.geom_type != "Polygon" or actual.interiors:
                    raise ValueError(f"Feature {k}: realized top is not a supported simple polygon.")
                # Remove numerical collinear wiggles from the exported ring;
                # the real DHE face retains every conforming boundary node.
                new[k]=actual.simplify(arithmetic_bound(actual.exterior.coords), preserve_topology=True)
            for k,g in contacts.items():
                actual=final_sections[str(k)]
                if g.geom_type=="LineString" and actual.geom_type=="MultiLineString":
                    from shapely.ops import linemerge
                    actual=linemerge(actual)
                targets[k]=actual
        finally:
            _restore(saved)
        features={k:Feature(f.index,f.geometry,dict(f.attributes),f.lineage,f.label) for k,f in state.features.items() if k not in selected}
        for k,g in new.items():
            if k in features:
                features[k].geometry=g
        if aggregate is not None:
            lineage=tuple(sorted({i for k in selected for i in state.features[k].lineage}))
            attrs=dict(state.features[aggregate].attributes) if operation == "merging" else {"operation":"aggregation"}
            features[aggregate]=Feature(aggregate,new[aggregate],attrs,lineage,f"Feature {aggregate}" if operation == "merging" else f"Aggregate {aggregate}")
        else:
            for k,g in targets.items():
                f=state.features[k]
                features[k]=Feature(k,new[k] if g.geom_type == "Polygon" else g,dict(f.attributes),f.lineage,f.label)
        affected=tuple(sorted(k for k,g in new.items() if k not in sources or sources[k].symmetric_difference(g).area>TOL*TOL))
        affected=tuple(sorted(set(affected)|set(selected)))
        record=OperationRecord(operation,selected,affected,
            self.history[self.cursor-1].z if self.cursor else min(p[2] for f in state.faces for p in f.points),
            lo,hi,aggregate,tuple(k for k in selected if k not in features),
            dict(dz=dz,tolerance=tolerance,iterations=iterations,ratio=ratio))
        next_state=State(features,faces,hi,f"{operation}: {', '.join(map(str,selected))}; Z={lo:g}–{hi:g}",affected,record)
        return Proposal(state,next_state,model,core,targets,affected,notes)

    @staticmethod
    def _validate_sections(model,old,new,lo,hi):
        from plane_slicing import PlaneSlicer,SlicePlane
        slicer=PlaneSlicer(model)
        def check(actual,expected,caption):
            for k,g in expected.items():
                if g.symmetric_difference(actual.get(str(k),Polygon())).area > max(1e-5,g.area*1e-7,g.length*TOL*5):
                    raise ValueError(f"{caption} section does not match feature {k}.")
        check(slicer.sections(SlicePlane.horizontal(lo)),old,"Source")
        check(slicer.sections(SlicePlane.horizontal(hi)),new,"Target")
        for t in (.001,.25,.5,.75,.999):
            sections=list(slicer.sections(SlicePlane.horizontal(lo+t*(hi-lo))).values())
            if any(not g.is_valid for g in sections):
                raise ValueError("An intermediate section is invalid.")
            if sum(g.area for g in sections)-unary_union(sections).area>1e-5:
                raise ValueError("Intermediate cells overlap.")
        return slicer.sections(SlicePlane.horizontal(hi),include_degenerate=True)

    def apply(self, proposal):
        if proposal.base is not self.state:
            raise ValueError("Preview is stale. Generate it again before applying.")
        self.history=self.history[:self.cursor+1]+[proposal.state]
        self.cursor+=1
        self.model,self.core=proposal.model,proposal.core
        _restore(self.core)

    def go(self, index):
        index=int(index)
        if not 0<=index<len(self.history):
            raise ValueError("History index is outside the session.")
        saved=_core()
        try:
            model,_,core=realize(self.history[index].faces,self.origin)
        except Exception:
            _restore(saved)
            raise
        self.cursor,self.model,self.core=index,model,core

    def undo(self):
        if self.cursor:
            self.go(self.cursor-1)
