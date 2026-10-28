"""Vector figure export without moving live artists or touching DHE globals."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import matplotlib.pyplot as plt
import numpy as np

from DHECore import face_edges
from paper_plot import draw_stage_geometry, draw_stage_lines, draw_xy, frame_axes, highlight_xy, highlight_region, surface
from paper_session import camera_state, restore_camera
from paper_stages import TRANSFORMATIONS


def _revision():
    root = Path(__file__).resolve().parent
    try:
        result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
                                 cwd=root, capture_output=True, text=True, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _copy_dhe_scene(ax, viewer):
    for record in viewer.scene_faces.values():
        artist = record["artist"]
        if artist is not None and artist.get_visible():
            surface(ax, record["points"], artist.get_facecolor()[0], artist.get_alpha())
    if viewer.face_artist is not None and viewer.face_artist.get_visible():
        points = [(e.V.x, e.V.y, e.V.z) for e in face_edges(viewer.e)]
        surface(ax, points, viewer.face_artist.get_facecolor()[0], 0.5, edges=False)
    for line in viewer.ax.lines:
        if line.get_visible() and hasattr(line, "get_data_3d"):
            ax.plot(*line.get_data_3d(), color=line.get_color(), alpha=line.get_alpha(),
                    lw=line.get_linewidth(), ls=line.get_linestyle(), marker=line.get_marker(),
                    markersize=line.get_markersize(), zorder=line.get_zorder())
    for artist in viewer.dual_vertex_artists:
        if artist.get_visible() and hasattr(artist, "_offsets3d"):
            ax.scatter(*artist._offsets3d, color=artist.get_facecolor(), s=artist.get_sizes())
    for text in viewer.ax.texts:
        if text.get_visible() and hasattr(text, "get_position_3d"):
            ax.text(*text.get_position_3d(), text.get_text(), color=text.get_color(), fontsize=text.get_fontsize())
    controls = getattr(viewer, "plane_controls", None)
    if controls and controls.show_slicing_3d:
        surface(ax, controls.slicer.plane_outline(controls.current_plane), "0.65", 0.14)


def _section(ax, controls):
    ax.set_aspect("equal", adjustable="box")
    for owner, geometry in controls.current_sections.items():
        for part in controls._parts(geometry):
            color = controls.colors[owner]
            if part.geom_type == "Polygon":
                from shapely.geometry.polygon import orient
                ax.add_patch(controls._path_patch(orient(part, 1), color))
                for ring in (part.exterior, *part.interiors):
                    xy = np.asarray(ring.coords)
                    ax.plot(xy[:, 0], xy[:, 1], color=color, lw=1)
            else:
                xy = np.asarray(part.coords)
                ax.plot(xy[:, 0], xy[:, 1], color=color, lw=2,
                        marker="o" if part.geom_type == "Point" else None)
    ax.set_xlim(controls.ax.get_xlim())
    ax.set_ylim(controls.ax.get_ylim())
    ax.set_xlabel(controls.ax.get_xlabel())
    ax.set_ylabel(controls.ax.get_ylabel())
    ax.set_title(f"{controls.orientation} section | {controls.position_slider.label.get_text()} = {controls.position_slider.val:g}")
    ax.grid(alpha=0.12)


def export_paper_figure(session, *, directory="artifacts/paper_figures", section=False):
    stage, viewer = session.stage, session.viewer
    controls = getattr(viewer, "plane_controls", None)
    if section and controls is None:
        raise ValueError("Cross-section export is available on final DHE models only.")
    root = Path(directory) / stage.transformation
    root.mkdir(parents=True, exist_ok=True)
    index = session.step_dropdown.options.index(session.step_dropdown.value)+1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stem = root / f"{index:02d}-{stage.spec.id}-{'section' if section else 'figure'}-{stamp}"
    serial = 1
    while any(stem.with_suffix(ext).exists() for ext in (".png", ".svg", ".json")):
        stem = root / f"{index:02d}-{stage.spec.id}-{stamp}-{serial}"
        serial += 1
    camera = camera_state(viewer.ax)
    fig = plt.figure(figsize=(8, 7) if section else (14, 7))
    try:
        if section:
            _section(fig.add_subplot(111), controls)
        else:
            ax = fig.add_axes([0.015, 0.07, 0.49, 0.83], projection="3d")
            xy = fig.add_axes([0.57, 0.11, 0.40, 0.75])
            frame_axes(ax, stage.bounds)
            if stage.model:
                _copy_dhe_scene(ax, viewer)
            else:
                draw_stage_geometry(ax, stage)
                draw_stage_lines(ax, stage)
                if viewer.overlay_panel.selection:
                    points, _, color = viewer.overlay_panel.selection
                    surface(ax, points, color, 0.5)
            restore_camera(ax, camera)
            draw_xy(xy, stage)
            if viewer.overlay_panel.selection:
                points, edge, color = viewer.overlay_panel.selection
                highlight_xy(xy, points, edge, color)
            if viewer.overlay_panel.region_selection:
                geometry, color, z, label = viewer.overlay_panel.region_selection
                highlight_region(ax, xy, geometry, color, z)
            xy.set_xlim(viewer.overlay_panel.ax.get_xlim())
            xy.set_ylim(viewer.overlay_panel.ax.get_ylim())
        fig.suptitle(f"{TRANSFORMATIONS[stage.transformation]} — {stage.spec.title}\n{stage.kind}", fontsize=13)
        png, svg, settings = (stem.with_suffix(ext) for ext in (".png", ".svg", ".json"))
        fig.savefig(png, dpi=300, facecolor="white")
        fig.savefig(svg, format="svg", facecolor="white")
        data = dict(stage.metadata, transformation=stage.transformation, stage_id=stage.spec.id,
                    stage_title=stage.spec.title, kind=stage.kind, bounds=list(stage.bounds),
                    camera=camera, slicing=controls.snapshot_state() if controls else None,
                    section_only=section, revision=_revision(), colors=stage.colors)
        if viewer.overlay_panel.selection:
            points, edge, color = viewer.overlay_panel.selection
            data["projection_selection"] = dict(points=points, edge_index=edge, color=color)
        if viewer.overlay_panel.region_selection:
            geometry, color, z, label = viewer.overlay_panel.region_selection
            data["region_selection"] = dict(geometry=geometry.__geo_interface__, color=color, z=z, label=label)
        if stage.model:
            data.update(current_edge=viewer.e.info, current_cell=str(viewer.e.D.V.i),
                        current_face_fill=viewer.current_face_fill_enabled,
                        shaded_features=sorted(viewer.shaded_features),
                        dual_vertices=viewer.show_dual_vertices, dual_edges=viewer.show_dual_edges,
                        lod_labels=viewer.show_lod_labels)
        settings.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                                       default=lambda value: value.item() if isinstance(value, np.generic) else list(value)), encoding="utf-8")
        return png, svg, settings
    finally:
        plt.close(fig)
