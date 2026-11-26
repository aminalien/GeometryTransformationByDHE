"""Render publication files from an immutable web scene's horizontal sections."""

from io import BytesIO
from tempfile import SpooledTemporaryFile
from threading import Lock
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from plane_slicing import SlicePlane
from web_generalization_scene import rgba
from web_space_export import find_export_scene


class SliceExport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    workspace_name: Literal['generalize', 'mapping']
    snapshot_id: str
    start_z: float
    end_z: float
    count: int = Field(ge=1, le=50, strict=True)
    format: Literal['png', 'jpeg', 'pdf', 'svg']


# Matplotlib font and renderer state is shared across FastAPI's worker threads.
_render_lock = Lock()


def slice_elevations(request, bounds):
    lower, upper = bounds
    if not lower <= request.start_z <= request.end_z <= upper:
        raise ValueError(f'Enter a Z range within {lower:g}–{upper:g} m.')
    if request.count > 1 and request.start_z == request.end_z:
        raise ValueError('End Z must be greater than Start Z for multiple slices.')
    return np.linspace(request.start_z, request.end_z, request.count)


def section_figure(scene, z):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import LineCollection, PatchCollection
    from matplotlib.figure import Figure
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    from shapely.geometry.polygon import orient

    figure = Figure(figsize=(8, 6), dpi=180, facecolor='white')
    FigureCanvasAgg(figure)
    ax = figure.add_subplot()
    ax.set_position((.13, .13, .81, .76))
    ax.set_title(f'Horizontal section  ·  Z = {np.format_float_positional(z, unique=True, trim="-")} m',
                 loc='left', color='#153c3d', pad=13)
    ax.set_xlabel('Local X (m)')
    ax.set_ylabel('Local Y (m)')
    lower, upper = scene.slicer.minimum[:2], scene.slicer.maximum[:2]
    pad = max(1., float(max(upper - lower)) * .055)
    ax.set(xlim=(lower[0]-pad, upper[0]+pad), ylim=(lower[1]-pad, upper[1]+pad),
           aspect='equal')
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(colors='#365352', labelsize=8)
    ax.grid(color='#e2e9e7', linewidth=.6)
    ax.set_axisbelow(True)

    polygons, lines, line_colors, points, point_colors = [], [], [], [], []
    for owner, geometry in scene.section_parts(SlicePlane.horizontal(z)):
        color = np.array(rgba(owner)[:3]) / 255
        edge = color * .66
        if geometry.geom_type == 'Polygon':
            polygon = orient(geometry, sign=1)
            paths = [Path(np.asarray(ring.coords), closed=True)
                     for ring in (polygon.exterior, *polygon.interiors)]
            polygons.append(PathPatch(Path.make_compound_path(*paths),
                                      facecolor=(*color, .52), edgecolor=edge, linewidth=.9))
        elif geometry.geom_type == 'Point':
            points.append((geometry.x, geometry.y))
            point_colors.append(color)
        else:
            lines.append(np.asarray(geometry.coords))
            line_colors.append(edge)
    if polygons:
        ax.add_collection(PatchCollection(polygons, match_original=True), autolim=False)
    if lines:
        ax.add_collection(LineCollection(lines, colors=line_colors, linewidths=1.6, zorder=3),
                          autolim=False)
    if points:
        x, y = zip(*points)
        ax.scatter(x, y, c=point_colors, s=27, edgecolors='#263d3e', linewidths=.5, zorder=4)
    if not (polygons or lines or points):
        ax.text(.5, .5, 'No geometry at this Z', transform=ax.transAxes,
                ha='center', va='center', color='#365352')
    return figure


def render_slices(scene, values, output, file_format):
    from matplotlib import rc_context
    from matplotlib.backends.backend_pdf import PdfPages

    with _render_lock, rc_context({'svg.fonttype': 'none', 'pdf.fonttype': 42}):
        if file_format == 'pdf':
            with PdfPages(output, metadata={'Title': 'LoD Transition Space · Horizontal sections'}) as pdf:
                for z in values:
                    figure = section_figure(scene, z)
                    pdf.savefig(figure)
                    figure.clear()
            return
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            for index, z in enumerate(values, 1):
                figure = section_figure(scene, z)
                with BytesIO() as page:
                    figure.savefig(page, format=file_format)
                    extension = 'jpg' if file_format == 'jpeg' else file_format
                    archive.writestr(f'slice_{index:02d}.{extension}', page.getvalue())
                figure.clear()


def attach_figure_export_routes(app, workspace_dependency):
    @app.post('/api/export/slices')
    def download_slices(request: SliceExport, workspace=Depends(workspace_dependency)):
        scene, _ = find_export_scene(workspace, request.workspace_name, request.snapshot_id)
        if scene is None:
            raise HTTPException(409, 'The displayed snapshot expired or changed. Refresh before exporting.')
        values = slice_elevations(request, scene.payload['z_bounds'])
        output = SpooledTemporaryFile(max_size=4 * 1024 * 1024)
        try:
            render_slices(scene, values, output, request.format)
            output.seek(0)
        except BaseException:
            output.close()
            raise
        extension = 'pdf' if request.format == 'pdf' else 'zip'
        media_type = 'application/pdf' if extension == 'pdf' else 'application/zip'
        name = f'dhe_{request.workspace_name}_slices_{request.snapshot_id[:8]}.{extension}'
        headers = {'Content-Disposition': f'attachment; filename="{name}"', 'Content-Encoding': 'identity'}
        return StreamingResponse(iter(lambda: output.read(65536), b''), media_type=media_type,
                                 headers=headers, background=BackgroundTask(output.close))
