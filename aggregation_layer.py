"""A closed, single-cell DHE aggregate built from exterior skeleton growth."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import product
from math import hypot, isfinite

from shapely import constrained_delaunay_triangles, union_all
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

import DHECore as dhe
from DHECore import face_edges
from aggregation_geometry import AggregationPreview, SOURCE_COLORS, polygon_parts, validate_aggregation
from top_face_overlay import cell_volume
from transition_layer import _check_shell, _planned_volume

AGGREGATE_COLOR = (0.28, 0.48, 0.68, 1.0)
EXTERIOR_COLOR = (0.55, 0.2, 0.75, 1.0)


@dataclass
class AggregationFacet:
    seed: dhe.DualHalfEdge
    part: str
    source_id: str | None

    @property
    def xyz(self):
        return [(e.V.x, e.V.y, e.V.z) for e in face_edges(self.seed)]

    @property
    def geometry(self):
        return Polygon([p[:2] for p in self.xyz])


@dataclass
class _FacePlan:
    points: list
    part: str
    source_id: str | None


@dataclass
class AggregationLayer:
    preview: AggregationPreview
    cell: object
    model: object
    bottom_z: float
    source_z: float
    merge_z: float
    target_z: float
    maximum_time: float
    facets: list[AggregationFacet]
    expected_volume: float
    source_colors: dict
    _face_sources: dict

    @property
    def connecting_facets(self):
        return [f for f in self.facets if f.part == "connecting"]

    def face_color(self, edge):
        if edge.D.V is self.model.exterior:
            return EXTERIOR_COLOR
        key = tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(edge)))
        return self.source_colors.get(self._face_sources.get(key), AGGREGATE_COLOR)

    def horizontal_section(self, z):
        """Analytical wavefront section, independent of the DHE slicing code."""
        if not isfinite(z) or not self.bottom_z <= z <= self.target_z:
            raise ValueError("Section height must lie within the aggregate.")
        if z <= self.source_z:
            return self.preview.union
        if z >= self.merge_z or self.maximum_time == 0:
            return self.preview.hull
        time = (z - self.source_z) / (self.merge_z - self.source_z) * self.maximum_time
        pieces = [self.preview.union]
        for face in self.preview.growth_faces:
            for polygon in polygon_parts(face.geometry):
                # Clip a bounding rectangle by the source edge's moving line;
                # this handles concave faces and faces with holes without a mesh.
                xmin, ymin, xmax, ymax = self.preview.hull.bounds
                pad = max(xmax - xmin, ymax - ymin, 1)
                rectangle = [(xmin-pad, ymin-pad), (xmax+pad, ymin-pad),
                             (xmax+pad, ymax+pad), (xmin-pad, ymax+pad)]
                clipped = []
                for a, b in zip(rectangle, rectangle[1:] + rectangle[:1]):
                    va, vb = _arrival(face, a) - time, _arrival(face, b) - time
                    if va <= 0:
                        clipped.append(a)
                    if (va < 0 < vb) or (vb < 0 < va):
                        fraction = va / (va - vb)
                        clipped.append(tuple(a[k] + fraction * (b[k] - a[k]) for k in range(2)))
                if len(clipped) >= 3:
                    pieces.append(polygon.intersection(Polygon(clipped)))
        return union_all(pieces)


def _arrival(face, xy):
    a, b = face.source_edge.coords
    return ((b[0]-a[0]) * (xy[1]-a[1]) - (b[1]-a[1]) * (xy[0]-a[0])) / hypot(b[0]-a[0], b[1]-a[1])


class _XYNodes:
    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.bins = defaultdict(list)
        self.points = []

    def canonical(self, point):
        point = tuple(float(v) for v in point[:2])
        key = tuple(round(v / self.tolerance) for v in point)
        for offset in product((-1, 0, 1), repeat=2):
            for other in self.bins[(key[0]+offset[0], key[1]+offset[1])]:
                if hypot(point[0]-other[0], point[1]-other[1]) <= self.tolerance:
                    return other
        self.bins[key].append(point)
        self.points.append(point)
        return point

    def add_geometry(self, geometry):
        for polygon in polygon_parts(geometry):
            for ring in [polygon.exterior, *polygon.interiors]:
                for point in list(ring.coords)[:-1]:
                    self.canonical(point)

    def ring(self, ring):
        coords = [self.canonical(p) for p in ring.coords]
        result = []
        for a, b in zip(coords, coords[1:]):
            line = LineString([a, b])
            at = [(0, a)]
            for point in self.points:
                distance = line.project(Point(point))
                if self.tolerance < distance < line.length-self.tolerance and line.distance(Point(point)) <= self.tolerance:
                    at.append((distance, point))
            for _, point in sorted(at):
                if not result or point != result[-1]:
                    result.append(point)
        return result


def _check_vertex_links(plans):
    """Reject point pinches even when every edge has two incident faces."""
    links = defaultdict(lambda: defaultdict(list))
    for plan in plans:
        points = plan.points
        for i, vertex in enumerate(points):
            a, b = points[i-1], points[(i+1) % len(points)]
            links[vertex][a].append(b)
            links[vertex][b].append(a)
    for graph in links.values():
        if any(len(neighbours) != 2 for neighbours in graph.values()):
            raise ValueError("Aggregate has a non-manifold vertex neighbourhood.")
        reached, pending = set(), [next(iter(graph))]
        while pending:
            vertex = pending.pop()
            if vertex not in reached:
                reached.add(vertex)
                pending.extend(graph[vertex])
        if len(reached) != len(graph):
            raise ValueError("Aggregate source polygons meet at a non-manifold point pinch.")


def _plan_shell(preview, bottom_z, source_z, merge_z, top_z):
    validate_aggregation(preview)
    if any(p.interiors for p in preview.sources.values()):
        raise ValueError("Aggregate source footprints must have single-ring bottom faces.")
    nodes = _XYNodes(preview.tolerance)
    for geometry in [*preview.sources.values(), preview.union, preview.hull,
                     *[f.geometry for f in preview.growth_faces]]:
        nodes.add_geometry(geometry)
    maximum_time = max((_arrival(face, point) for face in preview.growth_faces
                        for polygon in polygon_parts(face.geometry)
                        for ring in [polygon.exterior, *polygon.interiors]
                        for point in ring.coords), default=0.0)
    heights = {}
    for point in nodes.points:
        if preview.union.distance(Point(point)) <= preview.tolerance:
            heights[point] = source_z
            continue
        times = [_arrival(face, point) for face in preview.growth_faces
                 if face.geometry.distance(Point(point)) <= preview.tolerance]
        if not times or maximum_time <= 0:
            raise ValueError("A growth vertex has no source arrival time.")
        values = [source_z + (merge_z-source_z)*time/maximum_time for time in times]
        if max(values)-min(values) > preview.tolerance:
            raise ValueError("Skeleton faces disagree on a shared vertex elevation.")
        heights[point] = min(merge_z, max(source_z, sum(values)/len(values)))
    plans = []
    for key, source in preview.sources.items():
        plans.append(_FacePlan([(*p, bottom_z) for p in nodes.ring(orient(source, -1).exterior)], "bottom_cap", key))
    for polygon in polygon_parts(preview.union):
        polygon = orient(polygon, 1)
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = nodes.ring(ring)
            for a, b in zip(coords, coords[1:]+coords[:1]):
                edge = LineString([a, b])
                owner = _boundary_owner(edge, preview)
                plans.append(_FacePlan([(*a, bottom_z), (*b, bottom_z), (*b, source_z), (*a, source_z)], "base_wall", owner))
    expected_volume = preview.union.area * (top_z-bottom_z)
    for face in preview.growth_faces:
        for polygon in polygon_parts(face.geometry):
            polygon = Polygon(nodes.ring(polygon.exterior), [nodes.ring(r) for r in polygon.interiors])
            for triangle in constrained_delaunay_triangles(polygon).geoms:
                coords = [nodes.canonical(p) for p in list(orient(triangle, -1).exterior.coords)[:-1]]
                xyz = [(*p, heights[p]) for p in coords]
                if max(p[2] for p in xyz)-min(p[2] for p in xyz) <= 1e-10:
                    raise ValueError("A growth triangle is horizontal.")
                plans.append(_FacePlan(xyz, "connecting", face.source_id))
                expected_volume += triangle.area * (top_z-sum(p[2] for p in xyz)/3)
    hull = nodes.ring(orient(preview.hull, 1).exterior)
    for a, b in zip(hull, hull[1:]+hull[:1]):
        midpoint = Point((a[0]+b[0])/2, (a[1]+b[1])/2)
        owner = next((key for key, partition in preview.partitions.items()
                      if partition.distance(midpoint) <= preview.tolerance), None)
        plans.append(_FacePlan([(*a, heights[a]), (*b, heights[b]), (*b, top_z), (*a, top_z)], "hull_wall", owner))
    plans.append(_FacePlan([(*p, top_z) for p in hull], "upper_cap", None))
    _check_shell(plans,context=dict(case='aggregation',cell='aggregate',bottom_z=bottom_z,source_z=source_z,
                                  merge_z=merge_z,target_z=top_z,stage='growth_boundary'))
    _check_vertex_links(plans)
    from shell_conformity import check_face_intersections
    check_face_intersections(plans)
    if abs(_planned_volume(plans)-expected_volume) > max(1e-6, expected_volume*1e-9):
        raise ValueError("Aggregate boundary volume disagrees with its integrated growth surface.")
    return plans, maximum_time, expected_volume


def _boundary_owner(edge, preview):
    owners = [key for key, source in preview.sources.items()
              if edge.difference(source.boundary.buffer(preview.tolerance)).length <= preview.tolerance]
    if len(owners) != 1:
        raise ValueError("Cannot assign an exposed aggregate wall to one source.")
    return owners[0]


def build_aggregation_model(preview, *, bottom_z=0.0, source_z=5.0, merge_z=9.0, top_z=10.0):
    """Preflight a single closed shell, then allocate and sew its DHE boundary."""
    from main import ExtrudedCell, ExtrusionModel
    if not all(isfinite(v) for v in (bottom_z, source_z, merge_z, top_z)) or not bottom_z < source_z < merge_z < top_z:
        raise ValueError("Aggregation requires finite z-down < height < merge-z < target-z.")
    plans, maximum_time, expected_volume = _plan_shell(preview, bottom_z, source_z, merge_z, top_z)
    saved = dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId
    try:
        dhe.reset_globals()
        xmin, ymin, xmax, ymax = preview.hull.bounds
        exterior = dhe.make_vertex(xmax+5, ymax+5, top_z+5, ch="VINF", clr=EXTERIOR_COLOR)
        exterior.logical_feature_id = "VINF"
        center = preview.hull.representative_point()
        dual = dhe.make_vertex(center.x, center.y, (merge_z+top_z)/2, ch="aggregate", clr=AGGREGATE_COLOR)
        vertices, edge_pairs, facets = {}, defaultdict(list), []
        for plan in plans:
            vs = []
            for point in plan.points:
                if point not in vertices:
                    vertices[point] = dhe.make_vertex(*point)
                vs.append(vertices[point])
            first = dhe.MEVVFS(vs[0], vs[1], dual, exterior)
            cursor = first
            for vertex in vs[2:]:
                cursor = dhe.MEV(vertex, cursor.S)
            seed = dhe.MEF(first, cursor.S).S
            facets.append(AggregationFacet(seed, plan.part, plan.source_id))
            for edge in face_edges(seed):
                edge_pairs[frozenset((edge.V, edge.S.V))].append(edge)
        for a, b in edge_pairs.values():
            dhe.Sew(a, b.S)
        cell = ExtrudedCell("aggregate", facets[0].seed, dual)
        cell.source_ids = tuple(preview.sources)
        model = ExtrusionModel([cell], exterior, preview.origin, [])
        colors = {key: tuple(int(SOURCE_COLORS[i % len(SOURCE_COLORS)][j:j+2], 16)/255
                             for j in (1, 3, 5)) + (1.0,) for i, key in enumerate(preview.sources)}
        face_sources = {tuple(sorted(f.xyz)): f.source_id for f in facets}
        model.aggregation = AggregationLayer(preview, cell, model, bottom_z, source_z, merge_z, top_z,
                                             maximum_time, facets, expected_volume, colors, face_sources)
        validate_aggregation_model(model)
        return model
    except Exception:
        dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId = saved
        raise


def validate_aggregation_model(model):
    from main import validate_model
    from plane_slicing import _face_frame
    summaries = validate_model(model)
    layer = model.aggregation
    if len(model.cells) != 1 or layer.cell is not model.cells[0] or model.shared_walls:
        raise ValueError("Aggregation must have one cell without internal shared walls.")
    plans = [_FacePlan(f.xyz, f.part, f.source_id) for f in layer.facets]
    _check_shell(plans)
    _check_vertex_links(plans)
    if len(dhe.get_UniqueFace_in_cell(layer.cell.seed)) != len(layer.facets):
        raise ValueError("Aggregation facet metadata does not match its live DHE faces.")
    if sum(f.part == "upper_cap" for f in layer.facets) != 1:
        raise ValueError("Aggregate must have exactly one polygon top face.")
    top = next(f for f in layer.facets if f.part == "upper_cap")
    if top.geometry.symmetric_difference(layer.preview.hull).area > layer.preview.area_tolerance:
        raise ValueError("Aggregate top face is not the convex hull.")
    bottoms = {f.source_id: f for f in layer.facets if f.part == "bottom_cap"}
    if set(bottoms) != set(layer.preview.sources):
        raise ValueError("Aggregate bottom faces do not preserve every source ID.")
    for key, source in layer.preview.sources.items():
        if bottoms[key].geometry.symmetric_difference(source).area > layer.preview.area_tolerance:
            raise ValueError(f"Aggregate bottom face changes source {key}.")
    for face in layer.facets:
        _face_frame(face.xyz)
        heights = [p[2] for p in face.xyz]
        if face.part == "bottom_cap" and any(z != layer.bottom_z for z in heights):
            raise ValueError("Aggregate bottom cap has the wrong elevation.")
        if face.part == "upper_cap" and any(z != layer.target_z for z in heights):
            raise ValueError("Aggregate top cap has the wrong elevation.")
        if face.part == "connecting" and (len(heights) != 3 or min(heights) < layer.source_z or max(heights) > layer.merge_z):
            raise ValueError("An aggregate growth facet is outside its elevation interval.")
        if max(heights)-min(heights) <= 1e-10 and face.part not in ("bottom_cap", "upper_cap"):
            raise ValueError("Aggregate contains an internal horizontal face.")
    volume = cell_volume(layer.cell)
    if volume <= 0 or abs(volume-layer.expected_volume) > max(1e-6, layer.expected_volume*1e-9):
        raise ValueError("Aggregate DHE volume does not match its planned volume.")
    return summaries
