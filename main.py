"""Preview polygon aggregation or construct Dual Half Edge transitions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

import DHECore as dhe
from DHECore import face_edges

if TYPE_CHECKING:
    from aggregation_geometry import AggregationPreview
    from top_face_overlay import TopFaceOverlay
    from transition_layer import TransitionLayer

input_shp = "C:/University/PhD/Papers/Third Journal Paper/QGIS/OSM/BuildingFootprintStep0.shp"
POLYGON_IDS = [3000, 3001, 3002, 3003, 3004]
AGGREGATION_IDS = [8, 9, 17, 18, 21, 1123]
SOURCE_ID = 3000
TARGET_ID = 3006
TARGET_KIND = "polyline"
LineSHP = "C:/University/PhD/Papers/Third Journal Paper/QGIS/OSM/LineGenerlized.shp"
LINE_ID = 3001
ID_FIELD = "osm_id"
EXTRUSION_HEIGHT = 5.0
PROJECTED_CRS = "EPSG:32633"
TOLERANCE = 1e-6  # metres
CELL_COLORS = [
    (0.15, 0.48, 0.78, 1.0),
    (0.92, 0.48, 0.16, 1.0),
    (0.22, 0.63, 0.43, 1.0),
    (0.55, 0.34, 0.23, 1.0),
    (0.09, 0.68, 0.76, 1.0),
]


@dataclass
class ExtrudedCell:
    feature_id: str
    seed: dhe.DualHalfEdge
    dual_vertex: dhe.Vertex
    logical_feature_id: str | None = None

    def __post_init__(self):
        if self.logical_feature_id is None:
            self.logical_feature_id = self.feature_id
        self.dual_vertex.logical_feature_id = self.logical_feature_id


@dataclass
class ExtrusionModel:
    cells: list[ExtrudedCell]
    exterior: dhe.Vertex
    origin: tuple[float, float]
    shared_walls: list[tuple[dhe.DualHalfEdge, dhe.DualHalfEdge]]
    overlay: TopFaceOverlay | None = None
    transition: TransitionLayer | None = None

    @property
    def all_cells(self) -> list[ExtrudedCell]:
        return self.cells

    def active_edges(self, include_transition: bool = True) -> list[dhe.DualHalfEdge]:
        """Return live cell edges; merged final cells include both height intervals.

        include_transition is retained for callers of the earlier-stage API.
        """
        killed = dhe.Getdhe()[1]
        return list(dict.fromkeys(
            edge for cell in (self.all_cells if include_transition else self.cells)
            for edge in dhe.Cell(cell.seed)
            if edge not in killed
        ))


def _vertex_key(vertex: dhe.Vertex) -> tuple[int, int, int]:
    return tuple(round(float(value) / TOLERANCE)
                 for value in (vertex.x, vertex.y, vertex.z))


def _face_key(seed: dhe.DualHalfEdge) -> tuple:
    return tuple(sorted(_vertex_key(edge.V) for edge in face_edges(seed)))


def load_selected_polygons(
    path: str | Path,
    ids=POLYGON_IDS,
    id_field: str = ID_FIELD,
) -> gpd.GeoDataFrame:
    """Select feature IDs and return valid single-ring polygons in metre units."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Shapefile not found: {path}")
    requested = list(dict.fromkeys(str(value).strip() for value in ids))
    if not requested:
        raise ValueError("Select at least one polygon ID.")
    frame = gpd.read_file(path)
    if id_field not in frame.columns:
        raise ValueError(f"ID field {id_field!r} is missing. Available fields: {list(frame.columns)}")
    if frame.crs is None:
        raise ValueError("The shapefile must declare a coordinate reference system.")
    frame = frame.copy()
    frame[id_field] = frame[id_field].astype("string").str.strip()
    selected = frame[frame[id_field].isin(requested)].copy()
    missing = [value for value in requested if value not in set(selected[id_field])]
    if missing:
        raise ValueError(f"Polygon IDs not found in {id_field}: {', '.join(missing)}")
    if selected[id_field].duplicated().any():
        raise ValueError("Selected feature IDs must uniquely identify polygons.")
    selected = selected.set_index(id_field, drop=False).loc[requested].reset_index(drop=True)
    for _, row in selected.iterrows():
        _validate_polygon(row.geometry, row[id_field], tolerance=0.0)
    selected = selected.to_crs(PROJECTED_CRS)
    for _, row in selected.iterrows():
        _validate_polygon(row.geometry, row[id_field])
    for i, polygon in enumerate(selected.geometry):
        for other in selected.geometry.iloc[i + 1:]:
            if polygon.intersection(other).area > TOLERANCE ** 2:
                raise ValueError("Selected footprints overlap in area; shared boundaries are supported.")
    return selected


def _validate_polygon(polygon, feature_id, tolerance=TOLERANCE) -> None:
    if polygon is None or polygon.is_empty:
        raise ValueError(f"Feature {feature_id} has no geometry.")
    if not isinstance(polygon, Polygon):
        raise ValueError(f"Feature {feature_id}: only Polygon geometry is supported, got {polygon.geom_type}.")
    if polygon.interiors:
        raise ValueError(f"Feature {feature_id} contains holes; this example supports single-ring polygons.")
    if not polygon.is_valid:
        raise ValueError(f"Feature {feature_id} is invalid: {explain_validity(polygon)}")
    if polygon.area <= 0:
        raise ValueError(f"Feature {feature_id} has zero area.")
    coords = list(polygon.exterior.coords)
    if any(not isfinite(float(value)) for point in coords for value in point[:2]):
        raise ValueError(f"Feature {feature_id} has non-finite coordinates.")
    if any(
        abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance
        for a, b in zip(coords, coords[1:])
    ):
        raise ValueError(f"Feature {feature_id} has a zero-length boundary segment.")


def extrude_polygon(
    polygon: Polygon,
    feature_id,
    height: float,
    exterior: dhe.Vertex,
    color=CELL_COLORS[0],
    z_down: float = 0.0,
) -> ExtrudedCell:
    """Build caps and walls with the original DHE Euler construction operations."""
    if not isfinite(height) or not isfinite(z_down) or height <= z_down:
        raise ValueError("Extrusion height must be finite and greater than zDown.")
    _validate_polygon(polygon, feature_id)
    polygon = orient(polygon, sign=1.0)
    coords = [(float(x), float(y)) for x, y, *_ in polygon.exterior.coords]
    center = polygon.representative_point()
    interior = dhe.make_vertex(center.x, center.y, (height + z_down) / 2,
                               ch=str(feature_id), clr=color)
    bottom = dhe.MakeFace(coords, z_down, interior, exterior)
    top = dhe.MakeFace(coords, height, interior, exterior)
    # Snapshot corresponding cap vertices before MEF changes their face cycles.
    matches = dhe.FindMatchesHE(bottom.S, top, TypeOfMatch="juststart",
                               tol=TOLERANCE, OneMatch=False)
    if len(matches) != len(coords) - 1:
        raise ValueError(f"Feature {feature_id}: cap vertex matching failed.")
    for lower, upper in matches:
        dhe.MEF(lower, upper)
    return ExtrudedCell(str(feature_id), bottom, interior)


def sew_shared_walls(cells: list[ExtrudedCell]) -> list[tuple]:
    """Join oppositely oriented coincident wall faces, retaining both cells."""
    walls = {}
    for cell in cells:
        for face in dhe.get_UniqueFace_in_cell(cell.seed):
            edges = face_edges(face)
            if len({_vertex_key(edge.V)[2] for edge in edges}) < 2:
                continue
            walls.setdefault(_face_key(face), []).append((cell, face))
    shared = []
    for faces in walls.values():
        if len(faces) > 2:
            raise ValueError("A wall belongs to more than two cells.")
        if len(faces) != 2:
            continue
        (cell_a, first), (cell_b, second) = faces
        if cell_a is cell_b:
            raise ValueError("A cell has duplicate coincident walls.")
        aligned = next((
            edge for edge in face_edges(second)
            if _vertex_key(edge.V) == _vertex_key(first.S.V)
            and _vertex_key(edge.S.V) == _vertex_key(first.V)
        ), None)
        if aligned is None:
            raise ValueError("Shared walls must have opposite boundary orientations.")
        dhe.JoinByFace(first, aligned)
        shared.append((first, aligned))
    # After sewing, coincident primal vertices refer to the same vertex object.
    canonical = {}
    for cell in cells:
        for edge in dhe.Cell(cell.seed):
            for record in (edge, edge.S, edge.Adjacent(), edge.Adjacent().S):
                key = _vertex_key(record.V)
                record.V = canonical.setdefault(key, record.V)
    return shared


def build_model(
    path: str | Path = input_shp,
    ids=POLYGON_IDS,
    height: float = EXTRUSION_HEIGHT,
    z_down: float = 0.0,
) -> ExtrusionModel:
    """Load, extrude, and sew footprints without importing or opening the viewer."""
    if not isfinite(height) or not isfinite(z_down) or height <= z_down:
        raise ValueError("Extrusion height must be finite and greater than zDown.")
    footprints = load_selected_polygons(path, ids)
    xmin, ymin, xmax, ymax = footprints.total_bounds
    origin = (float(xmin), float(ymin))
    dhe.reset_globals()
    exterior = dhe.make_vertex(float(xmax - xmin) + 5, float(ymax - ymin) + 5,
                               (height + z_down) / 2, ch="VINF", clr=(0.55, 0.2, 0.75, 1))
    exterior.logical_feature_id = "VINF"
    cells = []
    for index, row in footprints.iterrows():
        local_polygon = translate(row.geometry, xoff=-origin[0], yoff=-origin[1])
        cells.append(extrude_polygon(
            local_polygon, row[ID_FIELD], height, exterior,
            CELL_COLORS[index % len(CELL_COLORS)], z_down=z_down,
        ))
    shared = sew_shared_walls(cells)
    return ExtrusionModel(cells, exterior, origin, shared)


def validate_model(model: ExtrusionModel) -> list[dict]:
    """Check closed cells, pointer consistency, and reciprocal shared-wall links."""
    summaries = []
    for cell in model.all_cells:
        edges = dhe.Cell(cell.seed)
        faces = dhe.get_UniqueFace_in_cell(cell.seed)
        vertices = {edge.V for edge in edges}
        undirected = {frozenset((edge.V, edge.S.V)) for edge in edges}
        for face in faces:
            face_edges(face)
        for edge in edges:
            if edge.S.S is not edge or edge.D.D is not edge:
                raise ValueError(f"Cell {cell.feature_id}: broken symmetric/dual pointers.")
            if edge.NF.V is not edge.S.V:
                raise ValueError(f"Cell {cell.feature_id}: discontinuous face boundary.")
            if edge.D.V is not cell.dual_vertex:
                raise ValueError(f"Cell {cell.feature_id}: inconsistent interior dual vertex.")
        if len(vertices) - len(undirected) + len(faces) != 2:
            raise ValueError(f"Cell {cell.feature_id} is not a closed genus-zero solid.")
        neighbors = {
            str(edge.Adjacent().D.V.i) for edge in edges
            if edge.Adjacent().D.V is not model.exterior
        }
        summaries.append({
            "id": cell.feature_id, "vertices": len(vertices),
            "edges": len(undirected), "faces": len(faces),
            "neighbors": sorted(neighbors),
        })
    interfaces = model.shared_walls
    for first, second in interfaces:
        for a, b in ((first, second), (second, first)):
            if any(edge.Adjacent().D.V is not b.D.V for edge in face_edges(a)):
                raise ValueError("A shared wall does not have reciprocal cell adjacency.")
    return summaries


def construct_model(args, *, target_kind=None) -> ExtrusionModel:
    """Build and validate one target without creating any viewer objects."""
    kind = target_kind or args.target_kind
    if not args.no_overlay and kind != "polygon" and (args.no_transition or args.no_triangulation):
        raise ValueError("Overlay-only stage flags require --target-kind polygon.")
    model = build_model(args.input_shp, args.ids, args.height, z_down=args.z_down)
    if args.no_overlay:
        validate_model(model)
        return model
    from transition_targets import TargetDefinition, load_line_target
    from transition_layer import build_transition_layer
    if kind == "polygon":
        from top_face_overlay import overlay_top_faces, validate_overlay
        target_geometry = load_selected_polygons(args.input_shp, [args.target_id]).geometry.iloc[0]
        target_geometry = translate(target_geometry, xoff=-model.origin[0], yoff=-model.origin[1])
        definition = TargetDefinition(kind, str(args.target_id), target_geometry)
        overlay_top_faces(model, target_geometry, source_id=args.source_id,
                          target_id=args.target_id, z=args.height, tolerance=TOLERANCE)
        validate_overlay(model)
        if not args.no_triangulation:
            from active_region_triangulation import triangulate_active_regions
            triangulate_active_regions(model)
            if not args.no_transition:
                build_transition_layer(model, target_z=args.target_z, target=definition)
    else:
        from top_face_overlay import face_polygon, roof_faces
        source = next((c for c in model.cells if c.feature_id == str(args.source_id)), None)
        if source is None:
            raise ValueError("The source is not one of the constructed cells.")
        if kind == "point":
            centroid = face_polygon(roof_faces(source, args.height)[0]).centroid
            definition = TargetDefinition(kind, "centroid", centroid)
        else:
            definition = load_line_target(args.line_shp, args.line_id, model.origin, PROJECTED_CRS)
        build_transition_layer(model, target_z=args.target_z, target=definition,
                               source_id=args.source_id, source_z=args.height)
    validate_model(model)
    return model


def construct_aggregation_preview(path=input_shp, ids=AGGREGATION_IDS) -> AggregationPreview:
    """Load the selected sources and compute geometry without changing DHE state."""
    from aggregation_geometry import build_aggregation_preview
    footprints = load_selected_polygons(path, ids)
    xmin, ymin = footprints.total_bounds[:2]
    origin = (float(xmin), float(ymin))
    sources = {str(row[ID_FIELD]): translate(row.geometry, xoff=-origin[0], yoff=-origin[1])
               for _, row in footprints.iterrows()}
    return build_aggregation_preview(sources, origin=origin, tolerance=TOLERANCE)


def _run_aggregation_preview(args):
    from aggregation_geometry import validate_aggregation
    preview = construct_aggregation_preview(args.input_shp, args.ids)
    metrics = validate_aggregation(preview)
    print(f"Projected CRS: {PROJECTED_CRS}; local XY origin: {preview.origin}")
    print(f"Aggregation sources: {', '.join(preview.sources)}; {len(preview.gap_regions)} transition regions")
    print(f"UP={metrics['source_area']:.6f} m2; CH-UP={metrics['growth_area']:.6f} m2; "
          f"CH={metrics['hull_area']:.6f} m2")
    for key, partition in preview.partitions.items():
        print(f"  Source {key}: top partition={partition.area:.6f} m2; "
              f"target length={preview.targets[key].length:.6f} m")
    print(f"Partition validation: overlap={metrics['overlap_area']:.9g}, "
          f"missing={metrics['missing_area']:.9g}, excess={metrics['excess_area']:.9g} m2; "
          f"frame stability error={preview.frame_stability_error:.9g} m2")
    print("Geometry preview only; the 3D transition and single aggregate DHE cell are the next stage.")
    if not args.no_show or args.preview:
        if args.no_show:
            import matplotlib
            matplotlib.use("Agg")
        from plot_aggregation_preview import AggregationPreviewPlot
        viewer = AggregationPreviewPlot(preview)
        if args.preview:
            args.preview.parent.mkdir(parents=True, exist_ok=True)
            viewer.fig.savefig(args.preview, dpi=160)
            print(f"Preview saved to {args.preview.resolve()}")
        if not args.no_show:
            viewer.show()
        else:
            import matplotlib.pyplot as plt
            plt.close(viewer.fig)
    return preview


def main(argv=None) -> ExtrusionModel | AggregationPreview:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("aggregation-preview", "transition"), default="aggregation-preview",
                        help="Default: aggregation geometry preview. Use transition for the existing 3D viewer.")
    parser.add_argument("--input-shp", default=input_shp)
    parser.add_argument("--ids", nargs="+", help="Source IDs; defaults depend on the selected scene.")
    parser.add_argument("--z-down", type=float, default=0.0, help="Base elevation in metres.")
    parser.add_argument("--height", type=float, default=EXTRUSION_HEIGHT, help="Source roof elevation in metres.")
    parser.add_argument("--source-id", default=SOURCE_ID, help="Source cell whose roof is being compared.")
    parser.add_argument("--target-id", default=TARGET_ID, help="Target polygon used to subdivide the roofs.")
    parser.add_argument("--target-kind", choices=("polygon", "point", "polyline"), default=TARGET_KIND)
    parser.add_argument("--line-shp", default=LineSHP)
    parser.add_argument("--line-id", default=LINE_ID)
    parser.add_argument("--no-overlay", action="store_true", help="Construct ordinary extrusions only.")
    parser.add_argument("--no-triangulation", action="store_true", help="Stop after the step-1 roof overlay.")
    parser.add_argument("--target-z", type=float, default=10.0, help="Upper transition elevation in metres.")
    parser.add_argument("--no-transition", action="store_true", help="Stop after the flat step-2A triangulation.")
    parser.add_argument("--no-show", action="store_true", help="Validate without opening a window.")
    parser.add_argument("--preview", type=Path, help="Save a PNG of the interactive viewer.")
    args = parser.parse_args(argv)
    if args.ids is None:
        args.ids = AGGREGATION_IDS if args.scene == "aggregation-preview" else POLYGON_IDS
    try:
        if args.scene == "aggregation-preview":
            if args.no_overlay or args.no_triangulation or args.no_transition:
                raise ValueError("Extrusion/transition stage flags require --scene transition.")
            return _run_aggregation_preview(args)
        model = construct_model(args)
        summaries = validate_model(model)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(f"Projected CRS: {PROJECTED_CRS}; local XY origin: {model.origin}")
    final_z = args.target_z if model.transition else args.height
    print(f"Cells: z={args.z_down:g} to {final_z:g} m; sewn shared faces: {len(model.shared_walls)}")
    for summary in summaries:
        print(f"Cell {summary['id']}: V={summary['vertices']}, E={summary['edges']}, "
              f"F={summary['faces']}; neighbors={summary['neighbors']}")
    if model.transition:
        from top_face_overlay import cell_volume
        layer = model.transition
        print(f"Source transition: z={layer.source_z:g} to {layer.target_z:g} m; "
              f"{len(layer.connecting_facets)} connecting triangles, "
              f"{len(layer.cells)} merged cells, {len(layer.shared_interfaces)} sewn interfaces; "
              f"volume={cell_volume(layer.cell):.6f} m3")
    if model.overlay and model.transition is None:
        overlay = model.overlay
        if overlay.triangulation is not None:
            mesh = overlay.triangulation
            print(f"Active-region triangulation: {len(mesh.triangles)} triangles, "
                  f"{len(mesh.inserted_diagonals)} new diagonals; elevations unchanged")
            for region in mesh.regions:
                if region.kind.active:
                    print(f"  Region {region.region_id}: {region.kind.description}; "
                          f"{len(region.fragments)} triangles, area={region.geometry.area:.6f} m2")
        print(f"Roof overlay: source {overlay.source_id}, target {overlay.target_id}; "
              f"{len(overlay.crossing_vertices)} crossings, {len(overlay.fragments)} roof fragments")
        for cell in model.cells:
            fragments = [f for f in overlay.fragments if f.cell_id == cell.feature_id]
            area = sum(f.geometry.area for f in fragments if f.inside_target)
            print(f"  Cell {cell.feature_id}: {len(fragments)} roof faces; target overlap={area:.6f} m2")
    if not args.no_show or args.preview:
        if args.no_show:
            import matplotlib
            matplotlib.use("Agg")
        from plot_dhe_interactive import interactive_plot_from_lists
        if model.transition:
            from viewer_session import ViewerSession
            session = ViewerSession(model, lambda kind: construct_model(args, target_kind=kind))
            viewer = session.viewer
        else:
            viewer = interactive_plot_from_lists(
                model.active_edges(), dhe.Getdhe()[1], show=False, highlight_cells=True,
                overlay=model.overlay, transition=model.transition)
        if args.preview:
            args.preview.parent.mkdir(parents=True, exist_ok=True)
            viewer.fig.savefig(args.preview, dpi=160)
            print(f"Preview saved to {args.preview.resolve()}")
        if not args.no_show:
            viewer.show()
        else:
            import matplotlib.pyplot as plt
            plt.close(viewer.fig)
    return model


if __name__ == "__main__":
    main()
