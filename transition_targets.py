"""Point and open-polyline targets and geometry-only collapse preflight."""

from collections import defaultdict
from dataclasses import dataclass, field
from math import hypot, isfinite
from pathlib import Path

import geopandas as gpd
from shapely import force_2d
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

import DHECore as dhe
from active_region_triangulation import VertexRole
from top_face_overlay import RegionKind, AREA_TOLERANCE, face_polygon, roof_faces

LINE_SHP = "C:/University/PhD/Papers/Third Journal Paper/QGIS/OSM/LineGenerlized.shp"
LINE_ID = 3001


@dataclass(frozen=True)
class TargetDefinition:
    kind: str
    identifier: str
    geometry: BaseGeometry

    def __post_init__(self):
        object.__setattr__(self, "geometry", force_2d(self.geometry))
        expected = {"polygon": "Polygon", "point": "Point", "polyline": "LineString"}
        if self.kind not in expected or self.geometry.geom_type != expected[self.kind]:
            raise ValueError("Target kind does not match its geometry.")
        if self.geometry.is_empty or not self.geometry.is_valid:
            raise ValueError("The target must be valid and nonempty.")
        coordinates = (self.geometry.exterior.coords if self.kind == "polygon" else self.geometry.coords)
        if any(not isfinite(float(v)) for p in coordinates for v in p):
            raise ValueError("Target coordinates must be finite.")


def load_line_target(path, identifier, origin, projected_crs="EPSG:32633"):
    if not Path(path).is_file():
        raise FileNotFoundError(f"Line shapefile not found: {path}")
    frame = gpd.read_file(path)
    if "osm_id" not in frame.columns or frame.crs is None:
        raise ValueError("The line shapefile must declare a CRS and contain osm_id.")
    mask = frame.osm_id.astype("string").str.strip() == str(identifier)
    if mask.sum() != 1:
        raise ValueError(f"Polyline ID {identifier} must identify exactly one feature.")
    geometry = frame.loc[mask].to_crs(projected_crs).geometry.iloc[0]
    geometry = translate(geometry, xoff=-origin[0], yoff=-origin[1])
    return TargetDefinition("polyline", str(identifier), geometry)


@dataclass
class TransitionContext:
    source_id: str
    target_id: str
    height: float
    original_roofs: dict
    target_geometry: BaseGeometry
    tolerance: float
    triangulation: None = None
    crossing_vertices: list = field(default_factory=list)
    fragments: list = field(default_factory=list)

    @property
    def source_geometry(self):
        return self.original_roofs[self.source_id]


@dataclass
class PreparedCollapse:
    upper_plans: dict
    base_faces: dict
    context: TransitionContext
    role_by_xy: dict
    projection_regions: list
    boundary_chains: list = field(default_factory=list)
    endpoint_extensions: list = field(default_factory=list)


def _node_loop(points, nodes, tolerance):
    result = []
    for a, b in zip(points, points[1:]+points[:1]):
        result.append(a)
        delta = [b[k]-a[k] for k in range(3)]
        length2 = sum(v*v for v in delta)
        insertions = []
        for point in nodes:
            t = sum((point[k]-a[k])*delta[k] for k in range(3))/length2
            if tolerance < t < 1-tolerance and sum(
                    (a[k]+t*delta[k]-point[k])**2 for k in range(3)) <= tolerance**2:
                insertions.append((t, point))
        result.extend(p for _, p in sorted(insertions))
    return result


def _end_extensions(source, target):
    xy = list(target.coords)
    distance = 4*hypot(source.bounds[2]-source.bounds[0], source.bounds[3]-source.bounds[1])
    ends = []
    for p, q in ((xy[0], xy[1]), (xy[-1], xy[-2])):
        dx, dy = p[0]-q[0], p[1]-q[1]
        length = hypot(dx, dy)
        ray = LineString([p, (p[0]+dx/length*distance, p[1]+dy/length*distance)])
        hits = ray.intersection(source.boundary)
        points = list(hits.geoms) if hits.geom_type == "MultiPoint" else [hits]
        if not points or any(point.geom_type != "Point" for point in points):
            raise ValueError("A polyline endpoint extension cannot meet the source boundary cleanly.")
        hit = min(points, key=lambda point: point.distance(Point(p)))
        ends.append((hit.x, hit.y))
    cut = LineString([ends[0], *xy, ends[1]])
    if not cut.is_simple or not source.buffer(1e-7).covers(cut):
        raise ValueError("Polyline endpoint extensions must form a simple interior boundary-to-boundary cut.")
    return ends


def _chain_strip(lower, upper, region, tolerance):
    from transition_layer import _signed_xy, _triangle_area
    sign = 1 if _signed_xy(lower+list(reversed(upper))) > 0 else -1
    covered = region.buffer(tolerance)
    def valid(triangle):
        cross = _signed_xy(triangle)
        if cross*sign < -tolerance**2 or _triangle_area(triangle) <= tolerance**2:
            return False
        xy = [p[:2] for p in triangle]
        geometry = Polygon(xy) if abs(cross) > tolerance**2 else LineString(xy)
        return covered.covers(geometry)
    states = {(0, 0): (0., [])}
    for i in range(len(lower)):
        for j in range(len(upper)):
            if (i, j) not in states:
                continue
            cost, triangles = states[i, j]
            moves = []
            if i+1 < len(lower):
                moves.append((i+1, j, [lower[i], lower[i+1], upper[j]]))
            if j+1 < len(upper):
                moves.append((i, j+1, [lower[i], upper[j+1], upper[j]]))
            for ni, nj, triangle in moves:
                if not valid(triangle):
                    continue
                new_cost = cost + sum((lower[ni][k]-upper[nj][k])**2 for k in range(3))
                if (ni, nj) not in states or new_cost < states[ni, nj][0]:
                    states[ni, nj] = new_cost, triangles+[triangle]
    endpoint = (len(lower)-1, len(upper)-1)
    if endpoint not in states:
        raise ValueError("The polyline cannot form a valid inclined boundary-chain strip.")
    triangles = states[endpoint][1]
    projected = [Polygon([p[:2] for p in tri]) for tri in triangles]
    area = [p for p in projected if p.area > tolerance**2]
    combined = unary_union(area)
    if (combined.symmetric_difference(region).area > AREA_TOLERANCE
            or abs(sum(p.area for p in area)-combined.area) > AREA_TOLERANCE):
        raise ValueError("A polyline strip has gaps or overlaps.")
    return [list(reversed(tri)) if sign < 0 else tri for tri in triangles]


def prepare_collapse(model, target, source_id, source_z, target_z, tolerance=1e-6):
    from transition_layer import _FacetPlan, ProjectionRegion, _check_shell, _signed_xy, _triangle_area
    if model.transition is not None:
        raise ValueError("This model already contains a transition.")
    if not all(isfinite(z) for z in (source_z, target_z)) or target_z <= source_z+tolerance:
        raise ValueError("Target elevation must be greater than the finite source elevation.")
    cells = {c.feature_id: c for c in model.cells}
    if source_id not in cells:
        raise ValueError("The source is not a constructed feature.")
    roofs = {}
    for cell in model.cells:
        faces = roof_faces(cell, source_z)
        if len(faces) != 1:
            raise ValueError("Collapse requires one unsplit roof per base cell.")
        roofs[cell.feature_id] = faces[0]
    source = face_polygon(roofs[source_id])
    geometry = target.geometry
    nodes = []
    if target.kind == "point":
        if not source.contains(geometry) or not all(
                source.buffer(tolerance).covers(LineString([p, (geometry.x, geometry.y)]))
                for p in source.exterior.coords[:-1]):
            raise ValueError("The point must be interior and visible from every source boundary vertex.")
    elif target.kind == "polyline":
        xy = list(geometry.coords)
        if (not geometry.is_simple or geometry.is_ring or geometry.length <= tolerance
                or any(hypot(b[0]-a[0], b[1]-a[1]) <= tolerance for a,b in zip(xy, xy[1:]))
                or not source.contains(geometry) or source.boundary.distance(geometry) <= tolerance):
            raise ValueError("Polyline collapse requires a simple open line strictly inside the source.")
        ends = _end_extensions(source, geometry)
        nodes = [(*p, source_z) for p in ends]
    else:
        raise ValueError("Collapse targets must be a point or polyline.")
    base_faces, footprints, boundary = {}, {}, {}
    for cell in model.cells:
        owner = cell.feature_id
        base_faces[owner] = [
            _node_loop([(e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(face)], nodes, tolerance)
            for face in dhe.get_UniqueFace_in_cell(cell.seed)]
        roof = _node_loop([(e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(roofs[owner])], nodes, tolerance)
        footprints[owner] = Polygon([p[:2] for p in roof])
        for edge in dhe.face_edges(roofs[owner]):
            a, b = (edge.V.x,edge.V.y,source_z), (edge.S.V.x,edge.S.V.y,source_z)
            segments = _node_loop([a,b], nodes, tolerance)
            # The two-edge loop also nodes the reverse edge; retain its forward half.
            stop = segments.index(b)
            forward = segments[:stop+1]
            neighbour = str(edge.S.Adjacent().D.V.i)
            for first, second in zip(forward, forward[1:]):
                boundary[owner, tuple(sorted((first[:2],second[:2])))] = first[:2],second[:2],neighbour
    ring = list(orient(footprints[source_id], 1).exterior.coords)[:-1]
    triangles = []
    chains = []
    if target.kind == "point":
        apex = (geometry.x, geometry.y, target_z)
        for a,b in zip(ring,ring[1:]+ring[:1]):
            triangles.append(([(*a,source_z),(*b,source_z),apex],
                              boundary[source_id,tuple(sorted((a,b)))][2]))
    else:
        i = min(range(len(ring)), key=lambda i: Point(ring[i]).distance(Point(ends[0])))
        ring = ring[i:]+ring[:i]
        j = min(range(len(ring)), key=lambda i: Point(ring[i]).distance(Point(ends[1])))
        chains = [ring[:j+1], list(reversed(ring[j:]+ring[:1]))]
        upper = [(*p[:2],target_z) for p in geometry.coords]
        for chain in chains:
            region = Polygon(chain+list(reversed(list(geometry.coords))))
            if not region.is_valid or region.area <= tolerance**2:
                raise ValueError("The extended polyline must divide the source into two valid regions.")
            lower = [(*p,source_z) for p in chain]
            owners = [boundary[source_id,tuple(sorted((a,b)))][2] for a,b in zip(chain,chain[1:])]
            for tri in _chain_strip(lower, upper, region, tolerance):
                lo = [p for p in tri if p[2] == source_z]
                if len(lo) == 2:
                    neighbour = boundary[source_id,tuple(sorted(p[:2] for p in lo))][2]
                else:
                    i = chain.index(lo[0][:2])
                    neighbour = owners[min(i,len(owners)-1)]
                triangles.append((tri,neighbour))
    plans = {owner:[] for owner in cells}
    assigned = defaultdict(list)
    edges = defaultdict(list)
    for tri, owner in triangles:
        if owner not in cells or owner == source_id:
            raise ValueError("Every source boundary edge needs a constructed neighbouring cell.")
        parent = f"{source_id}/collapse"
        plans[source_id].append(_FacetPlan(tri,parent,RegionKind.ACTIVE_SOURCE_ONLY,"connecting",False))
        plans[owner].append(_FacetPlan(list(reversed(tri)),parent,RegionKind.ACTIVE_SOURCE_ONLY,
                                       "neighbour_transition",False))
        projected = Polygon([p[:2] for p in tri])
        if projected.area > tolerance**2:
            assigned[owner].append(projected)
        for a,b in zip(tri,tri[1:]+tri[:1]):
            edges[tuple(sorted((a,b)))].append((a,b,owner))
    for uses in edges.values():
        if len(uses) != 2 or uses[0][2] == uses[1][2]:
            continue
        a,b,owner = uses[0]
        if a[2] == b[2] == target_z:
            continue
        lower = next(p for p in (a,b) if p[2] == source_z)
        tri = [a,b,(lower[0],lower[1],target_z)]
        if _triangle_area(tri) <= tolerance**2:
            continue
        for other, points in ((owner,tri),(uses[1][2],list(reversed(tri)))):
            plans[other].append(_FacetPlan(points,"junction",RegionKind.BACKGROUND,"separator",False))
    for (owner,_),(a,b,neighbour) in boundary.items():
        if owner == source_id or neighbour == source_id:
            continue
        outer = orient(footprints[owner],1).exterior
        if abs((outer.project(Point(b))-outer.project(Point(a))) % outer.length
               - LineString([a,b]).length) > tolerance:
            a,b = b,a
        plans[owner].append(_FacetPlan([(*a,source_z),(*b,source_z),(*b,target_z),(*a,target_z)],
                                      "wall",RegionKind.BACKGROUND,"wall",False))
    caps = []
    for owner in cells:
        lower = [(*p[:2],source_z) for p in orient(footprints[owner],-1).exterior.coords[:-1]]
        plans[owner].append(_FacetPlan(lower,"lower",None,"lower_cap",False))
        if owner == source_id:
            continue
        polygon = unary_union([footprints[owner],*assigned[owner]])
        if polygon.geom_type != "Polygon" or not polygon.is_valid or polygon.interiors:
            raise ValueError("A neighbouring top footprint is not a valid single-ring polygon.")
        caps.append(polygon)
        outer = orient(polygon,1).exterior
        cap_nodes = {tuple(p[:2]) for p in outer.coords[:-1]}
        cap_nodes.update(p[:2] for plan in plans[owner] for p in plan.points
                         if p[2] == target_z and outer.distance(Point(p[:2])) <= tolerance)
        ordered = sorted(cap_nodes,key=lambda p: outer.project(Point(p)))
        plans[owner].append(_FacetPlan([(*p,target_z) for p in ordered],"upper",None,"upper_cap",False))
    domain = unary_union(list(footprints.values()))
    combined = unary_union(caps)
    if (combined.symmetric_difference(domain).area > AREA_TOLERANCE
            or abs(sum(p.area for p in caps)-domain.area) > AREA_TOLERANCE):
        raise ValueError("The collapsed top partition has gaps or overlaps.")
    for owner,shell in plans.items():
        if _signed_xy([(e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(roofs[owner])]) < 0:
            for plan in shell:
                plan.points.reverse()
        _check_shell(shell)
    uses = defaultdict(list)
    for owner,shell in plans.items():
        for plan in shell:
            uses[tuple(sorted(plan.points))].append(owner)
    neighbours = {frozenset(owners) for owners in uses.values() if len(owners) == 2}
    expected = {frozenset((str(a.D.V.i),str(b.D.V.i))) for a,b in model.shared_walls}
    if neighbours != expected or any(len(owners)>2 for owners in uses.values()):
        raise ValueError("The collapse changes the original logical neighbour relationships.")
    role_by_xy = {p:VertexRole.SOURCE for p in ring}
    role_by_xy.update({tuple(p[:2]):VertexRole.TARGET for p in geometry.coords})
    regions = [ProjectionRegion(owner,f"{owner}/collapse",polygon,
               RegionKind.ACTIVE_SOURCE_ONLY if owner == source_id else RegionKind.BACKGROUND,False)
               for owner,polygon in footprints.items()]
    context = TransitionContext(source_id,target.identifier,source_z,footprints,geometry,tolerance)
    return PreparedCollapse(plans,base_faces,context,role_by_xy,regions,chains,
                            ends if target.kind == "polyline" else [])
