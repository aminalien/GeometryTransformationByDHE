"""Transactional, repeated DHE generalization with an explicit exterior.

Paper workflows remain unchanged. Boundary-chain strips and collapse ridges
are reused; a conforming vertical-interval arrangement integrates their faces
with exterior space, other sources, and the preceding external boundary.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from itertools import product
from types import SimpleNamespace

import numpy as np
from shapely import constrained_delaunay_triangles, set_precision, union_all
from shapely.affinity import translate
from shapely.geometry import Polygon, Point, LineString
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

import DHECore as dhe
from generalization_geometry import TOL, polygon_target, visible_point, skeleton_ridge, feature_color


def xyz(p):
    return tuple(round(float(v), 8) for v in p)


def parts(g):
    if g.geom_type == "Polygon":
        if not g.is_empty:
            yield g
    elif hasattr(g, "geoms"):
        for child in g.geoms:
            yield from parts(child)


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

    def __post_init__(self):
        self.points = [xyz(p) for p in self.points]
        self.geometry = Polygon([p[:2] for p in self.points])

    def height(self, point):
        a, b, c = np.asarray(self.points)
        u, v = np.linalg.solve(np.column_stack((b[:2]-a[:2], c[:2]-a[:2])), np.asarray(point)-a[:2])
        return float(a[2]+u*(b[2]-a[2])+v*(c[2]-a[2]))


@dataclass
class State:
    features: dict[int, Feature]
    faces: list[Face]
    z: float
    description: str
    changes: tuple[int, ...] = ()


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
    bins = defaultdict(list)
    offsets = tuple(product((-1,0,1), repeat=3))
    levels = {p[2] for face in faces if face.part in ("top","bottom") for p in face.points}
    def canonical(point):
        point = xyz(point)
        level = next((z for z in levels if abs(point[2]-z)<TOL),None)
        if level is not None:
            point = (point[0],point[1],level)
        key = tuple(round(v/TOL) for v in point)
        for offset in offsets:
            for old in bins[tuple(k+d for k,d in zip(key,offset))]:
                if sum((a-b)**2 for a,b in zip(point,old)) <= TOL*TOL:
                    return old
        bins[key].append(point)
        return point
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
            if length <= TOL:
                raise ValueError("Geometry contains a boundary segment below the construction tolerance.")
            delta = vertices-a
            t = delta@d/(length*length)
            mask = (t > TOL/length) & (t < 1-TOL/length)
            indices = np.flatnonzero(mask & (np.linalg.norm(delta-t[:,None]*d, axis=1) < TOL))
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


def realize(faces, origin):
    """Realize checked shells with the existing Euler and sewing operators."""
    from main import ExtrudedCell, ExtrusionModel, validate_model
    from aggregation_layer import _check_vertex_links
    from transition_layer import _check_shell, _planned_volume
    from plane_slicing import _face_frame
    faces = _node_faces(faces)
    groups = defaultdict(list)
    for face in faces:
        groups[face.owner].append(face)
    for owner, shell in groups.items():
        try:
            _check_shell(shell)
            _check_vertex_links(shell)
            for face in shell:
                _face_frame(face.points)
            if _planned_volume(shell) <= TOL*TOL:
                raise ValueError("non-positive oriented volume")
        except ValueError as exc:
            raise ValueError(f"Feature {owner}: {exc}") from exc
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
                    s, t = source.boundary.distance(p)<TOL, target.boundary.distance(p)<TOL
                    roles[v] = VertexRole.BOTH if s and t else VertexRole.SOURCE if s else VertexRole.TARGET if t else VertexRole.NEITHER
                context = SimpleNamespace(source_geometry=source, target_geometry=target, tolerance=TOL,
                                          triangulation=SimpleNamespace(vertex_roles=roles))
                try:
                    triangles = _strip(SimpleNamespace(boundary_vertices=vertices, geometry=region, kind=kind), context, lo, hi)
                except ValueError:
                    pass
            if triangles is None:
                extensions += 1
                triangles = []
                for triangle in constrained_delaunay_triangles(region).geoms:
                    points = []
                    for p in list(triangle.exterior.coords)[:-1]:
                        ds, dt = source.boundary.distance(Point(p)), target.boundary.distance(Point(p))
                        fraction = ds/(ds+dt) if ds+dt > TOL else .5
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
    from transition_targets import _end_extensions, _chain_strip
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
        triangles=_chain_strip(lower, upper, region, TOL)
        if any(Polygon([p[:2] for p in tri]).area <= TOL*TOL for tri in triangles):
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
    lower = [p[:2] for p in patch.points if abs(p[2]-lo)<TOL]
    boundary = list(source.exterior.coords)
    segments = [LineString([a,b]) for a,b in zip(boundary,boundary[1:])]
    if len(lower) >= 2:
        segment = LineString(lower[:2])
    else:
        # Same deterministic local boundary ownership as a one-lower-vertex strip.
        at = Point(lower[0]) if lower else patch.geometry.representative_point()
        segment = min(segments, key=lambda e: (e.distance(at), e.distance(patch.geometry.centroid)))
    candidates = [owner for owner, polygon in sources.items() if owner != patch.source and owner not in selected
                  and segment.difference(polygon.boundary.buffer(TOL)).length < TOL]
    return min(candidates) if candidates else None


@dataclass
class Column:
    points: list[tuple]
    old: int | None
    new: int | None
    heights: list[float]


def _layer_faces(old, new, patches, lo, hi):
    from aggregation_layer import _XYNodes
    xy_nodes = _XYNodes(TOL)
    for geometry in [*old.values(), *new.values()]:
        xy_nodes.add_geometry(geometry)
    boundaries = [p.boundary for p in [*old.values(), *new.values()]] + [p.geometry.boundary for p in patches]
    network = union_all(boundaries, grid_size=1e-8)
    domain = unary_union([*old.values(),*new.values()])
    columns = []
    seen_triangles = set()
    faces = []
    for region in polygonize(network):
        if region.area < 1e-10:
            continue
        point = region.representative_point()
        if not domain.covers(point):
            continue
        before = next((k for k,g in old.items() if g.buffer(TOL).covers(point)),None)
        after = next((k for k,g in new.items() if g.buffer(TOL).covers(point)),None)
        matching = [p for p in patches if p.geometry.buffer(TOL).covers(point)]
        patch = next((p for p in matching if p.source == before), next((p for p in matching if p.source == after), matching[0] if matching else None))
        if before != after and patch is None:
            raise ValueError(f"No transition surface for ownership change {before} → {after}.")
        for triangle in constrained_delaunay_triangles(region).geoms:
            if not region.covers(triangle.representative_point()):
                continue
            xy = [xyz(xy_nodes.canonical(p)) for p in orient(triangle,1).exterior.coords[:-1]]
            if len(set(xy)) != 3 or Polygon(xy).area < 1e-10:
                continue
            if 2*Polygon(xy).area / max(LineString([a,b]).length for a,b in zip(xy,xy[1:]+xy[:1])) < TOL*2:
                continue
            triangle_key = tuple(sorted(xy))
            if triangle_key in seen_triangles:
                continue
            seen_triangles.add(triangle_key)
            h = [float(np.clip(patch.height(p),lo,hi)) for p in xy] if before != after else [hi]*3
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
        for i in range(3):
            a,b = column.points[i],column.points[(i+1)%3]
            za,zb = column.heights[i],column.heights[(i+1)%3]
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
        hits=np.flatnonzero((ts>TOL)&(ts<1-TOL)&(np.linalg.norm(array-av-ts[:,None]*d,axis=1)<TOL))
        split=[(0.,a),*sorted([(float(ts[i]),nodes[i]) for i in hits]),(1.,b)]
        for (t,p),(u,q) in zip(split,split[1:]):
            for column,(ha,hb),forward in uses:
                atomic[p,q].append((column,(ha+t*(hb-ha),ha+u*(hb-ha)),forward))
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
            return c.old if z < h[0]+t*(h[1]-h[0]) else c.new
        for ta,tb in zip(cuts,cuts[1:]):
            tm=(ta+tb)/2
            ordered=sorted(functions,key=lambda f:f[0]+tm*(f[1]-f[0]))
            for lower,upper in zip(ordered,ordered[1:]):
                l=lower[0]+tm*(lower[1]-lower[0]); h=upper[0]+tm*(upper[1]-upper[0])
                if h-l<TOL:
                    continue
                ownleft,ownright=label(left,tm,(l+h)/2),label(right,tm,(l+h)/2)
                if ownleft == ownright:
                    continue
                p=tuple(a[k]+ta*(b[k]-a[k]) for k in range(2));q=tuple(a[k]+tb*(b[k]-a[k]) for k in range(2))
                points=[(*p,lower[0]+ta*(lower[1]-lower[0])),(*q,lower[0]+tb*(lower[1]-lower[0])),
                        (*q,upper[0]+tb*(upper[1]-upper[0])),(*p,upper[0]+ta*(upper[1]-upper[0]))]
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
        self.origin=tuple(float(v) for v in data.frame.total_bounds[:2])
        features={}
        faces=[]
        for _,row in data.frame.iterrows():
            index=int(row["_index"])
            geometry=set_precision(translate(row.geometry,xoff=-self.origin[0],yoff=-self.origin[1]),1e-8)
            attrs={k:v for k,v in row.items() if k not in (data.frame.geometry.name,"_index")}
            features[index]=Feature(index,geometry,attrs,(index,),f"Feature {index}")
            faces.extend(_prism(index,geometry,0.,initial_height))
        self.model,faces,self.core=realize(faces,self.origin)
        self.history=[State(features,faces,initial_height,"Initial extrusion")]
        self.cursor=0

    @property
    def state(self):
        return self.history[self.cursor]

    def preview(self, operation, selected, *, dz=10., tolerance=1., iterations=1, ratio=.25, winner=None):
        selected=tuple(sorted(set(map(int,selected))))
        state=self.state
        if not selected:
            raise ValueError("Select at least one polygon.")
        if not isfinite(dz) or dz <= TOL:
            raise ValueError("Height increment must be positive and finite.")
        if any(k not in state.features or state.features[k].geometry.geom_type != "Polygon" for k in selected):
            raise ValueError("Only current polygon features can be generalized; collapsed symbols are terminal.")
        lo,hi=state.z,state.z+dz
        sources={k:f.geometry for k,f in state.features.items() if f.geometry.geom_type == "Polygon"}
        # Ownership changes must be split where a neighbour starts or stops along
        # an edge, even if the input ring omits that degree-two boundary node.
        from aggregation_layer import _XYNodes
        nodes=_XYNodes(TOL)
        for geometry in sources.values():nodes.add_geometry(geometry)
        sources={k:Polygon(nodes.ring(orient(g,1).exterior)) for k,g in sources.items()}
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
                notes.append("Merge dissolves selected internal boundaries; earlier ownership remains in history.")
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
            remap={k:aggregate for k in selected}
        else:
            for k in selected:
                source=sources[k]
                if operation in ("simplification","smoothing"):
                    target=polygon_target(state.features[k].geometry,operation,tolerance,iterations,ratio)
                    target=set_precision(target,1e-8)
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
        new={k:set_precision(g,1e-8) for k,g in new.items()}
        for k,g in new.items():
            if g.is_empty or g.geom_type != "Polygon" or not g.is_valid or g.interiors:
                raise ValueError(f"Feature {k}: neighbour update creates an empty, split, invalid or holed footprint. Change the selection or parameters.")
        for i,(k,g) in enumerate(new.items()):
            for j,h in list(new.items())[:i]:
                if g.intersection(h).area > TOL*TOL:
                    raise ValueError(f"Updated footprints {j} and {k} overlap.")
        # Map selected source identities before dissolving historical interfaces.
        old={}
        for k,g in sources.items():
            owner=remap.get(k,k)
            old[owner]=unary_union([old.get(owner,Polygon()),g])
        historical=[Face(remap.get(f.owner,f.owner),list(f.points),f.part) for f in state.faces
                    if not all(abs(p[2]-lo)<TOL for p in f.points)]
        layer=_layer_faces(old,new,patches,lo,hi)
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
                new[k]=actual.simplify(TOL, preserve_topology=True)
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
        next_state=State(features,faces,hi,f"{operation}: {', '.join(map(str,selected))}; Z={lo:g}–{hi:g}",affected)
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
