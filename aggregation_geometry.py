"""Source-driven straight-skeleton partitions, clipped to a fixed convex hull.

No DHE objects are allocated here. A distant rectangular frame converts the
exterior wavefront of the source union into a polygon-with-holes skeleton.
Frame faces are discarded only after verifying they do not reach the hull.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Mapping

from shapely import union_all
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient


@dataclass(frozen=True)
class GrowthFace:
    source_id: str
    source_edge: LineString
    geometry: BaseGeometry


@dataclass(frozen=True)
class SkeletonArc:
    geometry: LineString
    source_ids: tuple[str, ...]
    times: tuple[float, float]


@dataclass(frozen=True)
class TargetLine:
    kind: str  # "meeting" or "hull"
    source_ids: tuple[str, ...]
    geometry: BaseGeometry


@dataclass
class AggregationPreview:
    sources: dict[str, Polygon]
    union: BaseGeometry
    hull: Polygon
    gap_regions: list[Polygon]
    growth_faces: list[GrowthFace]
    skeleton_arcs: list[SkeletonArc]
    target_lines: list[TargetLine]
    targets: dict[str, BaseGeometry]
    partitions: dict[str, BaseGeometry]
    origin: tuple[float, float]
    tolerance: float
    frame_margin: float = 0.0
    frame_stability_error: float = 0.0

    @property
    def gap(self) -> BaseGeometry:
        return union_all(self.gap_regions)

    @property
    def area_tolerance(self) -> float:
        return self.tolerance * max(self.hull.length, 1.0)


def polygon_parts(geometry):
    if isinstance(geometry, Polygon):
        if not geometry.is_empty:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def line_parts(geometry):
    if isinstance(geometry, LineString):
        if not geometry.is_empty:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from line_parts(part)


def _lines(geometry):
    return union_all(list(line_parts(geometry)))


def _ring_coordinates(ring, source_vertices, tolerance):
    """Keep source-ID changes even along collinear union boundary segments."""
    result = []
    for a, b in zip(ring.coords, list(ring.coords)[1:]):
        segment = LineString([a, b])
        points = [(0.0, a)]
        for point in source_vertices:
            p = Point(point)
            distance = segment.project(p)
            if tolerance < distance < segment.length - tolerance and segment.distance(p) <= tolerance:
                points.append((distance, point))
        for _, point in sorted(points):
            if not result or LineString([result[-1], point]).length > tolerance:
                result.append(tuple(point[:2]))
    return result


def _edge_owner(edge, sources, frame_boundary, tolerance):
    if edge.difference(frame_boundary.buffer(tolerance)).length <= tolerance:
        return None
    owners = [key for key, source in sources.items()
              if edge.difference(source.boundary.buffer(tolerance)).length <= tolerance]
    if len(owners) != 1:
        raise ValueError(f"Cannot uniquely assign exposed source edge {list(edge.coords)}: {owners}.")
    return owners[0]


def _skeleton_at_margin(sources, source_union, hull, margin, tolerance):
    try:
        from py_straight_skeleton import compute_skeleton
    except ImportError as exc:
        raise ValueError("Aggregation needs py_straight_skeleton==0.1.0; install requirements.txt.") from exc

    xmin, ymin, xmax, ymax = hull.bounds
    frame = box(xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    domain = frame.difference(source_union)
    gap = hull.difference(source_union)
    source_vertices = [point for source in sources.values()
                       for ring in [source.exterior, *source.interiors]
                       for point in list(ring.coords)[:-1]]
    growth_faces, arcs = [], []
    frame_area = 0.0
    for component in sorted(polygon_parts(domain), key=lambda p: p.bounds):
        component = orient(component, sign=1.0)
        exterior = _ring_coordinates(component.exterior, source_vertices, tolerance)
        holes = [_ring_coordinates(ring, source_vertices, tolerance)
                 for ring in component.interiors]
        skeleton = compute_skeleton(exterior=exterior, holes=holes)
        arc_owners = {}
        for face in skeleton.get_faces():
            coords = [(skeleton.nodes[i].position.x, skeleton.nodes[i].position.y) for i in face]
            source_edge = LineString(coords[:2])
            owner = _edge_owner(source_edge, sources, frame.boundary, tolerance)
            for a, b in zip(face, face[1:] + face[:1]):
                arc_owners.setdefault(tuple(sorted((a, b))), set()).add(owner)
            polygon = Polygon(coords)
            if not polygon.is_valid:
                raise ValueError("Straight skeleton produced an invalid face.")
            clipped = union_all(list(polygon_parts(polygon.intersection(gap))))
            if owner is None:
                frame_area += clipped.area
            elif not clipped.is_empty:
                growth_faces.append(GrowthFace(owner, source_edge, clipped))
        node_ids = {id(node): i for i, node in enumerate(skeleton.nodes)}
        for a, b in skeleton.arc_iterator():
            line = LineString([(a.position.x, a.position.y), (b.position.x, b.position.y)])
            if line.length <= tolerance:
                continue
            key = tuple(sorted((node_ids[id(a)], node_ids[id(b)])))
            owners = tuple(sorted(owner for owner in arc_owners.get(key, ()) if owner is not None))
            for clipped in line_parts(line.intersection(gap)):
                if clipped.length <= tolerance:
                    continue
                fractions = [line.project(Point(p)) / line.length
                             for p in (clipped.coords[0], clipped.coords[-1])]
                times = tuple(a.time + fraction * (b.time - a.time) for fraction in fractions)
                arcs.append(SkeletonArc(clipped, owners, times))
    area_tolerance = tolerance * max(hull.length, 1.0)
    if frame_area > area_tolerance:
        raise ValueError(f"Temporary frame influences the hull ({frame_area:g} square metres).")
    partitions = {key: union_all([source, *(face.geometry for face in growth_faces if face.source_id == key)])
                  for key, source in sources.items()}
    return growth_faces, arcs, partitions


def validate_aggregation(preview: AggregationPreview) -> dict[str, float]:
    """Validate source preservation, area coverage and exclusive ownership."""
    area_tolerance = preview.area_tolerance
    for key, source in preview.sources.items():
        partition = preview.partitions[key]
        if partition.is_empty or not partition.is_valid or not list(polygon_parts(partition)):
            raise ValueError(f"Source {key} has an invalid top partition.")
        if source.difference(partition).area > area_tolerance:
            raise ValueError(f"Top partition {key} loses original source area.")
    overlap = sum(a.intersection(b).area for a, b in combinations(preview.partitions.values(), 2))
    coverage = union_all(list(preview.partitions.values()))
    missing = preview.hull.difference(coverage).area
    excess = coverage.difference(preview.hull).area
    if max(overlap, missing, excess) > area_tolerance:
        raise ValueError(f"Invalid aggregation partition: overlap={overlap:g}, missing={missing:g}, excess={excess:g} m2.")
    return {"source_area": preview.union.area, "hull_area": preview.hull.area,
            "growth_area": preview.gap.area, "overlap_area": overlap,
            "missing_area": missing, "excess_area": excess}


def _targets(preview):
    targets = []
    for (a_id, a), (b_id, b) in combinations(preview.partitions.items(), 2):
        line = _lines(a.boundary.intersection(b.boundary).difference(preview.union))
        if not line.is_empty:
            targets.append(TargetLine("meeting", (a_id, b_id), line))
    for key, partition in preview.partitions.items():
        line = _lines(partition.boundary.intersection(preview.hull.boundary).difference(preview.union))
        if not line.is_empty:
            targets.append(TargetLine("hull", (key,), line))
    preview.target_lines = targets
    preview.targets = {key: union_all([line.geometry for line in targets if key in line.source_ids])
                       for key in preview.sources}


def build_aggregation_preview(
    sources: Mapping[str, Polygon], *, origin=(0.0, 0.0), tolerance=1e-6,
) -> AggregationPreview:
    """Return source-labeled top partitions without constructing DHE solids."""
    if not isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Aggregation tolerance must be finite and positive.")
    sources = {str(key): polygon for key, polygon in sources.items()}
    if not sources:
        raise ValueError("Select at least one source polygon for aggregation.")
    for key, polygon in sources.items():
        if not isinstance(polygon, Polygon) or polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError(f"Source {key} must be a valid positive-area polygon.")
    for a, b in combinations(sources.values(), 2):
        if a.intersection(b).area > tolerance ** 2:
            raise ValueError("Aggregation sources overlap in area.")
    source_union = union_all(list(sources.values()))
    hull = source_union.convex_hull
    regions = sorted(polygon_parts(hull.difference(source_union)), key=lambda p: (*p.bounds, p.area))
    preview = AggregationPreview(sources, source_union, hull, regions, [], [], [], {},
                                 dict(sources), tuple(origin), tolerance)
    if not regions:
        validate_aggregation(preview)
        _targets(preview)
        return preview
    span = max(hull.bounds[2] - hull.bounds[0], hull.bounds[3] - hull.bounds[1], 1.0)
    previous = None
    failure = "No stable skeleton partition found."
    for attempt in range(6):
        margin = span * 2 ** (attempt + 1)
        try:
            faces, arcs, partitions = _skeleton_at_margin(sources, source_union, hull, margin, tolerance)
            preview.growth_faces, preview.skeleton_arcs, preview.partitions = faces, arcs, partitions
            validate_aggregation(preview)
            if previous is not None:
                error = sum(partitions[key].symmetric_difference(previous[key]).area for key in sources)
                distance = max(partitions[key].boundary.hausdorff_distance(previous[key].boundary)
                               for key in sources)
                if error <= preview.area_tolerance and distance <= tolerance:
                    preview.frame_margin, preview.frame_stability_error = margin, error
                    _targets(preview)
                    return preview
            previous = partitions
        except (ValueError, RuntimeError, ArithmeticError, AssertionError, IndexError, KeyError) as exc:
            previous = None
            failure = str(exc) or type(exc).__name__
    raise ValueError(f"Cannot construct a frame-independent straight-skeleton aggregation: {failure}")
