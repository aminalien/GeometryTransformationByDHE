"""Insert a target polygon boundary into an existing DHE roof layer.

All geometry uses the model's local XY coordinates and metre units. Cell
ownership is retained: target membership is face metadata for the next step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import hypot, isfinite
from typing import TYPE_CHECKING

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

import DHECore as dhe
from DHECore import face_edges

if TYPE_CHECKING:
    from main import ExtrudedCell, ExtrusionModel
    from active_region_triangulation import TriangulationResult

AREA_TOLERANCE = 1e-6


class RegionKind(str, Enum):
    ACTIVE_SOURCE_ONLY = "active_source_only"
    ACTIVE_TARGET_ONLY = "active_target_only"
    COMMON_NONACTIVE = "common_nonactive"
    BACKGROUND = "background"

    @property
    def active(self) -> bool:
        return self in (RegionKind.ACTIVE_SOURCE_ONLY, RegionKind.ACTIVE_TARGET_ONLY)

    @property
    def description(self) -> str:
        return {
            RegionKind.ACTIVE_SOURCE_ONLY: "active S - T",
            RegionKind.ACTIVE_TARGET_ONLY: "active T - S",
            RegionKind.COMMON_NONACTIVE: "non-active S intersection T",
            RegionKind.BACKGROUND: "background",
        }[self]


@dataclass
class RoofFragment:
    cell_id: str
    seed: dhe.DualHalfEdge
    geometry: Polygon
    inside_target: bool
    region_kind: RegionKind | None = None
    parent_region_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.cell_id}/{self.seed.info}"


@dataclass
class TopFaceOverlay:
    source_id: str
    target_id: str
    height: float
    tolerance: float
    original_roofs: dict[str, Polygon]
    target_geometry: Polygon
    crossing_vertices: list[dhe.Vertex]
    fragments: list[RoofFragment]
    original_volumes: dict[str, float]
    triangulation: TriangulationResult | None = None

    @property
    def source_geometry(self) -> Polygon:
        return self.original_roofs[self.source_id]


@dataclass
class _BoundaryPath:
    cell_id: str
    coords: list[tuple[float, float]]


def roof_faces(cell: ExtrudedCell, z: float, tolerance: float = 1e-6) -> list:
    return [
        face for face in dhe.get_UniqueFace_in_cell(cell.seed)
        if all(abs(edge.V.z - z) <= tolerance for edge in face_edges(face))
    ]


def face_polygon(seed) -> Polygon:
    return Polygon([(edge.V.x, edge.V.y) for edge in face_edges(seed)])


def cell_volume(cell: ExtrudedCell) -> float:
    """Volume from oriented triangles spanning each DHE boundary face."""
    volume = 0.0
    for face in dhe.get_UniqueFace_in_cell(cell.seed):
        vertices = [edge.V for edge in face_edges(face)]
        a = vertices[0]
        for b, c in zip(vertices[1:-1], vertices[2:]):
            volume += (
                a.x * (b.y * c.z - b.z * c.y)
                - a.y * (b.x * c.z - b.z * c.x)
                + a.z * (b.x * c.y - b.y * c.x)
            ) / 6
    return abs(volume)


def _near(a, b, tolerance) -> bool:
    return hypot(a[0] - b[0], a[1] - b[1]) <= tolerance


def _preflight(model, target, source_id, target_id, z, tolerance):
    if model.overlay is not None:
        raise ValueError("This model already has a roof overlay; rebuild it before applying another.")
    if source_id not in {cell.feature_id for cell in model.cells}:
        raise ValueError(f"Source cell {source_id} is not among the constructed cells.")
    if target_id in {cell.feature_id for cell in model.cells}:
        raise ValueError("The overlay target must be loaded separately from the extruded cells.")
    if not isfinite(z) or not isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Roof height must be finite and tolerance must be positive and finite.")
    if (not isinstance(target, Polygon) or target.is_empty or not target.is_valid
            or target.interiors or target.area <= AREA_TOLERANCE):
        raise ValueError("The target must be a valid nonempty single-ring Polygon.")
    target = Polygon([(float(x), float(y)) for x, y, *_ in target.exterior.coords])
    if any(not isfinite(v) for xy in target.exterior.coords for v in xy):
        raise ValueError("The target has non-finite coordinates.")
    roofs = {}
    for cell in model.cells:
        faces = roof_faces(cell, z, tolerance)
        if len(faces) != 1:
            raise ValueError(f"Cell {cell.feature_id} must have one unsplit roof at z={z:g}.")
        polygon = face_polygon(faces[0])
        if not polygon.is_valid or polygon.interiors:
            raise ValueError("Roof overlay requires valid single-ring roof faces.")
        roofs[cell.feature_id] = polygon
    roof_union = unary_union(list(roofs.values()))
    if target.difference(roof_union).area > AREA_TOLERANCE:
        raise ValueError("The target extends outside the selected cells.")
    if roofs[source_id].intersection(target).area <= AREA_TOLERANCE:
        raise ValueError("The target must overlap the source roof.")

    # Node all full boundaries together; independently clipping boundary pieces
    # can produce slightly different endpoints and leave polygonize with gaps.
    boundary = unary_union([p.boundary for p in roofs.values()])
    intersection = target.boundary.intersection(boundary)
    if intersection.is_empty:
        raise ValueError("An interior target loop is not supported in this overlay step.")
    if intersection.geom_type == "Point":
        crossings = [intersection]
    elif intersection.geom_type == "MultiPoint":
        crossings = list(intersection.geoms)
    else:
        raise ValueError("Coincident boundary segments are not supported in this overlay step.")
    distinct = []
    for point in sorted(crossings, key=target.exterior.project):
        if not any(point.distance(other) <= tolerance for other in distinct):
            distinct.append(point)
    crossings = distinct
    if len(crossings) < 2:
        raise ValueError("The target must cross roof boundaries at two or more distinct points.")

    # Keep each crossing once, including a target corner that is also a crossing.
    nodes = [(target.exterior.project(p), (p.x, p.y), True) for p in crossings]
    for x, y in list(target.exterior.coords)[:-1]:
        if not any(_near((x, y), xy, tolerance) for _, xy, _ in nodes):
            nodes.append((target.exterior.project(Point(x, y)), (x, y), False))
    nodes.sort(key=lambda node: node[0])
    start = next(i for i, node in enumerate(nodes) if node[2])
    nodes = nodes[start:] + nodes[:start]
    nodes.append(nodes[0])
    paths = []
    coords = [nodes[0][1]]
    for _, xy, is_crossing in nodes[1:]:
        coords.append(xy)
        if not is_crossing:
            continue
        path = LineString(coords)
        midpoint = path.interpolate(0.5, normalized=True)
        owners = [cell_id for cell_id, roof in roofs.items()
                  if roof.covers(midpoint) and roof.buffer(tolerance).covers(path)]
        if len(owners) != 1:
            raise ValueError("Every target boundary arc must lie within one roof face.")
        paths.append(_BoundaryPath(owners[0], coords))
        coords = [xy]

    network = unary_union([p.boundary for p in roofs.values()] + [target.boundary])
    expected = {cell_id: [] for cell_id in roofs}
    for piece in polygonize(network):
        owners = [cell_id for cell_id, roof in roofs.items()
                  if roof.covers(piece.representative_point())]
        if len(owners) != 1 or piece.interiors:
            raise ValueError("The target arrangement must produce simple, uniquely owned roof fragments.")
        expected[owners[0]].append(piece)
    for cell_id, roof in roofs.items():
        pieces = expected[cell_id]
        if unary_union(pieces).symmetric_difference(roof).area > AREA_TOLERANCE:
            raise ValueError("The precomputed roof arrangement does not conserve the original roof.")
        # A simple boundary-to-boundary arc must increase the face count by one.
        if len(pieces) != 1 + sum(path.cell_id == cell_id for path in paths):
            raise ValueError("This target requires an unsupported roof subdivision pattern.")

    # Locate all edge insertions before editing any DHE pointers.
    for point in crossings:
        existing = any(
            abs(edge.V.z - z) <= tolerance
            and _near((edge.V.x, edge.V.y), (point.x, point.y), tolerance)
            for edge in model.active_edges()
        )
        if not existing and _crossing_edge(model, point, z, tolerance) is None:
            raise ValueError("A target crossing could not be located on a roof edge.")
    return target, roofs, crossings, paths, expected


def _crossing_edge(model, point, z, tolerance):
    for edge in model.active_edges():
        if abs(edge.V.z - z) > tolerance or abs(edge.S.V.z - z) > tolerance:
            continue
        endpoints = [(edge.V.x, edge.V.y), (edge.S.V.x, edge.S.V.y)]
        if any(_near(xy, (point.x, point.y), tolerance) for xy in endpoints):
            continue
        if LineString(endpoints).distance(point) <= tolerance:
            return edge
    return None


def overlay_top_faces(
    model: ExtrusionModel,
    target_geometry: Polygon,
    *,
    source_id,
    target_id,
    z: float,
    tolerance: float = 1e-6,
) -> TopFaceOverlay:
    """Subdivide roofs in place and attach the classified result to model.overlay.

    The target is already projected and translated to model-local XY. Supports
    simple boundary-to-boundary arcs; rejects interior loops and coincident edges
    during preflight. Geometry and dual-cell ownership are preserved.
    """
    source_id, target_id = str(source_id), str(target_id)
    target, roofs, crossings, paths, expected = _preflight(
        model, target_geometry, source_id, target_id, z, tolerance,
    )
    original_volumes = {cell.feature_id: cell_volume(cell) for cell in model.cells}
    vertices = []
    for point in crossings:
        vertex = next((
            edge.V for edge in model.active_edges()
            if abs(edge.V.z - z) <= tolerance
            and _near((edge.V.x, edge.V.y), (point.x, point.y), tolerance)
        ), None)
        if vertex is None:
            vertex = dhe.make_vertex(point.x, point.y, z)
            # MVE visits the whole edge ring, including the adjacent roof/wall.
            dhe.MVE(vertex, _crossing_edge(model, point, z, tolerance))
        vertices.append(vertex)

    cells = {cell.feature_id: cell for cell in model.cells}
    for path in paths:
        line = LineString(path.coords)
        candidates = [
            face for face in roof_faces(cells[path.cell_id], z, tolerance)
            if face_polygon(face).buffer(tolerance).covers(line)
        ]
        if len(candidates) != 1:
            raise ValueError("A target arc could not be assigned to one current DHE roof face.")
        edges = face_edges(candidates[0])
        cursor = next(edge for edge in edges
                      if _near((edge.V.x, edge.V.y), path.coords[0], tolerance))
        end = next(edge for edge in edges
                   if _near((edge.V.x, edge.V.y), path.coords[-1], tolerance))
        for x, y in path.coords[1:-1]:
            new_edge = dhe.MEV(dhe.make_vertex(x, y, z), cursor)
            cursor = new_edge.S
        dhe.MEF(cursor, end)

    fragments = []
    for cell in model.cells:
        remaining = list(expected[cell.feature_id])
        for face in roof_faces(cell, z, tolerance):
            geometry = face_polygon(face)
            match = next((p for p in remaining
                          if p.symmetric_difference(geometry).area <= AREA_TOLERANCE), None)
            if match is None:
                raise ValueError("A DHE roof fragment differs from the precomputed arrangement.")
            remaining.remove(match)
            fragments.append(RoofFragment(
                cell.feature_id, face, geometry,
                target.covers(geometry.representative_point()),
            ))
        if remaining:
            raise ValueError("The DHE subdivision is missing an expected roof fragment.")
    result = TopFaceOverlay(
        source_id, target_id, z, tolerance, roofs, target, vertices, fragments,
        original_volumes,
    )
    model.overlay = result
    validate_overlay(model)
    return result


def validate_overlay(model: ExtrusionModel) -> None:
    """Verify actual DHE roof geometry, area, volume, and target-edge coverage."""
    overlay = model.overlay
    if overlay is None:
        raise ValueError("The model has no roof overlay.")
    target = overlay.target_geometry
    target_pieces = []
    roof_edges = []
    for cell in model.cells:
        fragments = [f for f in overlay.fragments if f.cell_id == cell.feature_id]
        current_faces = roof_faces(cell, overlay.height, overlay.tolerance)
        if {frozenset(face_edges(f.seed)) for f in fragments} != {
            frozenset(face_edges(seed)) for seed in current_faces
        }:
            raise ValueError("Overlay metadata does not describe the current DHE roof faces.")
        polygons = []
        for fragment in fragments:
            geometry = face_polygon(fragment.seed)
            if not geometry.is_valid or geometry.interiors:
                raise ValueError("Overlay produced an invalid DHE roof fragment.")
            if geometry.symmetric_difference(fragment.geometry).area > AREA_TOLERANCE:
                raise ValueError("A DHE roof fragment changed after overlay classification.")
            area_on_wrong_side = (geometry.difference(target).area if fragment.inside_target
                                  else geometry.intersection(target).area)
            if area_on_wrong_side > AREA_TOLERANCE:
                raise ValueError("A roof fragment crosses the target boundary.")
            if fragment.inside_target:
                target_pieces.append(geometry)
            polygons.append(geometry)
            roof_edges.append(geometry.boundary)
        original = overlay.original_roofs[cell.feature_id]
        combined = unary_union(polygons)
        if (combined.symmetric_difference(original).area > AREA_TOLERANCE
                or abs(sum(p.area for p in polygons) - combined.area) > AREA_TOLERANCE):
            raise ValueError("Roof fragments overlap or do not conserve the original roof.")
        before = overlay.original_volumes[cell.feature_id]
        if abs(cell_volume(cell) - before) > max(1e-6, before * 1e-9):
            raise ValueError("Roof subdivision changed a cell volume.")
    if unary_union(target_pieces).symmetric_difference(target).area > AREA_TOLERANCE:
        raise ValueError("The target is not covered exactly by the classified roof fragments.")
    if target.boundary.difference(unary_union(roof_edges).buffer(overlay.tolerance)).length > overlay.tolerance:
        raise ValueError("The DHE roof edges do not cover the complete target boundary.")
