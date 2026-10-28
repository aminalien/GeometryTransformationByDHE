"""Deterministic scientific stages backed by the production construction paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from matplotlib.colors import to_rgba
from shapely import union_all
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon

import DHECore as dhe
from DHECore import face_edges
from aggregation_geometry import SOURCE_COLORS, build_aggregation_preview
from aggregation_layer import _plan_shell, build_aggregation_model
from active_region_triangulation import classify_region, triangulate_active_regions
from top_face_overlay import RegionKind, face_polygon, overlay_top_faces, roof_faces
from transition_layer import _preflight, build_transition_layer, merge_transition_shells
from transition_targets import TargetDefinition, prepare_collapse, load_line_target


TRANSFORMATIONS = {
    "polygon": "Polygon → polygon", "point": "Polygon → point",
    "polyline": "Polygon → polyline", "aggregation": "Aggregation",
}
REGION_COLORS = {
    RegionKind.ACTIVE_SOURCE_ONLY: "#ed9229", RegionKind.ACTIVE_TARGET_ONLY: "#64b765",
    RegionKind.COMMON_NONACTIVE: "#98a8b9", RegionKind.BACKGROUND: "#dde1e5",
}


@dataclass(frozen=True)
class StageSpec:
    id: str
    title: str
    dhe: bool
    explanation: str


STAGES = {
    "polygon": (
        StageSpec("input", "Source and target", True, "Constructed base cells and the supplied target polygon; no roof subdivision."),
        StageSpec("overlay", "Roof overlay", True, "MVE inserts new crossings; MEV follows target arcs; MEF splits roof faces."),
        StageSpec("regions", "Active/inactive regions", True, "Classify S−T, T−S, common S∩T and background; cell ownership stays fixed."),
        StageSpec("triangulation", "Flat triangulation", True, "Constrained diagonals are realized with MEF in active regions only."),
        StageSpec("inclined", "Inclined facet plans", False, "Boundary-chain strips and opposite neighbor interfaces are planned; they are not sewn DHE faces."),
        StageSpec("final", "Final DHE", True, "Reconstruct surviving boundaries with MEVVFS/MEV/MEF; Sew and JoinByFace connect the final cells."),
    ),
    "point": (
        StageSpec("input", "Source and target", True, "Constructed base cells and an interior centroid target at the upper level."),
        StageSpec("triangulation", "Flat fan triangulation", False, "XY projection of the boundary-to-apex fan; this is geometric planning, not a DHE roof triangulation."),
        StageSpec("inclined", "Inclined interfaces and neighbor allocation", False, "Lift the fan to its apex; pair neighbor interfaces and continue ownership junctions."),
        StageSpec("shell", "Complete shell plan", False, "Validated surviving boundary plans, without internal source-level caps; DHE records are not allocated yet."),
        StageSpec("final", "Final DHE", True, "Closed source and neighbor cells; the source terminates at an apex contact without a zero-area cap."),
    ),
    "polyline": (
        StageSpec("input", "Source and target", True, "Constructed base cells and the complete supplied interior polyline."),
        StageSpec("extensions", "Endpoint extensions and boundary chains", False, "Extend endpoint tangents to the source boundary and identify two chains; coordinate preparation only."),
        StageSpec("triangulation", "Flat strip triangulation", False, "XY projection of two constrained chain strips to the polyline; no DHE subdivision is claimed."),
        StageSpec("inclined", "Inclined interfaces and neighbor allocation", False, "Lift both strips to the target ridge, with reciprocal neighboring facets and separator sheets."),
        StageSpec("shell", "Complete shell plan", False, "Closed oriented boundary plans omit the internal caps and the degenerate ridge cap."),
        StageSpec("final", "Final DHE", True, "Sewn source and neighbor cells; the exact target ridge is available as a terminal contact."),
    ),
    "aggregation": (
        StageSpec("input", "Source cells", True, "Six initial DHE cells; matching source walls establish the original neighbors."),
        StageSpec("gaps", "Union, hull, and gaps", False, "Compute U, H=conv(U), and H−U; no skeleton or new DHE topology yet."),
        StageSpec("skeleton", "Skeleton and source ownership", False, "Source-driven exterior skeleton clipped to the fixed hull; generating edges label growth faces."),
        StageSpec("partitions", "Colored top partitions", False, "Source-owned partitions cover the hull; these are provenance regions, not final DHE cells."),
        StageSpec("inclined", "Lifted growth faces", False, "Assign levels from source-edge arrival times and triangulate only inclined growth faces."),
        StageSpec("shell", "Complete aggregate shell plan", False, "One closed exterior shell includes polygon bottoms, lifted growth, hull walls and one top cap."),
        StageSpec("final", "Final single-cell DHE", True, "One aggregate dual vertex and one hull top face; source IDs remain as face provenance."),
    ),
}


@dataclass
class DisplayFacet:
    points: list
    owner: str
    part: str


@dataclass
class StageResult:
    transformation: str
    spec: StageSpec
    model: object | None
    sources: dict
    colors: dict
    target: object
    bounds: tuple
    metadata: dict
    facets: list[DisplayFacet] = field(default_factory=list)
    context: list[DisplayFacet] = field(default_factory=list)
    regions: list = field(default_factory=list)  # (geometry, color, label)
    lines: list = field(default_factory=list)  # (XYZ coordinates, color, label, style)
    face_colors: dict = field(default_factory=dict)

    @property
    def kind(self):
        return "DHE state" if self.model is not None else "Geometry preview"

    @property
    def final(self):
        return self.spec.id == "final"


def model_facets(model):
    return [DisplayFacet([(e.V.x, e.V.y, e.V.z) for e in face_edges(seed)], cell.feature_id, "boundary")
            for cell in model.cells for seed in dhe.get_UniqueFace_in_cell(cell.seed)]


def _display_plans(plans, parts=None, flat_z=None):
    return [DisplayFacet([(p[0], p[1], flat_z if flat_z is not None else p[2]) for p in plan.points], owner, plan.part)
            for owner, shell in plans.items() for plan in shell if parts is None or plan.part in parts]


def _outline(geometry, z):
    return [(x, y, z) for x, y, *_ in (geometry.exterior.coords if geometry.geom_type == "Polygon" else geometry.coords)]


def build_paper_stage(args, transformation="aggregation", step="final"):
    """Build a stage from scratch; geometry previews never claim a live DHE model."""
    import main as app
    if transformation not in STAGES:
        raise ValueError("Unknown transformation.")
    spec = next((s for s in STAGES[transformation] if s.id == step), None)
    if spec is None:
        raise ValueError(f"Unknown step {step!r} for {transformation}.")
    levels = (args.z_down, args.height, args.target_z)
    if not all(isfinite(z) for z in levels) or not args.z_down < args.height < args.target_z:
        raise ValueError("Paper stages require finite z-down < height < target-z.")
    if transformation == "aggregation" and (not isfinite(args.merge_z) or not args.height < args.merge_z < args.target_z):
        raise ValueError("Aggregation requires height < merge-z < target-z.")
    ids = (getattr(args, "aggregation_ids", None) if transformation == "aggregation" else getattr(args, "transition_ids", None))
    ids = ids or (app.AGGREGATION_IDS if transformation == "aggregation" else app.POLYGON_IDS)
    model = app.build_model(args.input_shp, ids, args.height, z_down=args.z_down)
    sources = {cell.feature_id: face_polygon(roof_faces(cell, args.height)[0]) for cell in model.cells}
    colors = {c.feature_id: c.dual_vertex.color for c in model.cells}
    if transformation == "aggregation":
        colors = {key: to_rgba(SOURCE_COLORS[i % len(SOURCE_COLORS)]) for i, key in enumerate(sources)}
        for cell in model.cells:
            cell.dual_vertex.color = colors[cell.feature_id]
        target = union_all(list(sources.values())).convex_hull
    elif transformation == "polygon":
        target = app.load_selected_polygons(args.input_shp, [args.target_id]).geometry.iloc[0]
        target = translate(target, xoff=-model.origin[0], yoff=-model.origin[1])
    elif transformation == "point":
        target = sources[str(args.source_id)].centroid
    else:
        target = load_line_target(args.line_shp, args.line_id, model.origin).geometry
    extent = union_all([*sources.values(), target]).bounds
    frame = (extent[0], extent[1], args.z_down, extent[2], extent[3], args.target_z)
    result = StageResult(transformation, spec, model if spec.dhe else None, sources, colors, target, frame,
                         dict(source_ids=list(sources), source_id=str(args.source_id) if transformation != "aggregation" else None,
                              target_id=str(args.target_id) if transformation == "polygon" else
                              str(args.line_id) if transformation == "polyline" else "centroid" if transformation == "point" else "convex_hull",
                              origin=list(model.origin), z_down=args.z_down, source_z=args.height,
                              merge_z=args.merge_z, target_z=args.target_z, input_shp=str(args.input_shp),
                              line_shp=str(args.line_shp) if transformation == "polyline" else None))
    result.lines = ([] if transformation == "aggregation" and step == "input" else
                    [(_outline(target, args.target_z), "#783fb0" if transformation == "aggregation" else "#bb0088",
                      "Convex hull" if transformation == "aggregation" else "Target", "-")])
    if step == "input":
        app.validate_model(model)
        return result
    result.context = model_facets(model)
    if transformation == "aggregation":
        union = union_all(list(sources.values()))
        if step == "gaps":
            result.regions = [(target.difference(union), "#dce3eb", "H − U")]
            return result
        preview = build_aggregation_preview(sources, origin=model.origin)
        if step in ("skeleton", "partitions"):
            result.regions = ([(f.geometry, colors[f.source_id], f.source_id) for f in preview.growth_faces]
                              if step == "skeleton" else [(g, colors[key], key) for key, g in preview.partitions.items()])
            for arc in preview.skeleton_arcs:
                result.lines.append((_outline(arc.geometry, args.height), "#486272", "Skeleton", "-"))
            for line in preview.target_lines:
                from aggregation_geometry import line_parts
                for part in line_parts(line.geometry):
                    result.lines.append((_outline(part, args.target_z), "#c52d3e" if line.kind == "meeting" else "#783fb0", line.kind.title()+" target", "-"))
            return result
        if step == "final":
            result.model = build_aggregation_model(preview, bottom_z=args.z_down, source_z=args.height,
                                                   merge_z=args.merge_z, top_z=args.target_z)
            result.context = []
            return result
        plans, _, _ = _plan_shell(preview, args.z_down, args.height, args.merge_z, args.target_z)
        result.facets = [DisplayFacet(p.points, p.source_id or "aggregate", p.part) for p in plans
                         if step == "shell" or p.part == "connecting"]
        if step == "shell":
            result.context = []
        return result
    definition = TargetDefinition(transformation, result.metadata["target_id"], target)
    if transformation == "polygon":
        overlay_top_faces(model, target, source_id=args.source_id, target_id=args.target_id, z=args.height)
        if step in ("regions", "triangulation", "inclined", "final"):
            for fragment in model.overlay.fragments:
                fragment.region_kind = classify_region(fragment.geometry, sources[str(args.source_id)], target)
                result.regions.append((fragment.geometry, REGION_COLORS[fragment.region_kind], fragment.region_kind.description))
                result.face_colors[tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(fragment.seed)))] = to_rgba(REGION_COLORS[fragment.region_kind])
        if step == "overlay":
            result.regions = [(f.geometry, colors[f.cell_id], f.cell_id) for f in model.overlay.fragments]
        if step in ("overlay", "regions"):
            app.validate_model(model)
            return result
        triangulate_active_regions(model)
        if step == "triangulation":
            result.regions = [(f.geometry, REGION_COLORS[f.region_kind], f.region_kind.description) for f in model.overlay.fragments]
            result.face_colors = {tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(f.seed))): to_rgba(REGION_COLORS[f.region_kind])
                                  for f in model.overlay.fragments}
            app.validate_model(model)
            return result
        if step == "final":
            build_transition_layer(model, target_z=args.target_z, target=definition)
            result.model, result.regions, result.face_colors, result.context = model, [], {}, []
            return result
        plans = _preflight(model, args.target_z)
    else:
        prepared = prepare_collapse(model, definition, str(args.source_id), args.height, args.target_z)
        plans = prepared.upper_plans
        if step == "extensions":
            ends = prepared.endpoint_extensions
            cut = LineString([ends[0], *list(target.coords), ends[1]])
            result.lines.append((_outline(cut, args.height), "#ee8b24", "Endpoint extensions", "--"))
            result.regions = [(p, "#94c9dc" if i == 0 else "#e5bd8e", f"Chain region {i+1}")
                              for i, p in enumerate(Polygon(chain+list(reversed(list(target.coords))))
                                                    for chain in prepared.boundary_chains)]
            return result
        if step == "final":
            build_transition_layer(model, target_z=args.target_z, target=definition,
                                   source_id=str(args.source_id), source_z=args.height)
            result.model, result.context = model, []
            return result
        if step == "shell":
            plans = merge_transition_shells(model, plans, args.height, prepared.base_faces)
            result.facets = _display_plans(plans)
            result.context = []
            return result
    result.facets = _display_plans(plans, {"connecting", "neighbour_transition", "separator"},
                                    args.height if step == "triangulation" else None)
    return result
