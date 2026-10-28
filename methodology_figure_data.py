"""Read-only publication adapters for the existing DHE construction workflows.

The only retained state is immutable geometry, coordinates and public labels.
No renderer receives a live DHE record. Private planning functions are used as
implemented, rather than reproducing their geometric algorithms here.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import sys

from shapely import union_all
from shapely.geometry import LineString, Point, Polygon, box

import DHECore as dhe
import main as app
from active_region_triangulation import classify_region, triangulate_active_regions
from aggregation_geometry import build_aggregation_preview
from aggregation_layer import _plan_shell, build_aggregation_model, validate_aggregation_model
from plane_slicing import PlaneSlicer, SlicePlane
from top_face_overlay import RegionKind, face_polygon, overlay_top_faces, roof_faces
from transition_layer import _preflight, build_transition_layer, validate_transition_layer
from transition_targets import TargetDefinition, prepare_collapse

STAGES = (
    "Source and Target Selection",
    "Conforming Geometric and Topological Preparation",
    "Case-Specific Transition-Facet Construction and Level Assignment",
    "DHE Boundary Construction and Topological Integration",
    "Validation and Final Representation Extraction",
)
CASES = {"p2p": "P2P — Polygon to polygon", "p2pt": "P2Pt — Polygon to point",
         "p2l": "P2L — Polygon to polyline", "agg": "AGG — Aggregation"}
BLUE, MAGENTA, GREEN, PURPLE = "#2563eb", "#c026d3", "#15803d", "#7e22ce"
ORANGE, CYAN, GRAY, LIGHT = "#f59e0b", "#06b6d4", "#94a3b8", "#e5e7eb"
COLORS = {"Source S": BLUE, "N1": "#9eafc3", "N2": "#bdc9d6",
          "N3": "#8ea7b5", "N4": "#c1c3cb", "Source A": "#789db8",
          "Source B": "#c8a078", "Source C": "#86aa98", "Aggregate A": BLUE,
          "Exterior": PURPLE}
CLASS_STYLE = {
    RegionKind.ACTIVE_SOURCE_ONLY: (ORANGE, "Source-only: S − T (active)", "//"),
    RegionKind.ACTIVE_TARGET_ONLY: (CYAN, "Target-only: T − S (active)", "\\\\"),
    RegionKind.COMMON_NONACTIVE: (GRAY, "Common: S ∩ T (retained)", ".."),
    RegionKind.BACKGROUND: (LIGHT, "Unaffected/background", ""),
}
OVERLAY_NOTE = ("Active source-only and target-only regions are prepared for transition-surface "
                "construction; common and unaffected regions are retained.")


@dataclass(frozen=True)
class Shape:
    geometry: object
    color: str
    label: str
    group: str = "region"
    fill: bool = True
    style: str = "-"
    hatch: str = ""
    z: float = 1.


@dataclass(frozen=True)
class Line:
    points: tuple
    color: str
    label: str
    group: str = "primal"
    style: str = "-"


@dataclass(frozen=True)
class Facet:
    points: tuple
    owner: str
    part: str
    shared: bool = False
    provenance: str = ""


@dataclass(frozen=True)
class Panel:
    key: str
    title: str
    routine: str
    note: str
    shapes: tuple = ()
    facets: tuple = ()
    lines: tuple = ()
    default_3d: bool = False
    dimensions: tuple = ("2D",)
    topology: bool = False
    checks: tuple = ()
    status: str = "actual"


@dataclass(frozen=True)
class Stage:
    case: str
    number: int
    panels: tuple
    annotation: str = ""
    status: str = "actual"
    reason: str = "Computed through the production workflow; immutable snapshots captured."

    @property
    def title(self):
        return STAGES[self.number - 1]


@dataclass(frozen=True)
class Event:
    operator: str
    before: tuple
    after: tuple
    facet: tuple = ()
    owner: str = ""
    verified: bool = False


@dataclass(frozen=True)
class CaseData:
    case: str
    stages: tuple
    sources: tuple
    target: object
    bounds: tuple
    operations: tuple
    events: tuple
    intermediate: tuple
    terminal: tuple
    dual_pairs: tuple
    vertices: tuple
    edges: tuple
    cell_count: int


def xyz(v):
    return tuple(float(getattr(v, c)) for c in ("x", "y", "z"))


def points(seed):
    return tuple(xyz(e.V) for e in dhe.face_edges(seed))


@contextmanager
def isolated_core():
    """Construction needs core globals; never leave the caller's core replaced."""
    saved = dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId
    try:
        dhe.reset_globals()
        yield
    finally:
        dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId = saved


class OperatorTrace:
    """Observe actual calls, including entry arguments that JoinByFace rebinds.

    Only direct calls from the preparation/realization modules are counted;
    Sew calls internal to JoinByFace are deliberately excluded from this count.
    Tracing records coordinates immediately, before retirement can occur.
    """
    def __init__(self, labels, phase):
        self.labels, self.phase = labels, phase
        self.counts, self.events, self.pending = Counter(), [], {}
        self.selected = None
        self.error = None

    def __enter__(self):
        self.previous = sys.getprofile()
        sys.setprofile(self.profile)
        return self

    def __exit__(self, *args):
        sys.setprofile(self.previous)
        self.pending.clear()
        if self.error and not args[0]:
            raise RuntimeError("Operator snapshot capture failed") from self.error

    def profile(self, frame, event, result):
        try:
            self._profile(frame, event, result)
        except Exception as exc:
            self.error = exc

    def _profile(self, frame, event, result):
        names = {"MEVVFS", "MEV", "MVE", "MEF", "Sew", "JoinByFace"}
        name = frame.f_code.co_name
        if name not in names or frame.f_globals.get("__name__") != "DHECore":
            return
        caller = frame.f_back
        if caller.f_globals.get("__name__") not in {
            "top_face_overlay", "active_region_triangulation", "transition_layer", "aggregation_layer"
        }:
            return
        if event == "call":
            self.counts[name] += 1
            loc, outer = frame.f_locals, caller.f_locals
            plan = outer.get("plan")
            selected = False
            owner = self.labels.get(str(outer.get("owner", "aggregate")), "Aggregate A")
            if plan is not None and getattr(plan, "part", None) == "connecting":
                key = (owner, tuple(tuple(p) for p in plan.points))
                if self.selected is None:
                    self.selected = key
                selected = key == self.selected
            before, after, refs = (), (), ()
            if name == "MEVVFS":
                after = (xyz(loc["P1"]), xyz(loc["P2"]))
            elif name in ("MEV", "MVE"):
                e = loc["e"]
                before = (xyz(e.V), xyz(e.S.V))
                refs = (e, loc["P"])
                after = ((xyz(e.V), xyz(loc["P"])) if name == "MEV"
                         else (before[0], xyz(loc["P"]), before[1]))
            elif name in ("MEF", "JoinByFace"):
                a, b = loc["e1"], loc["e2"]
                before = (xyz(a.V), xyz(b.V))
                refs = (a, b)
                after = before
            elif name == "Sew":
                a, b = loc["e1"], loc["e2Sym"].S
                before, after, refs = (xyz(a.V), xyz(a.S.V)), (xyz(b.V), xyz(b.S.V)), (a, b)
            self.pending[id(frame)] = (before, after, refs, selected, owner)
        elif event == "return":
            stored = self.pending.pop(id(frame), None)
            if stored is None:
                return
            before, after, refs, selected, owner = stored
            facet, verified = (), result is not None
            if name == "JoinByFace":
                a, b = refs
                verified = a.Adjacent().D.V is b.D.V and b.Adjacent().D.V is a.D.V
                facet = points(a)
            elif name == "Sew":
                a, b = refs
                verified = (before == tuple(reversed(after)) and a.S.S is a and b.S.S is b
                            and a.D.D is a and b.D.D is b)
            elif name == "MEF":
                facet = points(result.S)
                after = (xyz(result.V), xyz(result.S.V))
                verified = len(facet) >= 3 and after == before
            elif name in ("MEV", "MEVVFS", "MVE"):
                verified = result.S.S is result and result.D.D is result
                if name == "MVE":
                    original, vertex = refs
                    actual = (xyz(original.V),xyz(original.S.V),xyz(result.S.V))
                    verified = verified and actual == after and result.V is vertex
                    after = actual
                else:
                    actual = (xyz(result.V),xyz(result.S.V))
                    verified = verified and actual == after
                    after = actual
            if not verified:
                raise ValueError("An observed operator effect failed its incidence check")
            # All calls count; retain only representative effects, not thousands of references.
            keep = ((self.phase == "prepare" and name in {"MVE", "MEV", "MEF"})
                    or (selected and name in {"MEVVFS", "MEV", "MEF"})
                    or name in {"Sew", "JoinByFace"})
            if keep and (name in {"MEVVFS", "MEV", "MEF"} and selected
                         or not any(e.operator == name for e in self.events)):
                self.events.append(Event(name, before, after, facet, owner, verified))


def _base(sources):
    exterior = dhe.make_vertex(12, 12, 1, ch="VINF", clr=(.55, .2, .75, 1))
    cells = [app.extrude_polygon(p, key, 1., exterior, app.CELL_COLORS[i % len(app.CELL_COLORS)], z_down=0.)
             for i, (key, p) in enumerate(sources.items())]
    model = app.ExtrusionModel(cells, exterior, (0., 0.), app.sew_shared_walls(cells))
    app.validate_model(model)
    return model


def _model_facets(model, labels):
    layer = model.aggregation or model.transition
    metadata = {(tuple(sorted(f.xyz)), labels.get(str(getattr(f, "cell_id", "aggregate")), "Aggregate A")):
                f.part for f in layer.facets} if layer else {}
    provenance = {tuple(sorted(f.xyz)): labels.get(f.source_id, "Aggregate A")
                  for f in model.aggregation.facets} if model.aggregation else {}
    return tuple(Facet(points(seed), labels[cell.feature_id],
                       metadata.get((tuple(sorted(points(seed))), labels[cell.feature_id]), "base"),
                       seed.Adjacent().D.V is not model.exterior,
                       provenance.get(tuple(sorted(points(seed))), ""))
                 for cell in model.cells for seed in dhe.get_UniqueFace_in_cell(cell.seed))


def _plans(plans, labels):
    return tuple(Facet(tuple(tuple(float(x) for x in p) for p in f.points), labels[owner], f.part,
                       f.part in {"connecting", "neighbour_transition", "separator"})
                 for owner, shell in plans.items() for f in shell)


def _outlines(sources, target, case):
    shapes = [Shape(p, COLORS[label], label, "source", False, z=1.) for label, p in sources]
    target_label = {"p2p": "Target T", "p2pt": "$P_t$", "p2l": "$L_t$", "agg": "Target H"}[case]
    shapes.append(Shape(target, MAGENTA, target_label, "target", False, "--", z=2.))
    return tuple(shapes)


def _dual_panel(model, labels):
    """Cell associations come from live DHE traversal, not geometric proximity."""
    lookup = {c.dual_vertex: labels[c.feature_id] for c in model.cells}
    lookup[model.exterior] = "Exterior"
    vertices = {label: xyz(v) for v, label in lookup.items()}
    pairs = set()
    for cell in model.cells:
        for seed in dhe.get_UniqueFace_in_cell(cell.seed):
            for e in dhe.face_edges(seed):
                other = e.Adjacent()
                a, b = lookup[e.D.V], lookup[other.D.V]
                if other.Adjacent().D.V is not e.D.V:
                    raise ValueError("A dual association is not reciprocal")
                if a != b:
                    pairs.add(tuple(sorted((a, b))))
    lines = tuple(Line((vertices[a], vertices[b]), PURPLE, f"{a} ↔ {b}", "dual", "--")
                  for a, b in sorted(pairs))
    shapes = tuple(Shape(Point(p[:2]), PURPLE, "d_" + label.replace("Source ", "").replace("Aggregate A", "A"),
                         "dual", z=p[2]) for label, p in vertices.items())
    panel = Panel("dual_associations", "Verified dual cell associations", "DHECore.Adjacent; e.D.V",
                  "Actual dual-vertex coordinates and checked cell associations; not a Voronoi construction.",
                  shapes=shapes, lines=lines, checks=("Reciprocal incident-cell association checked",))
    return panel, tuple(sorted(pairs))


def _construction_panels(events, final, model, labels):
    seq = [e for e in events if e.operator in {"MEVVFS", "MEV", "MEF"}]
    panels = []
    descriptions = {"MEVVFS": "Initialize the complex edge with supplied primal and cell/exterior dual vertices.",
                    "MEV": "Attach the supplied vertex to the current boundary chain; this is not an edge split.",
                    "MEF": "Connect the existing endpoints and close this planned face; the full cell is not sewn yet."}
    for name in ("MEVVFS", "MEV", "MEF"):
        matching = [e for e in seq if e.operator == name]
        if not matching:
            raise ValueError(f"No actual {name} snapshot captured for the selected transition face")
        e = matching[-1]
        lines = tuple(Line(x.after, GREEN, name, "construction") for x in seq[:seq.index(e)+1])
        facets = (Facet(e.facet, e.owner, "constructed_face"),) if name == "MEF" else ()
        panels.append(Panel(name.lower(), name + " — representative transition face", "DHECore." + name,
                            descriptions[name], facets=facets, lines=lines, default_3d=True,
                            dimensions=("2D", "3D"), topology=True,
                            checks=("Observed operator return and local incidence checked",)))
    sew = next(e for e in events if e.operator == "Sew")
    seams = [Line(sew.before, GREEN, "Sew: matching within-cell uses", "construction")]
    for a, b in model.shared_walls:
        ring = points(a)
        seams.append(Line(ring + (ring[0],), PURPLE, "JoinByFace: shared interface", "construction", "--"))
    note = ("Sew connects compatible edge uses within one aggregate cell. No JoinByFace or dual contraction."
            if model.aggregation else "Sew connects within-cell uses; JoinByFace connects matching faces while retaining distinct cell duals.")
    panels.append(Panel("seams_interfaces", "Sewn boundaries and cell interfaces", "DHECore.Sew" +
                        ("; DHECore.JoinByFace" if model.shared_walls else ""), note,
                        facets=final, lines=tuple(seams), default_3d=True, dimensions=("2D", "3D"),
                        topology=True, checks=("Final cycles/pointers and interface reciprocity checked",)))
    dual, pairs = _dual_panel(model, labels)
    panels.append(dual)
    return tuple(panels), pairs


def build_case(case):
    if case not in CASES:
        raise ValueError("Unknown publication case")
    with isolated_core():
        return _build_case(case)


def _build_case(case):
    # Private keys deliberately differ from public labels. They never leave this adapter.
    if case == "agg":
        sources = {"private_a": box(0, 0, 2, 3), "private_b": box(6, 1, 8, 4),
                   "private_c": box(3, 6, 5, 8)}
        labels = {key: f"Source {letter}" for key, letter in zip(sources, "ABC")}
        labels["aggregate"] = "Aggregate A"
        target = union_all(list(sources.values())).convex_hull
    else:
        rings = [[(1,1),(3,1),(3,3),(1,3)],
                 [(0,0),(1,0),(1,1),(1,3),(1,4),(0,4)],
                 [(1,0),(3,0),(3,1),(1,1)],
                 [(3,0),(4,0),(4,4),(3,4),(3,3),(3,1)],
                 [(1,3),(3,3),(3,4),(1,4)]]
        sources = {f"private_{i}": Polygon(r) for i, r in enumerate(rings)}
        labels = {key: "Source S" if i == 0 else f"N{i}" for i, key in enumerate(sources)}
        target = {"p2p": Polygon([(2,1.5),(3.5,1.5),(3.5,2.5),(2,2.5)]),
                  "p2pt": Point(2,2), "p2l": LineString([(1.6,2.4),(1.5,1.8),(2.4,1.7)])}[case]
    public_sources = tuple((labels[k], p) for k, p in sources.items())
    outlines = _outlines(public_sources, target, case)
    model = _base(sources)
    base = _model_facets(model, labels)
    input_panels = (
        Panel("input_geometries", "Source and target geometries", "main.extrude_polygon; supplied test geometry",
              "One consistent synthetic example throughout all five stages; the target is a geometric template.", shapes=outlines),
        Panel("input_cells", "Initial DHE cells and transition levels", "main.extrude_polygon; main.sew_shared_walls",
              "Constructed base cells below z_s; target geometry at z_t. Z is an ordered transition coordinate.",
              shapes=outlines, facets=base, default_3d=True, dimensions=("2D", "3D"), topology=True,
              checks=("Initial cell cycles, pointers, Euler characteristic and interfaces checked",)),
    )
    preparation_events = ()
    preparation_counts = ()
    if case == "p2p":
        with OperatorTrace(labels, "prepare") as trace:
            overlay_top_faces(model, target, source_id="private_0", target_id="private_target", z=1.)
        preparation_events = tuple(trace.events)
        counts = trace.counts.copy()
        regions = []
        for fragment in model.overlay.fragments:
            kind = classify_region(fragment.geometry, sources["private_0"], target)
            color, label, hatch = CLASS_STYLE[kind]
            regions.append(Shape(fragment.geometry, color, label, hatch=hatch))
        old_vertices = {tuple(p) for g in sources.values() for p in g.exterior.coords}
        crossings = tuple(Shape(Point(v.x, v.y), GREEN,
                                "Reused crossing" if (v.x,v.y) in old_vertices else "New crossing (MVE)",
                                "crossing", z=1.)
                          for v in model.overlay.crossing_vertices)
        effects = tuple(Line(e.before, GRAY, "Edge before MVE", "construction", "--")
                        for e in preparation_events if e.operator == "MVE") + tuple(
                            Line(e.after, GREEN, e.operator + " (observed)", "construction") for e in preparation_events)
        with OperatorTrace(labels, "prepare") as tri_trace:
            triangulate_active_regions(model)
        counts.update(tri_trace.counts)
        preparation_counts = tuple(sorted(counts.items()))
        app.validate_model(model)
        triangle_lines = tuple(Line(points(f.seed) + (points(f.seed)[0],), GREEN,
                                    "Temporary active-region DHE subdivision", "primal")
                               for f in model.overlay.fragments if f.region_kind.active)
        prep = (
            Panel("initial_boundaries", "(S, T) before overlay", "main.extrude_polygon", "Unsplit source and neighbor roofs with target outline.", shapes=outlines),
            Panel("region_classification", "Active, retained and unaffected regions", "top_face_overlay.overlay_top_faces; classify_region",
                  OVERLAY_NOTE, shapes=tuple(regions) + outlines),
            Panel("crossings_operators", "Crossings and observed operator effects", "overlay_top_faces: MVE, MEV, MEF",
                  "MVE splits edge rings; MEV attaches arc vertices; MEF splits roof faces. Crossing symbols use the captured overlay output.",
                  shapes=outlines + crossings, lines=effects, topology=True),
            Panel("temporary_subdivision", "Temporary active-region subdivision", "active_region_triangulation.triangulate_active_regions",
                  "MEF realizes the chosen active-region diagonals. These temporary roofs are not the final transition facets.",
                  shapes=tuple(regions) + outlines, lines=triangle_lines, topology=True),
        )
        plans = _preflight(model, 2.)
        definition = TargetDefinition("polygon", "private_target", target)
    elif case in {"p2pt", "p2l"}:
        kind = "point" if case == "p2pt" else "polyline"
        definition = TargetDefinition(kind, "private_target", target)
        prepared = prepare_collapse(model, definition, "private_0", 1., 2.)
        plans = prepared.upper_plans
        if case == "p2pt":
            ring = list(sources["private_0"].exterior.coords)[:-1]
            rays = tuple(Line(((*p,1.),(target.x,target.y,2.)), GREEN,
                              "Checked boundary-to-apex visibility", "preparation", "--") for p in ring)
            prep = (Panel("interior_visibility", "Interior target and visibility", "transition_targets.prepare_collapse",
                          "Production preflight accepted an interior point visible from every source-boundary vertex. No polygon overlay or MVE call.",
                          shapes=outlines, lines=rays, checks=("Interior and boundary visibility preflight passed",)),
                    Panel("ordered_boundary", "Ordered source-boundary segments", "prepare_collapse: oriented source ring",
                          "Oriented source segments supply the apex fan and neighboring ownership; geometric preparation only.",
                          shapes=outlines, lines=tuple(Line(((*a,1.),(*b,1.)), GREEN, "Ordered boundary", "preparation")
                                                     for a,b in zip(ring,ring[1:]+ring[:1]))))
        else:
            ends = prepared.endpoint_extensions
            cut = Line(((*ends[0],1.), *tuple((*p,1.) for p in target.coords), (*ends[1],1.)),
                       ORANGE, "Endpoint tangent extensions", "preparation", "--")
            chains = tuple(Line(tuple((*p,1.) for p in chain), color, f"Source chain {i+1}", "preparation")
                           for i,(chain,color) in enumerate(zip(prepared.boundary_chains,(GREEN,CYAN))))
            prep = (Panel("endpoint_extensions", "Endpoint tangents and coordinate noding", "transition_targets.prepare_collapse",
                          "Actual endpoint extensions node coordinate plans; no MVE is executed in this path.", shapes=outlines, lines=(cut,)),
                    Panel("source_chains", "Two source-boundary chains", "prepare_collapse.boundary_chains",
                          "The captured chains feed two strips meeting at the complete target polyline.", shapes=outlines, lines=chains,
                          checks=("Simple interior ridge, clean boundary cut and two chains accepted",)))
    else:
        preview = build_aggregation_preview(sources, origin=(0.,0.))
        gap = preview.hull.difference(preview.union)
        prep = (
            Panel("union_hull_gap", "Union U, hull H, and gap G", "aggregation_geometry.build_aggregation_preview",
                  "G = H − U. Geometric preparation has no DHE operator calls.",
                  shapes=(Shape(gap, ORANGE, "Gap G = H − U", hatch="//"),
                          Shape(preview.union, GRAY, "Source union U")) + outlines),
            Panel("growth_ownership", "Source-owned skeleton growth regions", "build_aggregation_preview.growth_faces; skeleton_arcs",
                  "Originating source edges label actual clipped skeleton-growth regions; colors represent provenance.",
                  shapes=tuple(Shape(f.geometry, COLORS[labels[f.source_id]], labels[f.source_id]) for f in preview.growth_faces) + outlines,
                  lines=tuple(Line(tuple((*p,1.) for p in a.geometry.coords), GREEN, "Clipped skeleton arc", "preparation")
                              for a in preview.skeleton_arcs)),
        )
        agg_plans, _, _ = _plan_shell(preview, 0., 1., 1.8, 2.)
        planned = tuple(Facet(tuple(tuple(p) for p in f.points), "Aggregate A", f.part,
                              provenance=labels.get(f.source_id, "Aggregate A"))
                        for f in agg_plans)
    if case != "agg":
        planned = _plans(plans, labels)
    transition = tuple(f for f in planned if f.part in {"connecting", "neighbour_transition", "separator"})
    focus = tuple(f for f in transition if f.part == "connecting")
    ownership = transition if case != "agg" else tuple(f for f in planned if f.part != "bottom_cap")
    plan_routine = ("transition_layer._preflight" if case=="p2p" else
                    "transition_targets.prepare_collapse" if case!="agg" else "aggregation_layer._plan_shell")
    plan_panels = (
        Panel("projected_facet_plan", "Transition-facet plan in projection", plan_routine,
              "Computed facet coordinates in XY projection; vertical facets may project to lines. Arrows show the stored winding of one facet plan, not connected DHE half-edges.",
              shapes=outlines, facets=focus, checks=("Accepted production facet plans",)),
        Panel("lifted_facet_plan", "Inclined facets and level assignment", plan_routine,
              "Piecewise-planar transition with shared planned XYZ coordinates; not a tangent-smooth curved surface.",
              shapes=outlines, facets=focus, default_3d=True, dimensions=("2D", "3D")),
        Panel("ownership_interfaces", "Paired ownership and separators" if case != "agg" else "Growth provenance and upper band",
              plan_routine,
              "Oppositely oriented interface copies and separators where present; still facet plans." if case != "agg" else
              "Source colors show provenance within one planned aggregate shell; growth completes at z_m, below the hull cap at z_t.",
              shapes=outlines, facets=ownership, default_3d=True, dimensions=("2D", "3D")),
    )
    with OperatorTrace(labels, "realize") as trace:
        if case == "agg":
            model = build_aggregation_model(preview, bottom_z=0., source_z=1., merge_z=1.8, top_z=2.)
        else:
            build_transition_layer(model, target_z=2., target=definition, source_id="private_0", source_z=1.)
    if case == "agg":
        validate_aggregation_model(model)
    else:
        validate_transition_layer(model)
    # Extra facet-plan identity and planarity checks support only these precise annotations.
    from plane_slicing import _face_frame
    for cell in model.cells:
        for face in dhe.get_UniqueFace_in_cell(cell.seed):
            _face_frame(points(face))
    final = _model_facets(model, labels)
    for f in transition:
        if not any(f.owner == a.owner and tuple(sorted(f.points)) == tuple(sorted(a.points)) for a in final):
            raise ValueError("A transition-facet plan does not match the realized boundary")
    realization, pairs = _construction_panels(tuple(trace.events), final, model, labels)
    slicer = PlaneSlicer(model)
    mid_z = 1.4 if case == "agg" else 1.5
    intermediate = tuple((labels[k], g) for k,g in slicer.sections(SlicePlane.horizontal(mid_z), include_degenerate=True).items())
    terminal = tuple((labels[k], g) for k,g in slicer.sections(SlicePlane.horizontal(2.), include_degenerate=True).items())
    terminal_label = "Aggregate A" if case == "agg" else "Source S"
    actual_target = dict(terminal)[terminal_label]
    if not (actual_target.equals(target) or (actual_target.geom_type == target.geom_type == "Polygon" and
                                            actual_target.symmetric_difference(target).area < 1e-8)):
        raise ValueError("Terminal DHE slice does not match its target")
    if case == "agg":
        if len(model.cells) != 1 or trace.counts["JoinByFace"]:
            raise ValueError("Aggregate realization must construct one cell without JoinByFace")
    final_checks = ("Pointer identities, closed cycles and per-cell Euler characteristic checked",
                    "Planarity and facet-plan identity checked", "Positive volumes and cap dimensions checked",
                    "Actual terminal slicer geometry matches target")
    if case != "agg":
        final_checks += ("Logical neighbor pairs and reciprocal interfaces checked",
                         "Coverage/overlap checked at the production validator's seven transition levels",)
    else:
        final_checks += ("One aggregate cell; vertex links and hull cap checked",)
    final_panels = (
        Panel("realized_dhe", "Final realized DHE boundary", "build_aggregation_model; validate_aggregation_model" if case == "agg" else
              "build_transition_layer; validate_transition_layer",
              "One aggregate DHE cell; subdued colors indicate provenance only." if case == "agg" else
              "Realized source and coupled neighboring cells; no internal source-level caps.",
              facets=final, default_3d=True, dimensions=("2D", "3D"), topology=True, checks=final_checks),
        Panel("intermediate_section", "Actual intermediate horizontal section", "PlaneSlicer.sections(include_degenerate=True)",
              "Actual section at z_s + ½(z_m − z_s)." if case == "agg" else "Actual section at z_s + ½(z_t − z_s).",
              shapes=tuple(Shape(g, COLORS[label], label) for label,g in intermediate), checks=("Section extracted from realized DHE faces",)),
        Panel("terminal_representation", "Actual terminal representation", "PlaneSlicer.sections(include_degenerate=True)",
              "Terminal " + actual_target.geom_type + "; " + ("one hull polygon." if case == "agg" else
              "point contact, without a zero-area cap." if case == "p2pt" else
              "ridge contact, without a zero-area cap." if case == "p2l" else "target polygon."),
              shapes=(Shape(actual_target, MAGENTA, {"p2p":"Target T", "p2pt":"$P_t$", "p2l":"$L_t$", "agg":"Target H"}[case]),),
              checks=("Terminal representation compared with target geometry",)),
    )
    stages = (Stage(case,1,input_panels), Stage(case,2,prep,OVERLAY_NOTE if case == "p2p" else ""),
              Stage(case,3,plan_panels), Stage(case,4,realization), Stage(case,5,final_panels))
    # Coordinate-based figure-local names are stable across every stage of this case.
    coords, edges = set(), set()
    for stage in stages:
        for panel in stage.panels:
            for f in panel.facets:
                coords.update(f.points)
                edges.update(tuple(sorted((a,b))) for a,b in zip(f.points,f.points[1:]+f.points[:1]))
            for line in panel.lines:
                if line.group != "dual":
                    coords.update(line.points)
                    edges.update(tuple(sorted((a,b))) for a,b in zip(line.points,line.points[1:]))
    vertices = tuple((p, f"v{i+1}") for i,p in enumerate(sorted(coords)))
    edge_names = tuple((p, f"e{i+1}") for i,p in enumerate(sorted(edges)))
    operations = (("prepare", preparation_counts), ("realize", tuple(sorted(trace.counts.items()))))
    domain = union_all(list(sources.values()) + [target])
    return CaseData(case, stages, public_sources, target, domain.bounds, operations,
                    preparation_events + tuple(trace.events), intermediate, terminal, pairs,
                    vertices, edge_names, len(model.cells))
