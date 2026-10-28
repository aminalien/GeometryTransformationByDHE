"""Linked XY provenance and face selection for the single aggregate cell."""

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon as PolygonPatch
from shapely.geometry import Point

from DHECore import face_edges
from plot_aggregation_preview import _fill, _stroke, HULL_COLOR, MEETING_COLOR, SKELETON_COLOR


class AggregationProjectionPanel:
    def __init__(self, viewer, layer):
        self.viewer, self.layer = viewer, layer
        self.highlight_artists = []
        self.selected_fragment = None
        self.current_is_vertical = False
        self.current_halfedge_2d_artist = None
        self.face_by_edge = {}
        for facet in layer.facets:
            for seed in (facet.seed, facet.seed.Adjacent()):
                for edge in face_edges(seed):
                    self.face_by_edge[edge] = facet
        fig = viewer.fig
        for text in list(fig.texts):
            if text.get_text() == "Feature aggregate":
                text.remove()
        viewer.checkbox.ax.set_position([0.01, 0.78, 0.13, 0.15])
        viewer.text_box.ax.set_position([0.055, 0.67, 0.085, 0.045])
        fig.text(0.02, 0.63, "Source provenance", fontsize=9, fontweight="bold")
        for i, key in enumerate(layer.preview.sources):
            fig.text(0.02, 0.59-i*0.032, key, color=layer.source_colors[key], fontsize=9)
        self.ax = fig.add_axes([0.475, 0.35, 0.22, 0.52])
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_title("XY aggregation projection", fontsize=10)
        self.ax.set_xlabel("Local X (m)")
        self.ax.set_ylabel("Local Y (m)")
        self.ax.grid(alpha=0.12)
        for key, partition in layer.preview.partitions.items():
            _fill(self.ax, partition, color=layer.source_colors[key], alpha=0.27,
                  edgecolor="white", linewidth=0.7)
            _stroke(self.ax, layer.preview.sources[key].boundary, color=layer.source_colors[key],
                    linewidth=1.4, linestyle=":")
            p = layer.preview.sources[key].representative_point()
            self.ax.text(p.x, p.y, key, fontsize=7, ha="center", va="center")
        for arc in layer.preview.skeleton_arcs:
            _stroke(self.ax, arc.geometry, color=SKELETON_COLOR, lw=0.6, alpha=0.65)
        for target in layer.preview.target_lines:
            _stroke(self.ax, target.geometry, color=MEETING_COLOR if target.kind == "meeting" else HULL_COLOR,
                    lw=1.1)
        _stroke(self.ax, layer.preview.hull.boundary, color=HULL_COLOR, lw=1.4)
        xmin, ymin, xmax, ymax = layer.preview.hull.bounds
        pad = max(xmax-xmin, ymax-ymin)*0.06
        self.ax.set_xlim(xmin-pad, xmax+pad)
        self.ax.set_ylim(ymin-pad, ymax+pad)
        self.ax.legend(handles=[Line2D([], [], color=MEETING_COLOR, label="Meeting targets"),
                                Line2D([], [], color=HULL_COLOR, label="Convex hull")],
                       loc="upper left", fontsize=6)
        self.status = fig.text(0.46, 0.285, "", fontsize=7)
        fig.text(0.02, 0.335, "Current half-edge", color="red", fontsize=9)
        fig.text(0.02, 0.305, "Other face edges\n(projection only)", color="#00bcd4", fontsize=8)
        fig.suptitle(f"DHE aggregation | {', '.join(layer.preview.sources)} | "
                     f"growth Z={layer.source_z:g}–{layer.merge_z:g}; hull top Z={layer.target_z:g}", fontsize=14)
        fig.canvas.manager.set_window_title("DHE: single-cell polygon aggregation")
        viewer.selection_callbacks.append(self.on_selection)
        self.click_connection = fig.canvas.mpl_connect("button_press_event", self.on_click)
        initial = max(layer.connecting_facets, key=lambda f: f.geometry.area, default=layer.facets[0])
        viewer._set_current_edge(initial.seed)
        viewer._draw_highlight_line()
        viewer._update_face_fill()

    def on_selection(self, edge):
        for artist in self.highlight_artists:
            artist.remove()
        self.highlight_artists.clear()
        self.selected_fragment = self.face_by_edge.get(edge)
        facet = self.selected_fragment
        coords = [(e.V.x, e.V.y) for e in face_edges(edge)]
        polygon = facet.geometry if facet else None
        if polygon is not None and polygon.area > self.layer.preview.tolerance**2:
            patch = PolygonPatch(coords, closed=True, facecolor=self.layer.face_color(edge),
                                 edgecolor="none", alpha=0.5, zorder=10)
            self.ax.add_patch(patch)
            self.highlight_artists.append(patch)
        for current in face_edges(edge):
            a, b = current.V, current.S.V
            is_current = current is edge
            color = "red" if is_current else "#00bcd4"
            vertical = np.hypot(b.x-a.x, b.y-a.y) <= self.layer.preview.tolerance
            if vertical:
                artist, = self.ax.plot(a.x, a.y, "o", color=color, ms=5 if is_current else 3, zorder=15)
            else:
                artist = self.ax.annotate("", xy=(b.x, b.y), xytext=(a.x, a.y),
                                          arrowprops=dict(arrowstyle="->", color=color,
                                                          lw=2 if is_current else 1.3), zorder=15)
            self.highlight_artists.append(artist)
            if is_current:
                self.current_is_vertical = vertical
                self.current_halfedge_2d_artist = artist
        owner = str(edge.D.V.i)
        text = f"{edge.info} | cell {owner}"
        if facet:
            heights = [p[2] for p in facet.xyz]
            text += f" | {facet.part}\nSource: {facet.source_id or 'all / merged hull'} | Z={min(heights):g}–{max(heights):g} m"
        if self.current_is_vertical:
            text += " | vertical in plan"
        self.status.set_text(text)
        self.viewer.fig.canvas.draw_idle()

    def on_click(self, event):
        if event.inaxes is not self.ax or event.button != 1 or event.xdata is None or event.ydata is None:
            return
        point = Point(event.xdata, event.ydata)
        candidates = [f for f in self.layer.connecting_facets if f.geometry.covers(point)]
        if not candidates:
            candidates = [f for f in self.layer.facets if f.part == "bottom_cap" and f.geometry.covers(point)]
        if not candidates:
            return
        seed = candidates[0].seed
        if self.viewer.e.D.V is self.layer.model.exterior:
            seed = seed.Adjacent()
        self.viewer._set_current_edge(seed)
        self.viewer._draw_highlight_line()
        self.viewer._update_face_fill()
