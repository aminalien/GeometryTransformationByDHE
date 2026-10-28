"""Standalone, publication-only DHE methodology viewer and reproducible exporter.

Run with the project's configured interpreter. Examples::

    python methodology_figure_generator.py
    python methodology_figure_generator.py --no-gui --export-all
    python methodology_figure_generator.py --no-gui --case p2pt --stage 5 --export

Production geometry and topology are computed in methodology_figure_data; this
module draws only immutable publication snapshots. No input Shapefiles needed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import textwrap

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
from shapely.geometry.polygon import orient

from methodology_figure_data import (
    BLUE, CASES, COLORS, GREEN, MAGENTA, OVERLAY_NOTE, PURPLE, STAGES,
    CaseData, Panel, build_case,
)

STYLE = {"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 9,
         "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
         "legend.fontsize": 7, "svg.fonttype": "none", "svg.hashsalt": "DHE-methodology",
         "pdf.fonttype": 42, "savefig.facecolor": "white", "axes.unicode_minus": True}
CAMERAS = {"Oblique": (26, -55), "Front": (14, -90), "Side": (15, 0), "Top": (90, -90)}


@dataclass(frozen=True)
class ViewOptions:
    dimension: str = "Auto"
    legend: bool = True
    outlines: bool = True
    labels: bool = True
    vertex_ids: bool = False
    edge_ids: bool = False
    primal: bool = True
    dual: bool = True
    ownership: bool = True
    camera: str = "Oblique"


def _parts(geometry):
    if geometry.is_empty:
        return
    if hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            yield from _parts(child)
    else:
        yield geometry


def _polygon_path(polygon):
    # Opposite ring directions preserve holes in vector and raster output.
    p = orient(polygon, 1)
    verts, codes = [], []
    for ring in (p.exterior, *p.interiors):
        xy = list(ring.coords)
        verts.extend(xy)
        codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(xy)-2) + [MplPath.CLOSEPOLY])
    return MplPath(verts, codes)


def capabilities(panels):
    return {
        "dimension": any("3D" in p.dimensions for p in panels),
        "outlines": any(s.group in {"source", "target"} for p in panels for s in p.shapes),
        "vertex_ids": any(p.facets or any(l.group != "dual" for l in p.lines) or
                          any(s.group == "source" for s in p.shapes) for p in panels),
        "edge_ids": any(p.facets or any(l.group != "dual" for l in p.lines) or
                        any(s.group == "source" for s in p.shapes) for p in panels),
        "primal": any(p.topology and (p.facets or any(l.group == "primal" for l in p.lines)) for p in panels),
        "dual": any(l.group == "dual" for p in panels for l in p.lines),
        "ownership": any(p.facets and len({f.provenance or f.owner for f in p.facets}) > 1 for p in panels),
        "camera": any("3D" in p.dimensions for p in panels),
    }


def selected_panels(stage, panel="combined"):
    if panel == "combined":
        return stage.panels
    return (next(p for p in stage.panels if p.key == panel),)


def _legend_shape(shape, three):
    if shape.geometry.geom_type in {"Point", "MultiPoint"}:
        return Line2D([], [], color=shape.color, marker="o", linestyle="", markersize=4)
    if shape.fill and "Polygon" in shape.geometry.geom_type and not three:
        return Patch(facecolor=shape.color, edgecolor="#475569", hatch=shape.hatch, alpha=.65)
    return Line2D([], [], color=shape.color, linestyle=shape.style, linewidth=1.5)


def _draw_panel(ax, panel, data, options, three):
    legends, annotations, used_text = {}, [], set()
    local_points, local_edges = set(), set()

    def text_at(point, label, color="#172033"):
        if not options.labels or label in used_text:
            return
        # Prefer a short callout above the geometry; avoid duplicate labels from multipart regions.
        used_text.add(label)
        if three:
            ax.text(*point, label, fontsize=7, color=color, zorder=10)
        else:
            annotation = ax.annotate(label, point[:2], xytext=(3, 7), textcoords="offset points",
                        fontsize=7, color=color, bbox=dict(facecolor="white", edgecolor="none", alpha=.8, pad=.6))
            annotations.append(annotation)

    def draw_line(coords, color, style="-", width=1.1, marker=None):
        p = np.asarray(coords)
        if not len(p):
            return
        if three:
            ax.plot(p[:,0],p[:,1],p[:,2],color=color,linestyle=style,linewidth=width,marker=marker,markersize=4)
        else:
            ax.plot(p[:,0],p[:,1],color=color,linestyle=style,linewidth=width,marker=marker,markersize=4,zorder=5)

    # Facet orientation is preserved in snapshots. Coincident opposite uses are drawn once.
    seen = set()
    for facet in panel.facets:
        key = tuple(sorted(facet.points))
        if key in seen:
            continue
        seen.add(key)
        owner = facet.provenance or facet.owner
        color = COLORS.get(owner, BLUE) if options.ownership else BLUE
        if facet.part == "separator":
            color = PURPLE
        alpha = (.13 if owner.startswith("N") else .48)
        if facet.part == "upper_cap":
            alpha = .18
        edges_on = options.primal if panel.topology else True
        linestyle = ":" if facet.shared else "-"
        if three:
            collection = Poly3DCollection([facet.points], facecolors=color, alpha=alpha,
                                         edgecolors="#334155" if edges_on else "none",
                                         linewidths=.55, linestyles=linestyle)
            ax.add_collection3d(collection)
        else:
            from shapely.geometry import Polygon
            projected = Polygon([p[:2] for p in facet.points])
            if projected.area > 1e-12:
                ax.add_patch(PathPatch(_polygon_path(projected), facecolor=color, alpha=alpha,
                                       edgecolor="#334155" if edges_on else "none", linewidth=.65, linestyle=linestyle))
            elif edges_on:
                draw_line(facet.points + (facet.points[0],), color, linestyle)
        local_points.update(facet.points)
        local_edges.update(tuple(sorted((a,b))) for a,b in zip(facet.points,facet.points[1:]+facet.points[:1]))
        legend_label = ("Separator sheet" if facet.part=="separator" else
                        owner + " (provenance)" if facet.provenance and owner != "Aggregate A" else owner)
        if not options.ownership:
            legend_label = "Realized DHE boundary" if panel.topology else "Planned facets"
        legends.setdefault(legend_label, Patch(facecolor=color, edgecolor="#475569", alpha=.5))
    if panel.facets and any(f.shared for f in panel.facets):
        legends["Shared interface" if panel.topology else "Paired interface plan"] = Line2D([],[],color="#334155",linestyle=":")

    # Draw the selected source after its neighbors so coincident neighbor boundaries
    # cannot hide the blue source outline.
    shapes = sorted(enumerate(panel.shapes), key=lambda pair:(
        2 if pair[1].group=="target" else 1 if pair[1].group=="source" and pair[1].label=="Source S" else 0,
        pair[0]))
    for _,shape in shapes:
        if shape.group in {"source", "target"} and not options.outlines:
            continue
        if shape.group == "dual" and not options.dual:
            continue
        if shape.group=="source" and shape.label.startswith("N"):
            legends.setdefault("Neighbor boundaries",Line2D([],[],color="#94a3b8",linewidth=1.4))
        else:
            legends.setdefault(shape.label, _legend_shape(shape, three))
        for part in _parts(shape.geometry):
            if part.geom_type == "Polygon":
                if three:
                    for ring in (part.exterior, *part.interiors):
                        draw_line(tuple((x,y,shape.z) for x,y,*_ in ring.coords),
                                  "#94a3b8" if shape.label.startswith("N") else shape.color, shape.style,1.45)
                elif shape.fill:
                    ax.add_patch(PathPatch(_polygon_path(part), facecolor=shape.color, edgecolor="#64748b",
                                           linewidth=.65, alpha=.62, hatch=shape.hatch, zorder=1))
                else:
                    for ring in (part.exterior, *part.interiors):
                        draw_line(tuple((x,y,shape.z) for x,y,*_ in ring.coords),
                                  "#94a3b8" if shape.label.startswith("N") else shape.color, shape.style,1.45)
                if shape.group in {"source", "target"}:
                    coords = tuple((x,y,shape.z) for x,y,*_ in list(part.exterior.coords)[:-1])
                    local_points.update(coords)
                    local_edges.update(tuple(sorted((a,b))) for a,b in zip(coords,coords[1:]+coords[:1]))
            elif part.geom_type in {"LineString", "LinearRing"}:
                draw_line(tuple((x,y,shape.z) for x,y,*_ in part.coords),shape.color,shape.style,2)
            elif part.geom_type == "Point":
                draw_line(((part.x,part.y,shape.z),),shape.color,"",marker="o")
        if shape.group in {"source", "target", "dual"}:
            p = shape.geometry.representative_point()
            if shape.label=="Source S" and not three:
                a,b,c,d = shape.geometry.bounds
                anchor = (a+.08*(c-a),d-.18*(d-b),shape.z)
            else:
                anchor = (p.x,p.y,shape.z)
            text_at(anchor,shape.label,"#475569" if shape.label.startswith("N") else shape.color)

    for line in panel.lines:
        if line.group == "dual" and not options.dual:
            continue
        if line.group == "primal" and not options.primal:
            continue
        draw_line(line.points,line.color,line.style,1.4 if line.group == "construction" else 1.,
                  "o" if line.group=="construction" and line.label in {"MEVVFS","MEV","MEF"} else None)
        legends.setdefault("Actual dual relation" if line.group == "dual" else line.label,
                           Line2D([],[],color=line.color,linestyle=line.style,linewidth=1.2))
        if line.group != "dual":
            local_points.update(line.points)
            local_edges.update(tuple(sorted((a,b))) for a,b in zip(line.points,line.points[1:]))

    if panel.key=="projected_facet_plan" and not three:
        # Show the stored vertex order of one nondegenerate facet plan. These
        # arrows are geometric winding, never claimed as realized half-edges.
        for facet in panel.facets:
            p = np.array(facet.points)[:,:2]
            area2 = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(p,np.roll(p,-1,axis=0)))
            if abs(area2)<1e-10:
                continue
            for a,b in zip(p,np.roll(p,-1,axis=0)):
                start,end = .68*a+.32*b,.34*a+.66*b
                ax.annotate("",end,xytext=start,arrowprops=dict(arrowstyle="->",color="#172033",lw=.8))
            legends["Facet-plan winding"] = Line2D([],[],color="#172033",marker=">",markersize=3)
            break

    if options.vertex_ids or options.edge_ids:
        # A legible deterministic subset, with stable names across stages; never v.i or edge.info.
        vertex_names, edge_names = dict(data.vertices), dict(data.edges)
        if options.vertex_ids:
            for p in sorted(local_points)[:18]:
                if p in vertex_names:
                    text_at(p,vertex_names[p],"#166534")
        if options.edge_ids:
            for edge in sorted(local_edges)[:12]:
                if edge in edge_names:
                    midpoint = tuple((a+b)/2 for a,b in zip(*edge))
                    text_at(midpoint,edge_names[edge],"#7e22ce")

    xmin,ymin,xmax,ymax = data.bounds
    if panel.key == "dual_associations":
        coords = [p for line in panel.lines for p in line.points]
        xmin,ymin = np.min(np.array(coords)[:,:2],axis=0)
        xmax,ymax = np.max(np.array(coords)[:,:2],axis=0)
    elif panel.key in {"mevvfs", "mev", "mef"}:
        coords = [p for line in panel.lines for p in line.points]
        xmin,ymin = np.min(np.array(coords)[:,:2],axis=0)
        xmax,ymax = np.max(np.array(coords)[:,:2],axis=0)
    span = max(xmax-xmin,ymax-ymin,1.)
    pad = span * .12
    ax.set_xlim(xmin-pad,xmax+pad)
    ax.set_ylim(ymin-pad,ymax+pad)
    ax.set_xlabel("x (local)")
    ax.set_ylabel("y (local)")
    if three:
        ax.set_zlim(-.08,2.15)
        ax.set_box_aspect((max(xmax-xmin,1),max(ymax-ymin,1),span*.72))
        ticks = [0,1,1.8,2] if data.case == "agg" else [0,1,2]
        ax.set_zticks(ticks, ["$z_b$","$z_s$","$z_m$","$z_t$"] if data.case == "agg" else ["$z_b$","$z_s$","$z_t$"])
        ax.set_zlabel("")
        ax.text2D(.95,.80,"$z$",transform=ax.transAxes,fontsize=8)
        ax.tick_params(axis="z",pad=1)
        ax.view_init(*CAMERAS[options.camera])
        for axis in (ax.xaxis,ax.yaxis,ax.zaxis):
            axis.pane.fill = False
        ax.grid(False)
    else:
        ax.set_aspect("equal",adjustable="box")
        ax.spines[["top","right"]].set_visible(False)
        # Place short public labels with measured bounding boxes, allowing callouts
        # rather than drawing overlapping source/target/neighbor names.
        ax.apply_aspect()
        renderer = ax.figure.canvas.get_renderer()
        occupied = []
        offsets = ((3,7),(3,-14),(-36,7),(-36,-14),(3,22),(-36,22),(12,-27),(-55,-27),(12,36))
        for annotation in annotations:
            for offset in offsets:
                annotation.set_position(offset)
                bbox = annotation.get_window_extent(renderer).expanded(1.08,1.15)
                if not any(bbox.overlaps(other) for other in occupied) and ax.bbox.contains(bbox.x0,bbox.y0) and ax.bbox.contains(bbox.x1,bbox.y1):
                    break
            occupied.append(bbox)
    return legends


def render(data: CaseData, stage_number=1, panel="combined", options=None, axes_views=None):
    options = options or ViewOptions()
    stage = data.stages[stage_number-1]
    panels = selected_panels(stage,panel)
    # Dedicated physical space for headers, plots, and legends prevents long
    # four-class legends or 3D labels from colliding with the next row/footer.
    construction_layout = len(panels)==5 and stage_number==4
    columns = 2 if len(panels)>1 else 1
    rows = 2 if construction_layout else (len(panels)+columns-1)//columns
    plot_height,legend_height = (2.65,1.05) if len(panels)>1 else (3.2,.85)
    band = plot_height + .4 + legend_height + .45
    height = 1.3 + rows*band + .5
    with matplotlib.rc_context(STYLE):
        fig = Figure(figsize=(7.2,height),layout=None)
        FigureCanvasAgg(fig)
        heading = f"Stage {stage.number} · {CASES[data.case].split(' — ')[0]}\n{stage.title}"
        fig.suptitle(textwrap.fill(heading.split('\n')[0],70)+"\n"+textwrap.fill(stage.title,74),fontsize=11,y=1-.12/height)
        for i,p in enumerate(panels):
            three = "3D" in p.dimensions and (options.dimension == "3D" or options.dimension == "Auto" and p.default_3d)
            if construction_layout:
                row,col,ncols = (0,i,3) if i<3 else (1,i-3,2)
            else:
                row,col,ncols = i//columns,i%columns,columns
            gap = .10 if ncols==2 else .065
            width = (.80-gap*(ncols-1))/ncols
            left = .09+col*(width+gap)
            top = height-1.3-row*band
            bottom = (top-plot_height)/height
            ax = fig.add_axes((left,bottom,width,plot_height/height),projection="3d" if three else None)
            ax.methodology_panel_key = p.key
            letter = chr(97+stage.panels.index(p))
            ax.set_title(f"({letter}) " + textwrap.fill(p.title,25 if ncols==3 else 34 if ncols==2 else 65),pad=12,
                         fontsize=8 if ncols==3 else 9)
            legends = _draw_panel(ax,p,data,options,three)
            view = (axes_views or {}).get(p.key)
            if view:
                ax.set_xlim(view["xlim"])
                ax.set_ylim(view["ylim"])
                if three and "zlim" in view:
                    ax.set_zlim(view["zlim"])
                    ax.view_init(view["elev"],view["azim"],roll=view["roll"])
            if options.legend and legends:
                legend_columns = 2 if len(legends)>3 and ncols<3 else 1
                legend_labels = [textwrap.fill(label,23 if legend_columns==2 else 31 if ncols==3 else 54)
                                 for label in legends]
                fig.legend(list(legends.values()),legend_labels,loc="upper center",
                           bbox_to_anchor=(left+width/2,(top-plot_height-.42)/height),
                           bbox_transform=fig.transFigure,ncol=legend_columns,
                           frameon=False,handlelength=1.7,columnspacing=.8,fontsize=6.5)
        footer = stage.annotation or "Actual computed states · Z denotes an ordered transition coordinate, not terrain elevation."
        if stage_number == 4:
            footer += "\nOperator panels are partial construction snapshots; subsequent sewing closes the cells."
        elif stage_number == 5:
            footer += "\nChecked: cycles/pointers, facet planarity, caps, volumes and terminal geometry."
        fig.text(.5,.025,textwrap.fill(footer,103),ha="center",va="bottom",fontsize=7)
        return fig


def caption(data, stage, panels):
    title = f"Stage {stage.number}: {stage.title} ({CASES[data.case].split(' — ')[0]})."
    pieces = [title]
    for p in panels:
        letter = chr(97+stage.panels.index(p))
        pieces.append(f"({letter}) {p.title}. {p.note}")
    pieces.append("All geometry and incidence shown are computed project outputs. Z is an ordered transition coordinate.")
    if stage.number == 5:
        pieces.append("Reported checks apply to this example; sampled sections are not a proof of universal 3D validity.")
    return " ".join(pieces)


def export_view(data, stage_number, panel="combined", output="methodology_figures", dpi=300, options=None, axes_views=None):
    if dpi not in {300,600}:
        raise ValueError("Publication PNG resolution must be 300 or 600 dpi")
    options = options or ViewOptions()
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    stage = data.stages[stage_number-1]
    panels = selected_panels(stage,panel)
    key = f"stage{stage_number}_{data.case}_{panel}"
    fig = render(data,stage_number,panel,options,axes_views)
    filenames = []
    with matplotlib.rc_context(STYLE):
        for suffix in ("svg","pdf","png"):
            filename = key+"."+suffix
            meta = {"Creator":"DHE Methodology Figure Generator"}
            if suffix == "svg":
                meta["Date"] = None
            elif suffix == "pdf":
                meta.update(CreationDate=None,ModDate=None)
            fig.savefig(output/filename,dpi=dpi,metadata=meta)
            filenames.append(filename)
    fig.clear()
    manifest_file = output/"figure_manifest.json"
    records = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else []
    record = {"key":key,"stage":stage_number,"stage_title":stage.title,"case":data.case,"panel":panel,
              "caption_key":key,"caption":caption(data,stage,panels),"status":stage.status,
              "source_routines":[p.routine for p in panels],
              "checks":sorted({c for p in panels for c in p.checks}),
              "png_dpi":dpi,"settings":asdict(options),"filenames":filenames}
    if axes_views:
        record["axes_views"] = axes_views
    records = [r for r in records if r["key"] != key]+[record]
    records.sort(key=lambda r:(r["case"],r["stage"],r["panel"]))
    manifest_file.write_text(json.dumps(records,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    (output/"figure_captions.md").write_text("# Methodology figure captions\n\n"+"\n\n".join(
        f"### {r['caption_key']}\n\n{r['caption']}" for r in records)+"\n",encoding="utf-8")
    return tuple(output/f for f in filenames)


class Repository:
    """Lazily compute each case once; retain only immutable publication data."""
    def __init__(self):
        self.cache = {}

    def get(self,case):
        if case not in self.cache:
            self.cache[case] = build_case(case)
        return self.cache[case]


def write_support_files(repo, output):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    lines = ["# Stage/case visualization status", "", "| Case | Stage | Status | Reason |", "|---|---|---|---|"]
    summary = []
    for case in CASES:
        try:
            data = repo.get(case)
        except Exception:
            # Do not leak exceptions containing private identifiers or local paths into publication metadata.
            for n in range(1,6):
                lines.append(f"| {case} | {n} | unavailable | Case capture failed; inspect the local application error. |")
            continue
        for stage in data.stages:
            lines.append(f"| {case} | {stage.number} | {stage.status} | {stage.reason} |")
        summary.append({"case":case,"public_labels":[label for label,_ in data.sources],
                        "cell_count":data.cell_count,"direct_operator_counts":dict(data.operations),
                        "terminal_type":dict(data.terminal)["Aggregate A" if case=="agg" else "Source S"].geom_type})
    (output/"visualization_status.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (output/"example_metadata.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    readme = Path(__file__).with_name("README_methodology_figures.md")
    if readme.exists():
        (output/readme.name).write_text(readme.read_text(encoding="utf-8"),encoding="utf-8")
    # Keep generated publication assets local under the project's standing workflow.
    ignore = output/".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n!.gitignore\n",encoding="utf-8")


def export_all(repo, output="methodology_figures", dpi=300, progress=None):
    completed, failures = [], []
    for case in CASES:
        try:
            data = repo.get(case)
        except Exception as exc:
            failures.append((case,"capture",type(exc).__name__))
            continue
        for stage in data.stages:
            for key in ("combined", *(p.key for p in stage.panels)):
                try:
                    paths = export_view(data,stage.number,key,output,dpi)
                    completed.append((case,stage.number,key))
                    if progress:
                        progress(f"Exported {paths[0].stem}")
                except Exception as exc:
                    failures.append((case,f"stage{stage.number}/{key}",type(exc).__name__))
    write_support_files(repo,output)
    Path(output,"export_report.json").write_text(json.dumps({"exported_views":len(completed),
                    "failures":failures},indent=2)+"\n",encoding="utf-8")
    return completed,failures


class MethodologyApp:
    """Native dropdowns avoid Matplotlib widget mouse-grab conflicts."""
    def __init__(self,root,repo=None,output="methodology_figures",case="p2p",stage=1):
        import tkinter as tk
        from tkinter import ttk
        self.tk,self.ttk,self.root = tk,ttk,root
        self.repo,self.output = repo or Repository(),output
        self.root.title("DHE Methodology Figure Generator")
        self.root.geometry("1260x920")
        self.case_var = tk.StringVar(value=CASES[case])
        self.stage_var = tk.StringVar(value=f"{stage}. {STAGES[stage-1]}")
        self.panel_var = tk.StringVar(value="Combined panels")
        self.dimension = tk.StringVar(value="Auto")
        self.camera = tk.StringVar(value="Oblique")
        self.dpi = tk.StringVar(value="300")
        self.vars = {name:tk.BooleanVar(value=getattr(ViewOptions(),name)) for name in
                     ("legend","outlines","labels","vertex_ids","edge_ids","primal","dual","ownership")}
        controls = ttk.Frame(root,padding=8)
        controls.pack(side="top",fill="x")
        self.case_box = self._combo(controls,"Case",self.case_var,list(CASES.values()),26,0)
        self.stage_box = self._combo(controls,"Stage",self.stage_var,[f"{i+1}. {s}" for i,s in enumerate(STAGES)],78,1)
        self.panel_box = self._combo(controls,"Panel",self.panel_var,["Combined panels"],50,2)
        self.dimension_box = self._combo(controls,"View",self.dimension,["Auto","2D","3D"],8,3)
        self.camera_box = self._combo(controls,"Camera",self.camera,list(CAMERAS),10,4)
        self.widgets = {}
        toggles = ttk.Frame(root,padding=(8,0))
        toggles.pack(fill="x")
        labels = {"legend":"Legend","outlines":"Source/target outlines","labels":"Publication-safe labels",
                  "vertex_ids":"Local vertex IDs","edge_ids":"Local edge IDs","primal":"Primal DHE edges",
                  "dual":"Actual dual relations","ownership":"Ownership/provenance"}
        for i,(key,label) in enumerate(labels.items()):
            widget = ttk.Checkbutton(toggles,text=label,variable=self.vars[key],command=self.refresh)
            widget.grid(row=i//4,column=i%4,sticky="w",padx=9,pady=3)
            self.widgets[key] = widget
        exports = ttk.Frame(root,padding=8)
        exports.pack(fill="x")
        ttk.Label(exports,text="PNG dpi").pack(side="left")
        ttk.Combobox(exports,textvariable=self.dpi,values=("300","600"),width=6,state="readonly").pack(side="left",padx=6)
        ttk.Button(exports,text="Export current view",command=self.export_current).pack(side="left",padx=6)
        ttk.Button(exports,text="Export All",command=self.export_everything).pack(side="left",padx=6)
        self.status = tk.StringVar()
        ttk.Label(exports,textvariable=self.status,wraplength=600).pack(side="left",padx=8)
        self.description = tk.Text(root,height=5,wrap="word",font=("Segoe UI",9),padx=10,pady=5)
        self.description.pack(side="bottom",fill="x")
        self.holder = ttk.Frame(root)
        self.holder.pack(fill="both",expand=True)
        self.scroll = tk.Canvas(self.holder,highlightthickness=0,background="white")
        bar = ttk.Scrollbar(self.holder,orient="vertical",command=self.scroll.yview)
        bar.pack(side="right",fill="y")
        self.scroll.configure(yscrollcommand=bar.set)
        self.scroll.pack(fill="both",expand=True)
        self.plot_frame = ttk.Frame(self.scroll)
        self.plot_window = self.scroll.create_window((0,0),window=self.plot_frame,anchor="n")
        self.plot_frame.bind("<Configure>",lambda e:self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>",lambda e:self.scroll.coords(self.plot_window,e.width/2,0))
        self.scroll.bind("<MouseWheel>",lambda e:self.scroll.yview_scroll(-int(e.delta/120),"units"))
        self.canvas = None
        self.toolbar = None
        self.figure = None
        self.refresh()

    def _combo(self,parent,label,variable,values,width,row):
        self.ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",padx=(0,8),pady=2)
        box = self.ttk.Combobox(parent,textvariable=variable,values=values,width=width,state="readonly")
        box.grid(row=row,column=1,sticky="w",pady=2)
        box.bind("<<ComboboxSelected>>",self.refresh)
        return box

    def selection(self):
        case = next(k for k,v in CASES.items() if v == self.case_var.get())
        number = int(self.stage_var.get().split(".",1)[0])
        data = self.repo.get(case)
        stage = data.stages[number-1]
        key = self.panel_keys.get(self.panel_var.get(),"combined") if hasattr(self,"panel_keys") else "combined"
        if key != "combined" and key not in {p.key for p in stage.panels}:
            key = "combined"
        return data,stage,key

    def options(self):
        return ViewOptions(dimension=self.dimension.get(),camera=self.camera.get(),
                           **{k:v.get() for k,v in self.vars.items()})

    def refresh(self,event=None):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        try:
            data,stage,key = self.selection()
            self.panel_keys = {"Combined panels":"combined",**{f"({chr(97+i)}) {p.title}":p.key for i,p in enumerate(stage.panels)}}
            self.panel_box.configure(values=list(self.panel_keys))
            self.panel_var.set(next(k for k,v in self.panel_keys.items() if v == key))
            panels = selected_panels(stage,key)
            caps = capabilities(panels)
            for name,widget in self.widgets.items():
                applicable = caps.get(name,True) and (name not in {"vertex_ids","edge_ids"} or self.vars["labels"].get())
                widget.configure(state="normal" if applicable else "disabled")
            self.dimension_box.configure(state="readonly" if caps["dimension"] else "disabled")
            self.camera_box.configure(state="readonly" if caps["camera"] and self.dimension.get() != "2D" else "disabled")
            new_figure = render(data,stage.number,key,self.options())
            if self.canvas:
                self.canvas.toolbar = None
            if self.toolbar:
                self.toolbar.destroy()
            if self.canvas:
                self.canvas.get_tk_widget().destroy()
            if self.figure:
                self.figure.clear()
            self.figure = new_figure
            self.canvas = FigureCanvasTkAgg(new_figure,master=self.plot_frame)
            self.toolbar = NavigationToolbar2Tk(self.canvas,self.holder,pack_toolbar=False)
            self.toolbar.pack(side="bottom",fill="x")
            self.canvas.get_tk_widget().pack()
            self.canvas.draw()
            unavailable = [k for k,v in caps.items() if not v]
            text = caption(data,stage,panels)+"\nSources: "+"; ".join(dict.fromkeys(p.routine for p in panels))
            text += "\nChecks: "+"; ".join(sorted({c for p in panels for c in p.checks}))
            if unavailable:
                text += "\nDisabled controls have no applicable data in this view: "+", ".join(unavailable)+"."
            self.description.configure(state="normal")
            self.description.delete("1.0","end")
            self.description.insert("1.0",text)
            self.description.configure(state="disabled")
            self.status.set(f"Actual computed state · {data.cell_count} final cell(s)")
        except Exception as exc:
            self.status.set("Data not captured / unavailable: "+str(exc))
            self.description.configure(state="normal")
            self.description.delete("1.0","end")
            self.description.insert("1.0","Data not captured / unavailable for this selection. No result has been substituted.\n"+str(exc))
            self.description.configure(state="disabled")
            if self.figure:
                self.figure.clear()
                self.figure.text(.5,.5,"Data not captured / unavailable",ha="center")
                self.canvas.draw_idle()

    def export_current(self):
        from tkinter import messagebox
        try:
            data,stage,key = self.selection()
            views = {}
            for ax in self.figure.axes:
                state = {"xlim":list(ax.get_xlim()),"ylim":list(ax.get_ylim())}
                if hasattr(ax,"get_zlim"):
                    state.update(zlim=list(ax.get_zlim()),elev=float(ax.elev),azim=float(ax.azim),roll=float(ax.roll))
                views[ax.methodology_panel_key] = state
            paths = export_view(data,stage.number,key,self.output,int(self.dpi.get()),self.options(),views)
            write_support_files(self.repo,self.output)
            self.status.set(f"Saved {paths[0].stem}: SVG, PDF and PNG")
        except Exception as exc:
            messagebox.showerror("Export failed",str(exc))

    def export_everything(self):
        self.status.set("Exporting verified combined and individual views…")
        self.root.update_idletasks()
        def progress(message):
            self.status.set(message)
            self.root.update_idletasks()
        done,failures = export_all(self.repo,self.output,int(self.dpi.get()),progress)
        self.status.set(f"Exported {len(done)} views; {len(failures)} failures. See export_report.json.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case",choices=CASES,default="p2p")
    parser.add_argument("--stage",type=int,choices=range(1,6),default=1)
    parser.add_argument("--panel",default="combined",help="Panel key from the manifest, or combined")
    parser.add_argument("--output",default="methodology_figures")
    parser.add_argument("--dpi",type=int,choices=(300,600),default=300)
    parser.add_argument("--no-gui",action="store_true")
    parser.add_argument("--export",action="store_true")
    parser.add_argument("--export-all",action="store_true")
    args = parser.parse_args(argv)
    repo = Repository()
    if args.export_all:
        done,failures = export_all(repo,args.output,args.dpi,print)
        print(f"Exported {len(done)} views; {len(failures)} failures")
        return 1 if failures else 0
    if args.export:
        for path in export_view(repo.get(args.case),args.stage,args.panel,args.output,args.dpi):
            print(path)
        write_support_files(repo,args.output)
    if args.no_gui:
        if not args.export:
            for case in CASES:
                data = repo.get(case)
                print(case,[(s.number,s.status,len(s.panels)) for s in data.stages])
            write_support_files(repo,args.output)
        return 0
    import tkinter as tk
    root = tk.Tk()
    MethodologyApp(root,repo,args.output,args.case,args.stage)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
