"""Plan-view review of aggregation sources, skeleton targets and top ownership."""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path
from matplotlib.widgets import CheckButtons
from shapely.geometry.polygon import orient

from aggregation_geometry import AggregationPreview, SOURCE_COLORS, line_parts, polygon_parts


HULL_COLOR = "#783fb0"
SKELETON_COLOR = "#486272"
MEETING_COLOR = "#c52d3e"


def _fill(ax, geometry, *, color, alpha, edgecolor="none", linewidth=1, zorder=1):
    artists = []
    for polygon in polygon_parts(geometry):
        polygon = orient(polygon, sign=1.0)
        vertices, codes = [], []
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            vertices.extend(coords)
            codes.extend([Path.MOVETO] + [Path.LINETO] * (len(coords) - 2) + [Path.CLOSEPOLY])
        patch = PathPatch(Path(vertices, codes), facecolor=color, alpha=alpha,
                          edgecolor=edgecolor, linewidth=linewidth, zorder=zorder)
        ax.add_patch(patch)
        artists.append(patch)
    return artists


def _stroke(ax, geometry, **kwargs):
    artists = []
    for line in line_parts(geometry):
        x, y = line.xy
        artists.extend(ax.plot(x, y, **kwargs))
    return artists


class AggregationPreviewPlot:
    def __init__(self, preview: AggregationPreview):
        self.preview = preview
        self.colors = {key: SOURCE_COLORS[i % len(SOURCE_COLORS)] for i, key in enumerate(preview.sources)}
        self.fig, self.axes = plt.subplots(1, 3, figsize=(19, 8), sharex=True, sharey=True)
        self.fig.canvas.manager.set_window_title("DHE aggregation — geometry preview")
        self.fig.subplots_adjust(left=0.045, right=0.985, bottom=0.23, top=0.85, wspace=0.13)
        self.fig.suptitle("Aggregation to a fixed convex hull", fontsize=19, y=0.97)
        self.fig.text(0.5, 0.91, "Sources: " + ", ".join(preview.sources)
                      + "   |   Geometry preview before the 3D transition", ha="center", fontsize=11)
        xmin, ymin, xmax, ymax = preview.hull.bounds
        pad = max(xmax - xmin, ymax - ymin, 1) * 0.07
        for ax in self.axes:
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlim(xmin - pad, xmax + pad)
            ax.set_ylim(ymin - pad, ymax + pad)
            ax.set_xlabel("Local X (m)")
            ax.grid(alpha=0.15, zorder=0)
        self.axes[0].set_ylabel("Local Y (m)")
        self.axes[0].set_title("1  Sources and transition regions", loc="left", fontsize=12, pad=14)
        self.axes[1].set_title("2  Skeleton and target lines", loc="left", fontsize=12, pad=14)
        self.axes[2].set_title("3  Top partitions", loc="left", fontsize=12, pad=14)

        for i, region in enumerate(preview.gap_regions, 1):
            _fill(self.axes[0], region, color="#dce3eb", alpha=0.8)
            p = region.representative_point()
            self.axes[0].text(p.x, p.y, f"R{i}", ha="center", va="center", color="#45556a",
                              fontsize=10, bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=2))
        for ax in self.axes[:2]:
            for key, polygon in preview.sources.items():
                _fill(ax, polygon, color=self.colors[key], alpha=0.55, edgecolor=self.colors[key], zorder=2)
                p = polygon.representative_point()
                ax.text(p.x, p.y, key, ha="center", va="center", fontsize=10, fontweight="bold", zorder=5)
            _stroke(ax, preview.hull.boundary, color=HULL_COLOR, linewidth=1.8, linestyle="--", zorder=4)
        for arc in preview.skeleton_arcs:
            _stroke(self.axes[1], arc.geometry, color=SKELETON_COLOR, linewidth=0.85, alpha=0.8, zorder=3)
        for target in preview.target_lines:
            color = MEETING_COLOR if target.kind == "meeting" else HULL_COLOR
            _stroke(self.axes[1], target.geometry, color=color, linewidth=2.1, zorder=4)

        self.partition_artists = []
        for key, partition in preview.partitions.items():
            self.partition_artists.extend(_fill(self.axes[2], partition, color=self.colors[key], alpha=0.6,
                                                edgecolor="white", linewidth=1.1, zorder=2))
            p = partition.representative_point()
            self.partition_artists.append(self.axes[2].text(p.x, p.y, key, ha="center", va="center",
                                                           fontsize=10, fontweight="bold", zorder=5))
            self.partition_artists.extend(_stroke(self.axes[2], preview.sources[key].boundary,
                                                  color=self.colors[key], linewidth=1.0, linestyle=":", zorder=3))
        self.merged_artists = _fill(self.axes[2], preview.hull, color=HULL_COLOR, alpha=0.22, zorder=2)
        p = preview.hull.representative_point()
        self.merged_artists.append(self.axes[2].text(p.x, p.y, "One aggregate\nconvex hull", ha="center",
                                                     va="center", color=HULL_COLOR, fontsize=13, zorder=5))
        for artist in self.merged_artists:
            artist.set_visible(False)
        _stroke(self.axes[2], preview.hull.boundary, color=HULL_COLOR, linewidth=2, zorder=4)

        handles = [Patch(facecolor=self.colors[key], alpha=0.6, label=key) for key in preview.sources]
        handles.extend([Line2D([], [], color=SKELETON_COLOR, lw=1, label="Skeleton trajectory"),
                        Line2D([], [], color=MEETING_COLOR, lw=2, label="Meeting target"),
                        Line2D([], [], color=HULL_COLOR, lw=2, label="Hull target")])
        self.fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.095), ncol=9,
                        frameon=False, fontsize=10)
        self.fig.text(0.045, 0.075,
                      f"UP: {preview.union.area:.2f} m²    CH − UP: {preview.gap.area:.2f} m²    "
                      f"CH: {preview.hull.area:.2f} m²    |    {len(preview.gap_regions)} transition regions",
                      fontsize=10, color="#364152")
        self.fig.text(0.045, 0.035,
                      "Top partitions cover CH without area overlaps. Only selected features participate; "
                      "use --scene aggregation to inspect the single-cell 3D model.", fontsize=9, color="#596270")
        self.checkbox = CheckButtons(self.fig.add_axes([0.79, 0.055, 0.18, 0.045]), ["Merged outline"], [False])
        self.checkbox.on_clicked(self._toggle_merged)
        self.fig.canvas.draw_idle()

    def _toggle_merged(self, _label):
        enabled = bool(self.checkbox.get_status()[0])
        for artist in self.partition_artists:
            artist.set_visible(not enabled)
        for artist in self.merged_artists:
            artist.set_visible(enabled)
        self.axes[2].set_title("3  Merged convex hull" if enabled else "3  Top partitions",
                              loc="left", fontsize=12, pad=14)
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()
