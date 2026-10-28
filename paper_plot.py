"""Consistent 3D/XY stage rendering, with live DHE navigation only where valid."""

from types import SimpleNamespace
import textwrap

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon as PolygonPatch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely import constrained_delaunay_triangles
from shapely.geometry import Point, Polygon

import DHECore as dhe
from DHECore import face_edges
from aggregation_geometry import polygon_parts
from paper_stages import TRANSFORMATIONS, DisplayFacet
from plot_aggregation_preview import _fill, _stroke
from plot_dhe_interactive import interactive_plot_from_lists


def unique_facets(facets):
    """A shared planned interface is rendered once, with neighbor ownership."""
    selected = {}
    for facet in facets:
        key = tuple(sorted(facet.points))
        if key not in selected or facet.part == "neighbour_transition":
            selected[key] = facet
    return list(selected.values())


def surface(ax, points, color, alpha=0.4, edges=True):
    xyz = np.asarray(points)
    if len(xyz) < 3 or np.linalg.norm(np.sum(np.cross(xyz, np.roll(xyz, -1, axis=0)), axis=0)) < 1e-12:
        return None
    artist = Poly3DCollection([points], facecolor=color, alpha=alpha,
                             edgecolor="0.35" if edges else "none", linewidth=0.5)
    ax.add_collection3d(artist)
    return artist


def frame_axes(ax, bounds):
    xmin, ymin, zmin, xmax, ymax, zmax = bounds
    pad = max(xmax-xmin, ymax-ymin, 1)*0.06
    ax.set_xlim(xmin-pad, xmax+pad)
    ax.set_ylim(ymin-pad, ymax+pad)
    ax.set_zlim(zmin, zmax)
    ax.set_box_aspect((xmax-xmin+2*pad, ymax-ymin+2*pad, zmax-zmin))
    ax.set_autoscale_on(False)
    ax.set_axis_off()


def draw_stage_geometry(ax, stage):
    if stage.spec.id != "shell":
        for facet in unique_facets(stage.context):
            if all(p[2] == stage.metadata["source_z"] for p in facet.points):
                continue
            surface(ax, facet.points, "0.65", 0.055, edges=False)
    for facet in unique_facets(stage.facets):
        surface(ax, facet.points, stage.colors.get(facet.owner, "#668cac"), 0.43)
    z = stage.metadata["target_z"] if stage.spec.id == "partitions" else stage.metadata["source_z"]
    for geometry, color, _ in stage.regions:
        for polygon in polygon_parts(geometry):
            for triangle in constrained_delaunay_triangles(polygon).geoms:
                surface(ax, [(x, y, z) for x, y in list(triangle.exterior.coords)[:-1]], color, 0.42, edges=False)
            for ring in [polygon.exterior, *polygon.interiors]:
                xy = np.asarray(ring.coords)
                ax.plot(xy[:, 0], xy[:, 1], np.full(len(xy), z), color=color, lw=1)


def draw_stage_lines(ax, stage):
    for coords, color, label, style in stage.lines:
        xyz = np.asarray(coords)
        ax.plot(xyz[:, 0], xyz[:, 1], xyz[:, 2], color=color, ls=style, lw=1.4,
                marker="o" if len(xyz) == 1 else None, label=label)


def projection_facets(stage):
    if stage.facets:
        return unique_facets(stage.facets)
    if stage.final:
        layer = stage.model.aggregation or stage.model.transition
        return [DisplayFacet(f.xyz, getattr(f, "source_id", None) or getattr(f, "cell_id", "aggregate"), f.part)
                for f in layer.connecting_facets]
    return []


def draw_xy(ax, stage):
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("XY projection", fontsize=10)
    ax.set_xlabel("Local X (m)")
    ax.set_ylabel("Local Y (m)")
    ax.grid(alpha=0.12)
    for key, source in stage.sources.items():
        _fill(ax, source, color=stage.colors[key], alpha=0.18)
        _stroke(ax, source.boundary, color=stage.colors[key], linewidth=1.1, linestyle=":")
    for geometry, color, _ in stage.regions:
        _fill(ax, geometry, color=color, alpha=0.43, edgecolor="0.5", linewidth=0.6)
    for facet in projection_facets(stage):
        polygon = Polygon([p[:2] for p in facet.points])
        if polygon.area > 1e-12:
            _fill(ax, polygon, color=stage.colors.get(facet.owner, "#668cac"), alpha=0.30,
                  edgecolor="0.5", linewidth=0.5)
    for key, source in stage.sources.items():
        p = source.representative_point()
        ax.text(p.x, p.y, key, fontsize=7, ha="center", va="center", zorder=8)
    labels = set()
    for coords, color, label, style in stage.lines:
        xyz = np.asarray(coords)
        ax.plot(xyz[:, 0], xyz[:, 1], color=color, ls=style, lw=1.3,
                marker="o" if len(xyz) == 1 else None, label=label if label not in labels else None)
        labels.add(label)
    xmin, ymin, _, xmax, ymax, _ = stage.bounds
    pad = max(xmax-xmin, ymax-ymin, 1)*0.06
    ax.set_xlim(xmin-pad, xmax+pad)
    ax.set_ylim(ymin-pad, ymax+pad)
    # Region classes have semantic labels in addition to source-ID colors.
    for _, color, label in stage.regions:
        if label not in labels and (label.startswith("active") or label.startswith("non-active") or label == "background"):
            ax.plot([], [], color=color, linewidth=5, label=label)
            labels.add(label)
    if labels:
        ax.legend(loc="upper left", fontsize=6, framealpha=0.9)


def highlight_xy(ax, points, current_edge=None, color="#668cac"):
    artists = []
    polygon = Polygon([p[:2] for p in points])
    if polygon.area > 1e-12:
        patch = PolygonPatch([p[:2] for p in points], closed=True, facecolor=color,
                             edgecolor="none", alpha=0.5, zorder=12)
        ax.add_patch(patch)
        artists.append(patch)
    for i, (a, b) in enumerate(zip(points, points[1:]+points[:1])):
        selected = i == current_edge
        edge_color = "red" if selected else "#00bcd4"
        if np.linalg.norm(np.subtract(a[:2], b[:2])) < 1e-8:
            artist, = ax.plot(a[0], a[1], "o", color=edge_color, ms=5 if selected else 3, zorder=15)
        else:
            artist = ax.annotate("", xy=b[:2], xytext=a[:2], zorder=15,
                                  arrowprops=dict(arrowstyle="->", color=edge_color, lw=2.2 if selected else 1.3))
        artists.append(artist)
    return artists


def highlight_region(ax3d, axxy, geometry, color, z):
    planar = _fill(axxy, geometry, color=color, alpha=0.5, edgecolor="#00bcd4", linewidth=1.8, zorder=12)
    spatial = []
    for polygon in polygon_parts(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            xy = np.asarray(ring.coords)
            spatial.extend(ax3d.plot(xy[:, 0], xy[:, 1], np.full(len(xy), z), color="#00bcd4", lw=2, zorder=130))
    return spatial, planar


class PaperProjectionPanel:
    def __init__(self, viewer, stage):
        self.viewer, self.stage = viewer, stage
        self.ax = viewer.fig.add_axes([0.475, 0.35, 0.22, 0.52])
        self.status = viewer.fig.text(0.46, 0.285, "", fontsize=7)
        self.highlight_artists = []
        self.highlight_3d = None
        self.region_3d_artists = []
        self.region_selection = None
        self.selection = None
        self.selected_fragment = None
        self.face_by_edge = {}
        draw_xy(self.ax, stage)
        if stage.model:
            layer = stage.model.aggregation or stage.model.transition
            if layer:
                for facet in layer.facets:
                    for seed in (facet.seed, facet.seed.Adjacent()):
                        for edge in face_edges(seed):
                            self.face_by_edge[edge] = facet
            viewer.selection_callbacks.append(self.on_selection)
        self.connection = viewer.fig.canvas.mpl_connect("button_press_event", self.on_click)

    def _clear(self):
        for artist in self.highlight_artists:
            artist.remove()
        self.highlight_artists.clear()
        if self.highlight_3d is not None:
            self.highlight_3d.remove()
            self.highlight_3d = None
        for artist in self.region_3d_artists:
            artist.remove()
        self.region_3d_artists.clear()
        self.selection = None
        self.region_selection = None

    def on_selection(self, edge):
        self._clear()
        points = [(e.V.x, e.V.y, e.V.z) for e in face_edges(edge)]
        color = self.viewer._face_color(edge)
        self.selection = (points, 0, color)
        self.highlight_artists = highlight_xy(self.ax, points, 0, color)
        facet = self.face_by_edge.get(edge)
        self.selected_fragment = facet
        text = f"{edge.info} | cell {edge.D.V.i}"
        if facet is not None:
            text += f" | {facet.part}"
            if hasattr(facet, "source_id"):
                text += f" | source {facet.source_id or 'all'}"
        self.status.set_text(text)
        self.viewer.fig.canvas.draw_idle()

    def on_click(self, event):
        if event.inaxes is not self.ax or event.button != 1 or event.xdata is None or event.ydata is None:
            return
        point = Point(event.xdata, event.ydata)
        stage = self.stage
        if stage.model:
            candidates = []
            layer = stage.model.aggregation or stage.model.transition
            if layer:
                candidates.extend(f.seed for f in layer.connecting_facets if f.geometry.area > 1e-12 and f.geometry.covers(point))
            if not candidates:
                for cell in stage.model.cells:
                    for seed in dhe.get_UniqueFace_in_cell(cell.seed):
                        edges = face_edges(seed)
                        if all(e.V.z == stage.metadata["source_z"] for e in edges):
                            polygon = Polygon([(e.V.x, e.V.y) for e in edges])
                            if polygon.covers(point):
                                candidates.append(seed)
            if candidates:
                seed = candidates[0]
                if self.viewer.e.D.V is stage.model.exterior:
                    seed = seed.Adjacent()
                self.viewer._set_current_edge(seed)
                self.viewer._draw_highlight_line()
                self.viewer._update_face_fill()
            return
        candidates = sorted(projection_facets(stage), key=lambda f: f.part not in ("connecting", "neighbour_transition"))
        facet = next((f for f in candidates if Polygon([p[:2] for p in f.points]).area > 1e-12
                      and Polygon([p[:2] for p in f.points]).covers(point)), None)
        if facet:
            self._clear()
            color = stage.colors.get(facet.owner, "#668cac")
            self.selection = (facet.points, None, color)
            self.highlight_artists = highlight_xy(self.ax, facet.points, None, color)
            self.highlight_3d = surface(self.viewer.ax, facet.points, color, 0.5)
            self.status.set_text(f"Planned geometry | {facet.part} | owner {facet.owner} | no DHE half-edge")
            self.viewer.fig.canvas.draw_idle()
        else:
            for geometry, color, label in stage.regions:
                polygon = next((p for p in polygon_parts(geometry) if p.covers(point)), None)
                if polygon is None:
                    continue
                self._clear()
                z = stage.metadata["target_z"] if stage.spec.id == "partitions" else stage.metadata["source_z"]
                self.region_selection = (polygon, color, z, label)
                self.region_3d_artists, self.highlight_artists = highlight_region(self.viewer.ax, self.ax, polygon, color, z)
                self.status.set_text(f"Planned region | {label} | area={polygon.area:.3f} m² | no DHE half-edge")
                self.viewer.fig.canvas.draw_idle()
                break


def mount_paper_view(stage, figure):
    if stage.model is not None:
        viewer = interactive_plot_from_lists(stage.model.active_edges(), dhe.Getdhe()[1], show=False,
                                             highlight_cells=True, figure=figure)
        def resolve(edge):
            if edge.D.V is stage.model.exterior:
                return stage.model.exterior.color
            if stage.model.aggregation:
                return stage.model.aggregation.face_color(edge)
            key = tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(edge)))
            return stage.face_colors.get(key, edge.D.V.color)
        viewer.face_color_resolver = resolve
        viewer._refresh_scene_colors()
        viewer.checkbox.ax.set_position([0.005, 0.72, 0.13, 0.13])
        viewer.text_box.ax.set_position([0.05, 0.66, 0.085, 0.035])
    else:
        figure.clear()
        viewer = SimpleNamespace(fig=figure, ax=figure.add_axes([0.155, 0.35, 0.29, 0.52], projection="3d"),
                                 selection_callbacks=[], plane_controls=None)
        viewer.navigation_hint = figure.text(0.18, 0.045, "Drag 3D: rotate | Planned geometry; DHE navigation is unavailable", fontsize=8)
        figure.text(0.02, 0.60, "Geometry preview\nNo DHE half-edges\nallocated for these\nplanned surfaces", fontsize=10, color="#5f6872")
        draw_stage_geometry(viewer.ax, stage)
    figure.set_size_inches(21, 10)
    viewer.ax.set_position([0.155, 0.35, 0.29, 0.52])
    frame_axes(viewer.ax, stage.bounds)
    draw_stage_lines(viewer.ax, stage)
    viewer.overlay_panel = PaperProjectionPanel(viewer, stage)
    if stage.final:
        from plane_controls import PlaneSlicingPanel
        viewer.plane_controls = PlaneSlicingPanel(viewer, stage.model)
        viewer.transition_panel = viewer.plane_controls
        frame_axes(viewer.ax, stage.bounds)
    else:
        viewer.plane_controls = None
        viewer.navigation_hint.set_position((0.18, 0.045))
        viewer.navigation_hint.set_fontsize(8)
        note = figure.add_axes([0.75, 0.35, 0.23, 0.52])
        note.set_axis_off()
        detail = ("Live DHE navigation is available." if stage.model else "These surfaces are preparation geometry, not sewn DHE faces.")
        note.text(0, 0.96, stage.kind, fontsize=13, weight="bold", va="top")
        note.text(0, 0.83, textwrap.fill(stage.spec.explanation, 43), fontsize=11, va="top", linespacing=1.5)
        note.text(0, 0.48, textwrap.fill(detail, 43), fontsize=10, va="top", color="#566575")
        note.text(0, 0.27, f"Sources: {', '.join(stage.sources)}\nSource Z: {stage.metadata['source_z']:g}\nTarget Z: {stage.metadata['target_z']:g}",
                  fontsize=10, va="top", linespacing=1.5)
        figure.text(0.18, 0.08, "Select an implementation step above. Slicing is available on the final DHE model.", fontsize=9)
    if stage.model:
        layer = stage.model.aggregation or stage.model.transition
        if layer and layer.connecting_facets:
            seed = max(layer.connecting_facets, key=lambda f: f.geometry.area).seed
        else:
            cell = next((c for c in stage.model.cells if c.feature_id == stage.metadata["source_id"]), stage.model.cells[0])
            seed = next(s for s in dhe.get_UniqueFace_in_cell(cell.seed)
                        if all(e.V.z == stage.metadata["source_z"] for e in face_edges(s)))
        viewer._set_current_edge(seed)
        viewer._draw_highlight_line()
        viewer._update_face_fill()
    figure.suptitle(f"{TRANSFORMATIONS[stage.transformation]} | {stage.spec.title}", fontsize=15, y=0.985)
    figure.canvas.manager.set_window_title("DHE paper — transformation stages")
    figure.text(0.5, 0.885, stage.kind, fontsize=10, ha="center", color="#566575")
    return viewer
