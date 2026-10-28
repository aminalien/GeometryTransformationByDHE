"""Classify overlay regions and triangulate their active parts in DHE topology."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from math import hypot
from typing import TYPE_CHECKING

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

import DHECore as dhe
from DHECore import face_edges
from top_face_overlay import (
    AREA_TOLERANCE, RegionKind, RoofFragment, face_polygon, roof_faces, validate_overlay,
)

if TYPE_CHECKING:
    from main import ExtrusionModel


class VertexRole(str, Enum):
    SOURCE = "source"
    TARGET = "target"
    BOTH = "both"
    NEITHER = "neither"


@dataclass
class TransitionRegion:
    region_id: str
    cell_id: str
    geometry: Polygon
    kind: RegionKind
    boundary_vertices: list[dhe.Vertex]
    fragments: list[RoofFragment]


@dataclass
class TriangulationResult:
    regions: list[TransitionRegion]
    inserted_diagonals: list[dhe.DualHalfEdge]
    vertex_roles: dict[dhe.Vertex, VertexRole]
    original_vertex_positions: dict[dhe.Vertex, tuple[float, float, float]]

    @property
    def triangles(self) -> list[RoofFragment]:
        return [fragment for region in self.regions if region.kind.active
                for fragment in region.fragments]


@dataclass
class _RegionPlan:
    original: RoofFragment
    region: TransitionRegion
    triangles: list[Polygon]
    diagonals: list[tuple[dhe.Vertex, dhe.Vertex]]


def classify_region(geometry: Polygon, source: Polygon, target: Polygon) -> RegionKind:
    point = geometry.representative_point()
    in_source, in_target = source.covers(point), target.covers(point)
    if in_source and not in_target:
        return RegionKind.ACTIVE_SOURCE_ONLY
    if in_target and not in_source:
        return RegionKind.ACTIVE_TARGET_ONLY
    if in_source and in_target:
        return RegionKind.COMMON_NONACTIVE
    return RegionKind.BACKGROUND


def _vertex_role(vertex, source, target, tolerance):
    point = Point(vertex.x, vertex.y)
    on_source = source.boundary.distance(point) <= tolerance
    on_target = target.boundary.distance(point) <= tolerance
    if on_source and on_target:
        return VertexRole.BOTH
    if on_source:
        return VertexRole.SOURCE
    if on_target:
        return VertexRole.TARGET
    return VertexRole.NEITHER


def _vertex_at(vertices, xy, tolerance):
    matches = [v for v in vertices if hypot(v.x - xy[0], v.y - xy[1]) <= tolerance]
    if len(matches) != 1:
        raise ValueError("Triangulation must reuse one existing vertex for every triangle corner.")
    return matches[0]


def _diagonal_sort_key(pair):
    return tuple(sorted((v.x, v.y, v.z) for v in pair))


def _preflight(model):
    overlay = model.overlay
    if overlay is None:
        raise ValueError("Construct the roof overlay before triangulating active regions.")
    if overlay.triangulation is not None:
        raise ValueError("The active regions have already been triangulated.")
    triangulate = getattr(shapely, "constrained_delaunay_triangles", None)
    if triangulate is None or shapely.geos_version < (3, 10, 0):
        raise ValueError("Constrained triangulation requires Shapely >= 2.1 and GEOS >= 3.10.")
    validate_overlay(model)
    plans = []
    roles = {}
    for original in overlay.fragments:
        geometry = original.geometry
        vertices = [edge.V for edge in face_edges(original.seed)]
        kind = classify_region(geometry, overlay.source_geometry, overlay.target_geometry)
        region = TransitionRegion(original.label, original.cell_id, geometry, kind, vertices, [])
        for vertex in vertices:
            roles[vertex] = _vertex_role(
                vertex, overlay.source_geometry, overlay.target_geometry, overlay.tolerance,
            )
        if not kind.active:
            plans.append(_RegionPlan(original, region, [], []))
            continue
        if not geometry.is_valid or geometry.interiors:
            raise ValueError("Active triangulation requires valid single-ring regions.")
        triangles = list(triangulate(geometry).geoms)
        if len(triangles) != len(vertices) - 2:
            raise ValueError("The constrained triangulation does not preserve the region vertices.")
        edge_uses = Counter()
        for triangle in triangles:
            if (not isinstance(triangle, Polygon) or triangle.interiors
                    or len(triangle.exterior.coords) != 4
                    or triangle.area <= overlay.tolerance ** 2
                    or triangle.difference(geometry).area > AREA_TOLERANCE):
                raise ValueError("Triangulation produced a degenerate triangle or left its region.")
            corners = [_vertex_at(vertices, xy, overlay.tolerance)
                       for xy in list(triangle.exterior.coords)[:-1]]
            for a, b in zip(corners, corners[1:] + corners[:1]):
                edge_uses[frozenset((a, b))] += 1
        combined = unary_union(triangles)
        if (combined.symmetric_difference(geometry).area > AREA_TOLERANCE
                or abs(sum(t.area for t in triangles) - combined.area) > AREA_TOLERANCE):
            raise ValueError("The constrained triangles overlap or do not cover their region.")
        boundary = {frozenset((a, b)) for a, b in zip(vertices, vertices[1:] + vertices[:1])}
        if any(edge_uses[edge] != 1 for edge in boundary):
            raise ValueError("Triangulation changed a constrained region boundary.")
        internal = [edge for edge in edge_uses if edge not in boundary]
        if any(edge_uses[edge] != 2 for edge in internal) or len(internal) != len(vertices) - 3:
            raise ValueError("Triangulation has inconsistent internal diagonals.")
        diagonals = sorted(
            (tuple(sorted(edge, key=lambda v: (v.x, v.y, v.z))) for edge in internal),
            key=_diagonal_sort_key,
        )
        plans.append(_RegionPlan(original, region, triangles, diagonals))
    return plans, roles


def triangulate_active_regions(model: ExtrusionModel) -> TriangulationResult:
    """Add MEF diagonals in active regions; preserve every vertex and elevation."""
    plans, roles = _preflight(model)
    overlay = model.overlay
    original_positions = {
        edge.V: (edge.V.x, edge.V.y, edge.V.z) for edge in model.active_edges()
    }
    cells = {cell.feature_id: cell for cell in model.cells}
    inserted = []
    fragments = []
    for plan in plans:
        original, region = plan.original, plan.region
        if not region.kind.active:
            original.region_kind = region.kind
            original.parent_region_id = region.region_id
            region.fragments = [original]
            fragments.append(original)
            continue
        cell = cells[region.cell_id]
        for a, b in plan.diagonals:
            line = LineString([(a.x, a.y), (b.x, b.y)])
            candidates = []
            for face in roof_faces(cell, overlay.height, overlay.tolerance):
                polygon = face_polygon(face)
                if not region.geometry.covers(polygon.representative_point()):
                    continue
                edges = face_edges(face)
                first = next((edge for edge in edges if edge.V is a), None)
                second = next((edge for edge in edges if edge.V is b), None)
                if (first is not None and second is not None
                        and polygon.buffer(overlay.tolerance).covers(line)):
                    candidates.append((first, second))
            if len(candidates) != 1:
                raise ValueError("A triangulation diagonal must split exactly one current DHE face.")
            inserted.append(dhe.MEF(*candidates[0]))

        expected = list(plan.triangles)
        for face in roof_faces(cell, overlay.height, overlay.tolerance):
            polygon = face_polygon(face)
            if not region.geometry.covers(polygon.representative_point()):
                continue
            if len(face_edges(face)) != 3:
                raise ValueError("An active DHE region contains a non-triangular child face.")
            match = next((tri for tri in expected
                          if tri.symmetric_difference(polygon).area <= AREA_TOLERANCE), None)
            if match is None:
                raise ValueError("A DHE triangle does not match the constrained triangulation.")
            expected.remove(match)
            fragment = RoofFragment(
                region.cell_id, face, polygon, original.inside_target,
                region.kind, region.region_id,
            )
            region.fragments.append(fragment)
            fragments.append(fragment)
        if expected:
            raise ValueError("The DHE mesh is missing constrained triangles.")

    result = TriangulationResult(
        [plan.region for plan in plans], inserted, roles, original_positions,
    )
    overlay.fragments = fragments
    overlay.triangulation = result
    validate_triangulation(model)
    return result


def validate_triangulation(model: ExtrusionModel) -> None:
    """Check the active triangle mesh against its original regions and vertices."""
    overlay = model.overlay
    if overlay is None or overlay.triangulation is None:
        raise ValueError("The model has no active-region triangulation.")
    result = overlay.triangulation
    validate_overlay(model)
    current_vertices = {edge.V for edge in model.active_edges(include_transition=False)}
    if current_vertices != set(result.original_vertex_positions):
        raise ValueError("Active-region triangulation added or removed a vertex.")
    for vertex, position in result.original_vertex_positions.items():
        if (vertex.x, vertex.y, vertex.z) != position:
            raise ValueError("Active-region triangulation moved a vertex.")
    collected = []
    expected_diagonals = 0
    for region in result.regions:
        polygons = [face_polygon(f.seed) for f in region.fragments]
        combined = unary_union(polygons)
        if (combined.symmetric_difference(region.geometry).area > AREA_TOLERANCE
                or abs(sum(p.area for p in polygons) - combined.area) > AREA_TOLERANCE):
            raise ValueError("The DHE child faces overlap or do not cover the original region.")
        for fragment in region.fragments:
            if (fragment.parent_region_id != region.region_id
                    or fragment.region_kind != region.kind or fragment.cell_id != region.cell_id):
                raise ValueError("A child face lost its original region classification or ownership.")
        if region.kind.active:
            if len(polygons) != len(region.boundary_vertices) - 2:
                raise ValueError("An active region has the wrong number of triangles.")
            if any(len(face_edges(f.seed)) != 3 or p.area <= overlay.tolerance ** 2
                   for f, p in zip(region.fragments, polygons)):
                raise ValueError("An active region contains a degenerate or non-triangular face.")
            expected_diagonals += len(region.boundary_vertices) - 3
        elif len(region.fragments) != 1:
            raise ValueError("A non-active or background region was subdivided.")
        collected.extend(region.fragments)
    if {id(f) for f in collected} != {id(f) for f in overlay.fragments}:
        raise ValueError("Overlay fragment metadata does not match the transition regions.")
    if len(result.inserted_diagonals) != expected_diagonals:
        raise ValueError("The active triangle mesh has the wrong number of inserted diagonals.")
    for cell in model.cells:
        edges = dhe.Cell(cell.seed)
        for edge in edges:
            if (edge.S.S is not edge or edge.D.D is not edge
                    or edge.NF.V is not edge.S.V or edge.D.V is not cell.dual_vertex):
                raise ValueError("Triangulation broke a DHE pointer or cell ownership.")
        vertices = {edge.V for edge in edges}
        undirected = {frozenset((edge.V, edge.S.V)) for edge in edges}
        if len(vertices) - len(undirected) + len(dhe.get_UniqueFace_in_cell(cell.seed)) != 2:
            raise ValueError("Triangulation broke a closed cell's Euler characteristic.")
