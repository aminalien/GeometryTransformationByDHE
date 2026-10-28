"""Construct a conforming DHE upper partition with shared inclined interfaces."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import TYPE_CHECKING

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

import DHECore as dhe
from DHECore import face_edges
from active_region_triangulation import VertexRole, validate_triangulation
from top_face_overlay import AREA_TOLERANCE, RegionKind, cell_volume, face_polygon, roof_faces

if TYPE_CHECKING:
    from main import ExtrudedCell, ExtrusionModel

XYZ = tuple[float, float, float]


@dataclass
class TransitionFacet:
    cell_id: str
    seed: dhe.DualHalfEdge
    parent_region_id: str
    region_kind: RegionKind | None
    part: str
    inside_target: bool

    @property
    def label(self):
        return f"{self.cell_id}/{self.seed.info}"

    @property
    def xyz(self) -> list[XYZ]:
        return [(e.V.x, e.V.y, e.V.z) for e in face_edges(self.seed)]

    @property
    def geometry(self) -> Polygon:
        return face_polygon(self.seed)


@dataclass
class TransitionLayer:
    cell: ExtrudedCell
    cells: list[ExtrudedCell]
    source_z: float
    target_z: float
    source_geometry: Polygon
    target_geometry: BaseGeometry
    facets: list[TransitionFacet]
    shared_interfaces: list[tuple[dhe.DualHalfEdge, dhe.DualHalfEdge]]
    vertex_stages: dict[dhe.Vertex, str]
    vertex_roles: dict[dhe.Vertex, VertexRole]
    original_base_positions: dict[dhe.Vertex, XYZ]
    original_base_volumes: dict[str, float]
    expected_volumes: dict[str, float]
    original_neighbours: set[frozenset[str]]
    bottom_z: float
    projection_regions: list
    tolerance: float
    model: ExtrusionModel
    target_definition: object = None

    @property
    def target_kind(self):
        return {"Polygon": "polygon", "Point": "point", "LineString": "polyline"}[self.target_geometry.geom_type]

    @property
    def source_cell(self):
        return self.cell

    @property
    def connecting_facets(self):
        return [f for f in self.facets if f.part == "connecting"]

    @property
    def lower_facets(self):
        return [f for f in self.facets if f.part == "bottom_cap"]

    @property
    def upper_facets(self):
        return [f for f in self.facets if f.part == "upper_cap"]

    def horizontal_section(self, z: float) -> BaseGeometry:
        if not isfinite(z) or not self.source_z <= z <= self.target_z:
            raise ValueError("Section height must be within the transition interval.")
        if z == self.source_z:
            return self.source_geometry
        if z == self.target_z:
            return self.target_geometry
        segments = []
        intersections = {}
        for facet in self.connecting_facets:
            triangle = facet.xyz
            points = []
            for a, b in zip(triangle, triangle[1:] + triangle[:1]):
                if a[2] == b[2]:
                    continue
                # Cache an unordered edge in one canonical direction so adjoining
                # faces compute bit-identical section endpoints.
                a, b = sorted((a, b))
                if not min(a[2], b[2]) <= z <= max(a[2], b[2]):
                    continue
                key = (a, b)
                if key not in intersections:
                    fraction = (z - a[2]) / (b[2] - a[2])
                    intersections[key] = (
                        a[0] + fraction * (b[0] - a[0]),
                        a[1] + fraction * (b[1] - a[1]),
                    )
                point = intersections[key]
                if point not in points:
                    points.append(point)
            if len(points) == 2 and points[0] != points[1]:
                segments.append(LineString(points))
        pieces = list(polygonize(unary_union(segments)))
        if len(pieces) != 1 or not pieces[0].is_valid or pieces[0].interiors:
            raise ValueError("The transition section is not one closed, simple polygon.")
        return pieces[0]


@dataclass
class _FacetPlan:
    points: list[XYZ]
    parent_region_id: str
    kind: RegionKind | None
    part: str
    inside_target: bool
    base_seed: dhe.DualHalfEdge | None = None


def _signed_xy(points):
    return sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(points, points[1:] + points[:1]))


def _triangle_area(points):
    a, b, c = points
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return sqrt(sum(value * value for value in (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ))) / 2


def _strip(region, overlay, source_z, target_z):
    vertices = region.boundary_vertices
    roles = overlay.triangulation.vertex_roles
    crossings = [i for i, v in enumerate(vertices) if roles[v] is VertexRole.BOTH]
    if len(crossings) != 2:
        raise ValueError("Each active region must have exactly two source/target crossing endpoints.")
    i, j = crossings
    first = vertices[i:j + 1]
    second = list(reversed(vertices[j:] + vertices[:i + 1]))

    def on_boundary(path, polygon):
        return polygon.boundary.buffer(overlay.tolerance).covers(
            LineString([(v.x, v.y) for v in path])
        )

    on_first = on_boundary(first, overlay.source_geometry)
    on_second = on_boundary(second, overlay.source_geometry)
    if on_first == on_second:
        raise ValueError("An active region must have one identifiable source boundary chain.")
    source, target = (first, second) if on_first else (second, first)
    if not on_boundary(target, overlay.target_geometry):
        raise ValueError("An active region's opposite chain must follow the target boundary.")
    source = [(v.x, v.y, source_z) for v in source]
    target = [(v.x, v.y, target_z) for v in target]
    winding = _signed_xy(source + list(reversed(target)))
    sign = 1 if winding > 0 else -1

    def valid(triangle):
        if _triangle_area(triangle) <= overlay.tolerance ** 2:
            return False
        a, b, c = triangle
        cross = ((b[0] - a[0]) * (c[1] - a[1])
                 - (b[1] - a[1]) * (c[0] - a[0]))
        if cross * sign < -(overlay.tolerance ** 2):
            return False
        xy = [(p[0], p[1]) for p in triangle]
        geometry = Polygon(xy) if abs(cross) > overlay.tolerance ** 2 else LineString(xy)
        return region.geometry.buffer(overlay.tolerance).covers(geometry)

    # A state is the current cross-chain link. Advancing one chain adds one
    # mixed-height triangle. Deterministic source-first traversal breaks ties.
    states = {(0, 0): (0.0, [])}
    for i in range(len(source)):
        for j in range(len(target)):
            if (i, j) not in states:
                continue
            cost, triangles = states[i, j]
            moves = []
            if i + 1 < len(source):
                moves.append((i + 1, j, [source[i], source[i + 1], target[j]]))
            if j + 1 < len(target):
                moves.append((i, j + 1, [source[i], target[j + 1], target[j]]))
            for ni, nj, triangle in moves:
                if not valid(triangle):
                    continue
                new_cost = cost + sum((source[ni][k] - target[nj][k]) ** 2 for k in range(3))
                if (ni, nj) not in states or new_cost < states[ni, nj][0]:
                    states[ni, nj] = (new_cost, triangles + [triangle])
    endpoint = (len(source) - 1, len(target) - 1)
    if endpoint not in states:
        raise ValueError(f"Region {region.region_id} has no valid boundary-chain triangle strip.")
    triangles = states[endpoint][1]
    projected = [Polygon([(p[0], p[1]) for p in tri]) for tri in triangles]
    nonzero = [p for p in projected if p.area > overlay.tolerance ** 2]
    combined = unary_union(nonzero)
    if (combined.symmetric_difference(region.geometry).area > AREA_TOLERANCE
            or abs(sum(p.area for p in nonzero) - combined.area) > AREA_TOLERANCE):
        raise ValueError("A transition strip overlaps or fails to cover its active region.")
    desired = 1 if region.kind is RegionKind.ACTIVE_SOURCE_ONLY else -1
    if sign != desired:
        triangles = [list(reversed(triangle)) for triangle in triangles]
    return triangles


def _boundary_chains(region, overlay):
    vertices = region.boundary_vertices
    crossings = [i for i, v in enumerate(vertices)
                 if overlay.triangulation.vertex_roles[v] is VertexRole.BOTH]
    if len(crossings) != 2:
        raise ValueError("Each active region must have two source/target crossings.")
    i, j = crossings
    first = vertices[i:j + 1]
    second = list(reversed(vertices[j:] + vertices[:i + 1]))
    line = LineString([(v.x, v.y) for v in first])
    return first if overlay.source_geometry.boundary.buffer(overlay.tolerance).covers(line) else second


def _xy_key(a, b):
    return tuple(sorted((tuple(a[:2]), tuple(b[:2]))))


def _check_shell(plans):
    edges = defaultdict(list)
    for plan in plans:
        for a, b in zip(plan.points, plan.points[1:] + plan.points[:1]):
            if a == b:
                raise ValueError("A planned face has a zero-length edge.")
            edges[tuple(sorted((a, b)))].append((a, b))
    if any(len(uses) != 2 or uses[0] != tuple(reversed(uses[1]))
           for uses in edges.values()):
        raise ValueError("The planned transition shell is not a consistently oriented closed manifold.")
    vertices = {p for plan in plans for p in plan.points}
    if len(vertices) - len(edges) + len(plans) != 2:
        raise ValueError("The planned transition shell does not have genus-zero topology.")


def _preflight(model, target_z):
    if model.transition is not None:
        raise ValueError("This model already contains a transition layer.")
    if model.overlay is None or model.overlay.triangulation is None:
        raise ValueError("Construct the active-region triangulation before the transition layer.")
    overlay = model.overlay
    lo, hi = overlay.height, target_z
    if not isfinite(hi) or hi <= lo + overlay.tolerance:
        raise ValueError("Target elevation must be finite and greater than source elevation.")
    validate_triangulation(model)
    source_id = overlay.source_id
    plans = {cell.feature_id: [] for cell in model.cells}
    boundary = {}
    for cell in model.cells:
        polygon = overlay.original_roofs[cell.feature_id]
        for face in roof_faces(cell, lo):
            for edge in face_edges(face):
                a, b = (edge.V.x, edge.V.y), (edge.S.V.x, edge.S.V.y)
                if polygon.boundary.buffer(overlay.tolerance).covers(LineString([a, b])):
                    boundary[cell.feature_id, _xy_key(a, b)] = (
                        a, b, str(edge.S.Adjacent().D.V.i))
    assigned = defaultdict(list)
    strips = []
    for region in overlay.triangulation.regions:
        if not region.kind.active:
            continue
        chain = _boundary_chains(region, overlay)
        owners = [boundary[source_id, _xy_key((a.x, a.y), (b.x, b.y))][2]
                  for a, b in zip(chain, chain[1:])]
        for triangle in _strip(region, overlay, lo, hi):
            lower = [p for p in triangle if p[2] == lo]
            if region.kind is RegionKind.ACTIVE_TARGET_ONLY:
                neighbour = region.cell_id
            elif len(lower) == 2:
                neighbour = boundary[source_id, _xy_key(*lower)][2]
            else:
                i = next(i for i, v in enumerate(chain)
                         if (v.x, v.y) == lower[0][:2])
                neighbour = owners[min(i, len(owners) - 1)]
            if neighbour not in plans or neighbour == source_id:
                raise ValueError("Transition regions must have a constructed neighbour on every source edge.")
            plan = _FacetPlan(triangle, region.region_id, region.kind, "connecting",
                              region.kind is RegionKind.ACTIVE_TARGET_ONLY)
            plans[source_id].append(plan)
            plans[neighbour].append(_FacetPlan(
                list(reversed(triangle)), region.region_id, region.kind,
                "neighbour_transition", plan.inside_target))
            strips.append((plan, neighbour))
            projected = Polygon([p[:2] for p in triangle])
            if region.kind is RegionKind.ACTIVE_SOURCE_ONLY and projected.area > overlay.tolerance ** 2:
                assigned[neighbour].append(projected)
    # Where the source chain changes neighbour, continue the old triple junction
    # up to the shared inclined edge. Both neighbour cells own a side of this sheet.
    strip_edges = defaultdict(list)
    for plan, neighbour in strips:
        for a, b in zip(plan.points, plan.points[1:] + plan.points[:1]):
            strip_edges[tuple(sorted((a, b)))].append((a, b, neighbour))
    for uses in strip_edges.values():
        if len(uses) != 2 or uses[0][2] == uses[1][2]:
            continue
        a, b, first = uses[0]
        second = uses[1][2]
        lower = next(p for p in (a, b) if p[2] == lo)
        apex = (lower[0], lower[1], hi)
        points = [a, b, apex]
        if _triangle_area(points) <= overlay.tolerance ** 2:
            continue
        plans[first].append(_FacetPlan(points, "junction", RegionKind.BACKGROUND, "separator", False))
        plans[second].append(_FacetPlan(list(reversed(points)), "junction",
                                        RegionKind.BACKGROUND, "separator", False))
    # Carry neighbour-neighbour walls and the exterior boundary vertically.
    # Source-neighbour walls are replaced by the shared triangle strips.
    for (owner, _), (a, b, neighbour) in boundary.items():
        if owner == source_id or neighbour == source_id:
            continue
        ring = orient(overlay.original_roofs[owner], sign=1).exterior
        forward = (ring.project(Point(b)) - ring.project(Point(a))) % ring.length
        if abs(forward - LineString([a, b]).length) > overlay.tolerance:
            a, b = b, a
        points = [(*a, lo), (*b, lo), (*b, hi), (*a, hi)]
        plans[owner].append(_FacetPlan(points, "wall", RegionKind.BACKGROUND, "wall", False))
    upper_polygons = {}
    for cell in model.cells:
        owner = cell.feature_id
        if owner == source_id:
            polygon = overlay.target_geometry
        else:
            polygon = unary_union([
                overlay.original_roofs[owner].difference(overlay.target_geometry),
                *assigned[owner],
            ])
        if not isinstance(polygon, Polygon) or not polygon.is_valid or polygon.interiors:
            raise ValueError("Each upper feature must be a valid single-ring polygon.")
        upper_polygons[owner] = polygon
    combined = unary_union(list(upper_polygons.values()))
    domain = unary_union(list(overlay.original_roofs.values()))
    if (domain.symmetric_difference(combined).area > AREA_TOLERANCE
            or abs(sum(p.area for p in upper_polygons.values()) - combined.area) > AREA_TOLERANCE):
        raise ValueError("The upper feature partition has gaps or overlapping cells.")
    for fragment in overlay.fragments:
        points = [(e.V.x, e.V.y, lo) for e in face_edges(fragment.seed)]
        if _signed_xy(points) > 0:
            points.reverse()
        plans[fragment.cell_id].append(_FacetPlan(
            points, fragment.parent_region_id, fragment.region_kind, "lower_cap",
            fragment.inside_target, fragment.seed))
    # Node each upper boundary at the wall endpoints; avoid copying source-only
    # vertices into the source's target cap.
    for owner, polygon in upper_polygons.items():
        ring = orient(polygon, sign=1).exterior
        nodes = {tuple(p[:2]) for p in ring.coords[:-1]}
        nodes.update(p[:2] for plan in plans[owner] for p in plan.points
                     if p[2] == hi and ring.distance(Point(p[:2])) <= overlay.tolerance)
        ordered = sorted(nodes, key=lambda p: ring.project(Point(p)))
        cap = Polygon(ordered)
        points = [(*p[:2], hi) for p in cap.exterior.coords[:-1]]
        plans[owner].append(_FacetPlan(points, "upper", None,
                                       "upper_cap", owner == source_id))
        if cap.symmetric_difference(polygon).area > AREA_TOLERANCE:
            raise ValueError("An upper cap does not cover its feature polygon.")
    # Match the base DHE's normal convention, preserving opposite lower interfaces.
    for owner, shell in plans.items():
        lower = next(p for p in shell if p.base_seed is not None)
        base = [(e.V.x, e.V.y, e.V.z) for e in face_edges(lower.base_seed)]
        if _signed_xy(base) * _signed_xy(lower.points) > 0:
            for plan in shell:
                plan.points.reverse()
        _check_shell(shell)
    # Check inter-cell incidence before allocating any DHE records.
    ownership = defaultdict(list)
    for owner, shell in plans.items():
        for plan in shell:
            if plan.base_seed is None:
                ownership[tuple(sorted(plan.points))].append((owner, plan))
    neighbours = set()
    for uses in ownership.values():
        if len(uses) > 2:
            raise ValueError("A planned interface has more than two incident cells.")
        if len(uses) == 2:
            neighbours.add(frozenset(owner for owner, _ in uses))
        elif uses[0][1].part in ("connecting", "neighbour_transition", "separator"):
            raise ValueError("An inclined interface has no matching neighbour face.")
    expected = {frozenset((str(a.D.V.i), str(b.D.V.i))) for a, b in model.shared_walls}
    if neighbours != expected:
        raise ValueError("The planned upper partition changes the logical neighbour relationships.")
    return plans


def _planned_volume(plans):
    volume = 0.0
    for plan in plans:
        a = plan.points[0]
        for b, c in zip(plan.points[1:-1], plan.points[2:]):
            volume += (a[0] * (b[1]*c[2] - b[2]*c[1])
                       - a[1] * (b[0]*c[2] - b[2]*c[0])
                       + a[2] * (b[0]*c[1] - b[1]*c[0])) / 6
    return abs(volume)


@dataclass(frozen=True)
class ProjectionRegion:
    """Geometry-only provenance; no pointers into the retired stage topology."""
    cell_id: str
    region_id: str
    geometry: Polygon
    region_kind: RegionKind
    inside_target: bool
    seed: None = None

    @property
    def label(self):
        return self.region_id


def build_transition_layer(model: ExtrusionModel, target_z: float = 10.0, *,
                           target=None, source_id="3000", source_z=5.0) -> TransitionLayer:
    """Union base and upper boundaries into one closed DHE cell per feature."""
    prepared = None
    if target is not None and target.kind != "polygon":
        from transition_targets import prepare_collapse
        prepared = prepare_collapse(model, target, str(source_id), source_z, target_z)
        upper_plans, overlay = prepared.upper_plans, prepared.context
    else:
        upper_plans = _preflight(model, target_z)
        overlay = model.overlay
    old_edges = model.active_edges()
    positions = {e.V: (e.V.x, e.V.y, e.V.z) for e in old_edges}
    volumes = {cell.feature_id: cell_volume(cell) for cell in model.cells}
    bottom_z = min(p[2] for p in positions.values())
    neighbours = {frozenset((str(a.D.V.i), str(b.D.V.i))) for a, b in model.shared_walls}
    expected = {owner: volumes[owner] + _planned_volume(shell)
                for owner, shell in upper_plans.items()}
    plans = {}
    # The union removes both coincident sides of every Z=source partition.
    # Rebuild the surviving boundary using a single dual vertex, including caps
    # as polygon faces. No hidden second cell or internal horizontal face remains.
    for cell in model.cells:
        owner = cell.feature_id
        shell = [p for p in upper_plans[owner] if p.part != "lower_cap"]
        base_faces = (prepared.base_faces[owner] if prepared else [
            [(e.V.x, e.V.y, e.V.z) for e in face_edges(face)]
            for face in dhe.get_UniqueFace_in_cell(cell.seed)])
        for points in base_faces:
            if all(p[2] == overlay.height for p in points):
                continue
            part = "bottom_cap" if all(p[2] == bottom_z for p in points) else "base_wall"
            shell.append(_FacetPlan(points, part, None, part, False))
        _check_shell(shell)
        if abs(_planned_volume(shell) - expected[owner]) > max(AREA_TOLERANCE, expected[owner]*1e-9):
            raise ValueError("A merged shell changes its feature volume.")
        plans[owner] = shell
    projection_regions = prepared.projection_regions if prepared else [
        ProjectionRegion(r.cell_id, r.region_id, r.geometry, r.kind,
                         r.kind in (RegionKind.ACTIVE_TARGET_ONLY, RegionKind.COMMON_NONACTIVE))
        for r in overlay.triangulation.regions]
    cache = {position: vertex for vertex, position in positions.items()}
    stages, roles = {}, {}
    role_by_xy = (prepared.role_by_xy if prepared else
                  {(v.x, v.y): role for v, role in overlay.triangulation.vertex_roles.items()})
    facets, cells = [], []
    matching_faces = defaultdict(list)
    for original in model.cells:
        owner = original.feature_id
        center = overlay.original_roofs[owner].representative_point()
        dual = dhe.make_vertex(center.x, center.y, (bottom_z + target_z) / 2,
                               ch=owner, clr=original.dual_vertex.color)
        edge_pairs = defaultdict(list)
        cell_facets = []
        for plan in plans[owner]:
            vertices = []
            for point in plan.points:
                if point not in cache:
                    cache[point] = dhe.make_vertex(*point)
                vertex = cache[point]
                vertices.append(vertex)
                stages[vertex] = ("bottom" if point[2] == bottom_z else
                                  "lower" if point[2] == overlay.height else "upper")
                roles[vertex] = role_by_xy.get(point[:2], VertexRole.NEITHER)
            first = dhe.MEVVFS(vertices[0], vertices[1], dual, model.exterior)
            cursor = first
            for vertex in vertices[2:]:
                cursor = dhe.MEV(vertex, cursor.S)
            seed = dhe.MEF(first, cursor.S).S
            facet = TransitionFacet(owner, seed, plan.parent_region_id, plan.kind,
                                    plan.part, plan.inside_target)
            facets.append(facet)
            cell_facets.append(facet)
            matching_faces[tuple(sorted(plan.points))].append(seed)
            for edge in face_edges(seed):
                edge_pairs[frozenset((edge.V, edge.S.V))].append(edge)
        for first, second in edge_pairs.values():
            dhe.Sew(first, second.S)
        cells.append(type(original)(owner, cell_facets[0].seed, dual, owner))
    shared = []
    for matching in matching_faces.values():
        if len(matching) > 2:
            raise ValueError("A final interface has more than two incident cells.")
        if len(matching) != 2:
            continue
        first, other = matching
        second = next(e for e in face_edges(other)
                      if e.V is first.S.V and e.S.V is first.V)
        dhe.JoinByFace(first, second)
        shared.append((first, second))
    # Only intermediate records are retired; primal vertices on the surviving
    # boundary are reused without coordinate changes.
    killed = dhe.Getdhe()[1]
    for e in old_edges:
        for record in (e, e.S, e.Adjacent(), e.Adjacent().S):
            killed.update((record, record.D))
    model.cells = cells
    model.shared_walls = shared
    model.overlay = overlay
    result = TransitionLayer(
        cell=next(c for c in cells if c.feature_id == overlay.source_id), cells=cells,
        source_z=overlay.height, target_z=target_z, bottom_z=bottom_z,
        source_geometry=overlay.source_geometry, target_geometry=overlay.target_geometry,
        facets=facets, shared_interfaces=shared, vertex_stages=stages, vertex_roles=roles,
        original_base_positions=positions, original_base_volumes=volumes,
        expected_volumes=expected, original_neighbours=neighbours,
        projection_regions=projection_regions, tolerance=overlay.tolerance, model=model,
        target_definition=target)
    model.transition = result
    validate_transition_layer(model)
    return result


def slice_model(model: ExtrusionModel, z: float) -> dict[str, Polygon]:
    """Horizontal compatibility API for the arbitrary-plane DHE slicer."""
    from plane_slicing import SlicePlane, slice_model_plane
    all_edges = model.active_edges()
    if not isfinite(z) or not min(e.V.z for e in all_edges) <= z <= max(e.V.z for e in all_edges):
        raise ValueError("Section height must be within the model interval.")
    return slice_model_plane(model, SlicePlane.horizontal(z))


def validate_transition_layer(model: ExtrusionModel) -> None:
    layer = model.transition
    if layer is None:
        raise ValueError("The model has no transition layer.")
    from main import validate_model
    validate_model(model)
    if model.cells != layer.cells or len(model.cells) != len(model.overlay.original_roofs):
        raise ValueError("The final model must contain one merged cell per feature.")
    for vertex, old in layer.original_base_positions.items():
        if (vertex.x, vertex.y, vertex.z) != old:
            raise ValueError("Final construction moved an original vertex.")
    actual_neighbours = {frozenset((str(a.D.V.i), str(b.D.V.i))) for a, b in model.shared_walls}
    if actual_neighbours != layer.original_neighbours:
        raise ValueError("Final construction changes feature neighbours.")
    for cell in model.cells:
        recorded = [f for f in layer.facets if f.cell_id == cell.feature_id]
        faces = dhe.get_UniqueFace_in_cell(cell.seed)
        if {frozenset(face_edges(f)) for f in faces} != {
                frozenset(face_edges(f.seed)) for f in recorded}:
            raise ValueError("Facet metadata does not match the final DHE cell.")
        horizontal = [f for f in recorded if len({p[2] for p in f.xyz}) == 1]
        expected_levels = ({layer.bottom_z} if cell is layer.cell and layer.target_kind != "polygon"
                           else {layer.bottom_z, layer.target_z})
        if len(horizontal) != len(expected_levels) or {f.xyz[0][2] for f in horizontal} != expected_levels:
            raise ValueError("A final cell has incorrect polygon caps for its target dimension.")
        actual = cell_volume(cell)
        if actual <= 0 or abs(actual - layer.expected_volumes[cell.feature_id]) > max(AREA_TOLERANCE, actual*1e-9):
            raise ValueError("A final feature has an incorrect volume.")
    for vertex in {e.V for e in dhe.Cell(layer.cell.seed)}:
        if layer.vertex_roles[vertex] is VertexRole.SOURCE and vertex.z == layer.target_z:
            raise ValueError("A source-only vertex remains on the target cap.")
    for facet in layer.connecting_facets:
        if (len(facet.xyz) != 3 or _triangle_area(facet.xyz) <= layer.tolerance ** 2
                or {p[2] for p in facet.xyz} != {layer.source_z, layer.target_z}
                or any(e.Adjacent().D.V is model.exterior for e in face_edges(facet.seed))):
            raise ValueError("An inclined source-neighbour triangle is degenerate or unshared.")
    domain = unary_union(list(model.overlay.original_roofs.values()))
    common = layer.source_geometry.intersection(layer.target_geometry)
    envelope = layer.source_geometry.union(layer.target_geometry)
    for fraction in (0, 0.001, 0.25, 0.5, 0.75, 0.999, 1):
        z = layer.source_z + fraction * (layer.target_z - layer.source_z)
        sections = slice_model(model, z)
        source = sections.get(model.overlay.source_id, Polygon())
        if (common.difference(source).area > AREA_TOLERANCE
                or source.difference(envelope).area > AREA_TOLERANCE):
            raise ValueError("A source section leaves its transition envelope.")
        combined = unary_union(list(sections.values()))
        if (any(not p.is_valid for p in sections.values())
                or domain.symmetric_difference(combined).area > AREA_TOLERANCE
                or abs(sum(p.area for p in sections.values()) - combined.area) > AREA_TOLERANCE):
            raise ValueError("An intermediate feature partition has gaps or overlaps.")
    expected_total = domain.area * (layer.target_z - layer.bottom_z)
    if abs(sum(cell_volume(c) for c in model.cells) - expected_total) > max(AREA_TOLERANCE, expected_total*1e-9):
        raise ValueError("Merged cells do not fill the selected domain.")
