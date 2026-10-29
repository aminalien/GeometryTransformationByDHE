"""Publication-only settings, main-figure recipes, text editing and export.

This module neither constructs nor modifies DHE geometry. All recipes select
existing immutable snapshots from the standalone figure generator's repository.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import ctypes
import json
import math
import os
from pathlib import Path

import matplotlib as mpl
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.text import Annotation
from mpl_toolkits.mplot3d import proj3d


DEFAULTS = {
    "fonts": {"title":14., "panel_title":12., "panel_letter":12., "axis_label":11.,
              "tick":10., "legend":9., "name":11., "vertex":10., "edge":10.,
              "z_level":16., "z_axis":12., "footer":9.},
    "layout": {"width_in":10., "height_in":None, "plot_height_in":3.6,
               "legend_space_in":1.25, "left":.07, "right":.95, "top_in":.72,
               "bottom_in":.65, "wspace":.28, "hspace":.12,
               "three_d_width_ratio":1.6, "row_heights":None, "width_ratios":None,
               "show_title":True, "show_footer":True},
    "export": {"dpi":600., "size_mode":"physical", "width_px":None,
               "height_px":None, "memory_budget_mib":1024.},
    "view": {"zoom":1.05, "projection":"persp", "focal_length":1.,
             "axis_pad":5., "z_offset_points":14., "legend_loc":"upper center",
             "legend_columns":None, "aspect":None},
    "panels": {}, "annotations": {}, "views": {},
}


class PublicationSettings:
    """Mutable GUI state with a JSON-compatible, explicitly validated schema."""
    def __init__(self, values=None):
        self.values = deepcopy(DEFAULTS)
        for section, changes in (values or {}).items():
            if section not in DEFAULTS or not isinstance(changes,dict):
                raise ValueError(f"Unknown or invalid settings section: {section}")
            if section in {"panels","annotations","views"}:
                self.values[section] = deepcopy(changes)
            else:
                unknown = set(changes)-set(DEFAULTS[section])
                if unknown:
                    raise ValueError(f"Unknown {section} setting: {sorted(unknown)}")
                self.values[section].update(deepcopy(changes))
        self.validate()

    def __getitem__(self,key):
        return self.values[key]

    def validate(self):
        def positive(value,name):
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<=0:
                raise ValueError(f"{name} must be a finite positive number")
        for key,value in self["fonts"].items():
            positive(value,"font "+key)
        for key in ("width_in","plot_height_in","legend_space_in","top_in","bottom_in","three_d_width_ratio"):
            positive(self["layout"][key],key)
        if self["layout"]["height_in"] is not None:
            positive(self["layout"]["height_in"],"height_in")
        if not 0 <= self["layout"]["left"] < self["layout"]["right"] <= 1:
            raise ValueError("Layout margins must satisfy 0 ≤ left < right ≤ 1")
        for key in ("wspace","hspace"):
            if not isinstance(self["layout"][key],(int,float)) or not math.isfinite(self["layout"][key]) or self["layout"][key]<0:
                raise ValueError(f"{key} must be nonnegative")
        for key in ("row_heights","width_ratios"):
            values=self["layout"][key]
            if values is not None:
                if not isinstance(values,list) or not values:
                    raise ValueError(f"{key} must be a nonempty list or null")
                for value in values: positive(value,key)
        for key in ("dpi","memory_budget_mib"): positive(self["export"][key],key)
        if self["export"]["size_mode"] not in {"physical","pixels"}:
            raise ValueError("size_mode must be physical or pixels")
        for key in ("width_px","height_px"):
            v=self["export"][key]
            if v is not None and (not isinstance(v,int) or isinstance(v,bool) or v<1):
                raise ValueError(f"{key} must be a positive integer or null")
        if self["export"]["size_mode"]=="pixels" and all(self["export"][k] is None for k in ("width_px","height_px")):
            raise ValueError("Pixel sizing requires at least one pixel dimension")
        positive(self["view"]["zoom"],"zoom")
        if self["view"]["projection"] not in {"persp","ortho"}:
            raise ValueError("projection must be persp or ortho")
        positive(self["view"]["focal_length"],"focal_length")
        for mapping in (self["panels"],self["annotations"],self["views"]):
            if any(not isinstance(v,dict) for v in mapping.values()):
                raise ValueError("Panel, annotation and view overrides must be objects")

    @classmethod
    def load(cls,path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self,path):
        Path(path).write_text(json.dumps(self.values,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")

    def copy(self):
        return PublicationSettings(self.values)


@lru_cache(maxsize=1)
def require_times_new_roman():
    paths=[]
    try:
        for style,weight in (("normal","normal"),("italic","normal"),("normal","bold"),("italic","bold")):
            prop=font_manager.FontProperties(family="Times New Roman",style=style,weight=weight)
            path=font_manager.findfont(prop,fallback_to_default=False)
            if font_manager.get_font(path).family_name != "Times New Roman":
                raise ValueError("Resolved font is not Times New Roman")
            paths.append(path)
    except ValueError as exc:
        raise RuntimeError("Times New Roman (regular, italic, bold and bold italic) is required. Install it and restart; no substitute font was used.") from exc
    return tuple(paths)


def style(settings):
    require_times_new_roman()
    f=settings["fonts"]
    result={"font.family":"Times New Roman", "font.size":f["name"],
            "axes.titlesize":f["panel_title"],"axes.labelsize":f["axis_label"],
            "xtick.labelsize":f["tick"],"ytick.labelsize":f["tick"],"legend.fontsize":f["legend"],
            "svg.fonttype":"none","svg.hashsalt":"DHE-methodology","pdf.fonttype":42,
            "savefig.facecolor":"white","axes.unicode_minus":True,"text.usetex":False,
            "mathtext.fontset":"custom","mathtext.fallback":None}
    for key in ("rm","sf","tt","cal"): result["mathtext."+key]="Times New Roman"
    result.update({"mathtext.it":"Times New Roman:italic", "mathtext.bf":"Times New Roman:bold",
                   "mathtext.bfit":"Times New Roman:bold:italic"})
    return result


MAIN_CAPTIONS = {
    1: "Source–target selection in the local transition domain: (a) source and target geometries with affected neighbors; (b) the initial DHE cells and ordered source and target levels. The target specifies the intended representation, while the selected domain defines the boundaries considered during transformation.",
    2: "Conforming preparation of source–target boundaries: (a) initial geometric relationships; (b) active, common and unaffected regions; (c) boundary subdivision through the observed DHE operations; and (d) temporary active-region triangulation. The prepared configuration supplies compatible boundaries and ownership information for transition-facet planning.",
    3: "Oriented transition-facet plans spanning the source and target levels: (a) boundary-chain strips, (b) an apex fan, (c) strips meeting along a ridge, and (d) source-owned growth facets with an upper band. Compatible coordinates and incident-region assignments are established before DHE boundary realization.",
    4: "DHE boundary realization and integration: (a–c) complex-edge initialization, boundary-chain extension and face closure using MEVVFS, MEV and MEF; (d) within-cell sewing and matching neighboring interfaces; and (e) verified dual cell associations. The construction snapshots distinguish individual face assembly from subsequent cell-boundary connection.",
    5: "Final DHE realizations and representations extracted at intermediate and terminal transition levels. Each row shows the realized boundary, an intermediate horizontal section and the terminal representation. Lower-dimensional terminal geometries are extracted as point or ridge contacts rather than polygon caps; the aggregate terminates in its hull polygon.",
}


@dataclass(frozen=True)
class PanelItem:
    data: object
    panel: object
    key: str
    letter: str
    title: str


def main_items(repo,stage):
    if stage not in range(1,6): raise ValueError("Main figure stage must be 1–5")
    if stage in (1,2,4):
        data=repo.get("p2p")
        return tuple(PanelItem(data,p,p.key,chr(97+i),p.title) for i,p in enumerate(data.stages[stage-1].panels))
    if stage==3:
        items=[]
        for i,(case,title) in enumerate(zip(("p2p","p2pt","p2l","agg"),
                                            ("Boundary-chain strip","Apex fan","Polyline ridge","Aggregate growth and upper band"))):
            data=repo.get(case)
            key="ownership_interfaces" if case=="agg" else "lifted_facet_plan"
            p=next(p for p in data.stages[2].panels if p.key==key)
            items.append(PanelItem(data,p,case+":"+key,chr(97+i),title))
        return tuple(items)
    items=[]
    for case,label in (("p2p","P2P"),("p2pt","P2Pt"),("p2l","P2L"),("agg","AGG")):
        data=repo.get(case)
        for j,p in enumerate(data.stages[4].panels):
            title=label+" · Realized boundary" if j==0 else "Intermediate section" if j==1 else "Terminal representation"
            items.append(PanelItem(data,p,case+":"+p.key,chr(97+len(items)),title))
    return tuple(items)


def register_text(fig,artist,key,settings):
    artist.set_fontfamily("Times New Roman")
    fig._publication_artists[key]=artist
    artist.publication_id=key
    override=settings["annotations"].get(key,{})
    if isinstance(artist,Annotation):
        if "offset" in override: artist.set_position(override["offset"])
    elif "position" in override:
        artist.set_position(override["position"])
    for prop in ("ha","va","rotation"):
        if prop in override: getattr(artist,"set_"+prop)(override[prop])
    if key.endswith(("/xaxis_label","/yaxis_label")) and not isinstance(artist,Annotation):
        artist._publication_axis_label=key.rsplit("/",1)[1][0]
        if "axes_position" in override:
            getattr(artist.axes,artist._publication_axis_label+"axis").set_label_coords(*override["axes_position"])
    return artist


def annotate(ax,point,label,settings,role="name",color="#172033",offset=(3,7)):
    fig=ax.figure
    key=f"{fig._publication_key}/{ax.methodology_panel_key}/{role}:{label}"
    if hasattr(ax,"get_zlim"):
        projected=proj3d.proj_transform(*point,ax.get_proj())[:2]
    else:
        projected=point[:2]
    artist=ax.annotate(label,projected,xytext=offset,textcoords="offset points",annotation_clip=False,
                       fontsize=settings["fonts"][role],color=color,zorder=15,
                       bbox=dict(facecolor="white",edgecolor="none",alpha=.78,pad=.5))
    artist._publication_anchor3d=tuple(point) if hasattr(ax,"get_zlim") else None
    artist._publication_default_offset=tuple(offset)
    register_text(fig,artist,key,settings)
    if role=="z_level":
        # Restore the earlier Matplotlib mathematical lettering only for the
        # transition-level symbols; preserve sizes, offsets and all other text.
        artist.set_fontfamily("DejaVu Sans")
        artist.set_math_fontfamily("dejavusans")
    return artist


def update_projected(fig):
    for artist in fig._publication_artists.values():
        anchor=getattr(artist,"_publication_anchor3d",None)
        if anchor is not None:
            if hasattr(artist,"_publication_zlevel"):
                ax=artist.axes
                corners=[(x,y,artist._publication_zlevel) for x in ax.get_xlim() for y in ax.get_ylim()]
                anchor=max(corners,key=lambda p:proj3d.proj_transform(*p,ax.get_proj())[0])
            elif hasattr(artist,"_publication_axis3d"):
                ax=artist.axes; xl=ax.get_xlim(); yl=ax.get_ylim(); z=min(ax.get_zlim())
                candidates=([(sum(xl)/2,y,z) for y in yl] if artist._publication_axis3d=="x" else
                            [(x,sum(yl)/2,z) for x in xl])
                anchor=min(candidates,key=lambda p:proj3d.proj_transform(*p,ax.get_proj())[1])
            artist.xy=proj3d.proj_transform(*anchor,artist.axes.get_proj())[:2]
            if artist.publication_id not in fig._publication_settings["annotations"]:
                artist.set_position(artist._publication_default_offset)
                bbox=artist.get_window_extent(fig.canvas.get_renderer())
                dx=max(0,4-bbox.x0)-max(0,bbox.x1-fig.bbox.width+4)
                dy=max(0,4-bbox.y0)-max(0,bbox.y1-fig.bbox.height+4)
                if dx or dy:
                    x,y=artist.get_position()
                    artist.set_position((x+dx*72/fig.dpi,y+dy*72/fig.dpi))


def scoped_canvas(canvas,settings):
    original=canvas.draw
    def draw(*args,**kwargs):
        with mpl.rc_context(style(settings)):
            update_projected(canvas.figure)
            return original(*args,**kwargs)
    canvas.draw=draw


def resolve_size(settings,auto_height):
    s=settings["export"]; l=settings["layout"]
    w,h=l["width_in"],l["height_in"] or auto_height
    if s["size_mode"]=="pixels":
        wp,hp=s["width_px"],s["height_px"]
        if wp is None: wp=max(1,round(hp*w/h))
        if hp is None: hp=max(1,round(wp*h/w))
        w,h=wp/s["dpi"],hp/s["dpi"]
    return float(w),float(h)


def _layout(items,stage,main,settings):
    n=len(items); l=settings["layout"]; ratio=l["three_d_width_ratio"]
    if main and stage==5:
        return 4,3,[(i//3,i%3,1,1) for i in range(12)],[ratio,1,1],[1]*4
    if n==1: return 1,1,[(0,0,1,1)],[1],[1]
    if n==2:
        return 1,2,[(0,0,1,1),(0,1,1,1)],[1,ratio] if items[1].panel.default_3d else [1,1],[1]
    if n==3 and stage==5:
        return 2,2,[(0,0,2,1),(0,1,1,1),(1,1,1,1)],[ratio,1],[1,1]
    if n==3:
        return 2,2,[(0,0,1,1),(0,1,1,1),(1,0,1,2)],[1,ratio],[1,1.12]
    if n==5:
        return 2,6,[(0,0,1,2),(0,2,1,2),(0,4,1,2),(1,0,1,4),(1,4,1,2)],[1]*6,[1,1.25]
    return math.ceil(n/2),2,[(i//2,i%2,1,1) for i in range(n)],[1,1],[1]*math.ceil(n/2)


def render_items(items,stage,key,title,footer,options,settings,axes_views,draw_panel,main=False):
    settings=settings or PublicationSettings()
    settings.validate()
    l,f=settings["layout"],settings["fonts"]
    nr,nc,slots,wr,hr=_layout(items,stage,main,settings)
    wr=l["width_ratios"] or wr; hr=l["row_heights"] or hr
    if len(wr)!=nc or len(hr)!=nr:
        raise ValueError(f"This view requires {nc} width ratios and {nr} row heights")
    autoheight=l["top_in"]+l["bottom_in"]+nr*(l["plot_height_in"]+l["legend_space_in"])
    if main and stage==5: autoheight=14.
    w,h=resolve_size(settings,autoheight)
    with mpl.rc_context(style(settings)):
        preview_dpi=min(100.,1600./w,2400./h)
        fig=Figure(figsize=(w,h),dpi=preview_dpi)
        fig._publication_key=key
        fig._publication_artists={}
        fig._publication_settings=settings
        FigureCanvasAgg(fig)
        scoped_canvas(fig.canvas,settings)
        top=l["top_in"] if l["show_title"] else min(.25,l["top_in"])
        bottom=l["bottom_in"] if l["show_footer"] else min(.25,l["bottom_in"])
        shared_legend=main and stage==5 and options.legend
        if shared_legend: bottom+=.65
        if top+bottom>=h:
            raise ValueError("Figure height is too small for the configured header and footer")
        gs=fig.add_gridspec(nr,nc,left=l["left"],right=l["right"],top=1-top/h,bottom=bottom/h,
                            width_ratios=wr,height_ratios=hr,wspace=l["wspace"],hspace=l["hspace"])
        if l["show_title"]:
            register_text(fig,fig.text(.5,1-.12/h,title,ha="center",va="top",fontsize=f["title"]),key+"/title",settings)
        for item,slot in zip(items,slots):
            row,col,rs,cs=slot
            bbox=gs[row:row+rs,col:col+cs].get_position(fig)
            legend_space=min(l["legend_space_in"],bbox.height*h*.35) if options.legend and not shared_legend else min(.32,bbox.height*h*.15)
            title_pad=min(.22,bbox.height*h*.1)
            rect=[bbox.x0,bbox.y0+legend_space/h,bbox.width,bbox.height-(legend_space+title_pad)/h]
            override=settings["panels"].get(key+"/"+item.key,{})
            rect=override.get("rect",rect)
            if len(rect)!=4 or min(rect[2:])<=0:
                raise ValueError("Panel rect must be [left,bottom,width,height] with positive size")
            p=item.panel
            three="3D" in p.dimensions and (options.dimension=="3D" or options.dimension=="Auto" and p.default_3d)
            ax=fig.add_axes(rect,projection="3d" if three else None)
            ax.methodology_panel_key=item.key
            ax._publication_override=override
            import textwrap
            title_artist=ax.set_title(textwrap.fill(item.title,max(18,int(bbox.width*w*10))),loc="left",x=.10,
                                      fontsize=f["panel_title"],pad=12)
            register_text(fig,title_artist,key+"/"+item.key+"/panel_title",settings)
            register_text(fig,ax.text2D(-.02,1.05,f"({item.letter})",transform=ax.transAxes,
                          fontsize=f["panel_letter"],fontweight="bold") if three else
                          ax.text(-.02,1.05,f"({item.letter})",transform=ax.transAxes,
                          fontsize=f["panel_letter"],fontweight="bold"),key+"/"+item.key+"/panel_letter",settings)
            legends=draw_panel(ax,p,item.data,options,three,settings)
            view={**settings["views"].get(key,{}).get(item.key,{}),**override,**(axes_views or {}).get(item.key,{})}
            for name in ("xlim","ylim"):
                if name in view: getattr(ax,"set_"+name)(view[name])
            if three:
                if "zlim" in view: ax.set_zlim(view["zlim"])
                ax.view_init(view.get("elev",ax.elev),view.get("azim",ax.azim),roll=view.get("roll",ax.roll))
                aspect=view.get("aspect",settings["view"]["aspect"])
                ax.set_box_aspect(aspect or ax.get_box_aspect(),zoom=view.get("zoom",settings["view"]["zoom"]))
                ax.set_proj_type(settings["view"]["projection"],focal_length=settings["view"]["focal_length"]
                                 if settings["view"]["projection"]=="persp" else None)
            elif "aspect" in view:
                ax.set_aspect(view["aspect"])
            for name in ("xaxis","yaxis"):
                label=getattr(ax,name).label
                if three:
                    text=label.get_text(); label.set_visible(False)
                    label=annotate(ax,(0,0,0),text,settings,"axis_label",offset=(-8,-42) if name=="xaxis" else (24,-30))
                    fig._publication_artists.pop(label.publication_id)
                    label._publication_axis3d=name[0]
                register_text(fig,label,key+"/"+item.key+"/"+name+"_label",settings)
            if options.legend and legends and not shared_legend:
                columns=override.get("legend_columns",settings["view"]["legend_columns"]) or (2 if len(legends)>3 and bbox.width*w>2.8 else 1)
                chars=max(16,int(bbox.width*w*11/columns))
                import textwrap
                labels=[textwrap.fill(label,chars) for label in legends]
                anchor=override.get("legend_anchor",(bbox.x0+bbox.width/2,rect[1]-.37/h))
                legend=fig.legend(list(legends.values()),labels,loc=override.get("legend_loc",settings["view"]["legend_loc"]),
                                  bbox_to_anchor=anchor,bbox_transform=fig.transFigure,ncol=columns,
                                  frameon=False,fontsize=f["legend"],columnspacing=.8,labelspacing=.25)
                if "rect" not in override and "legend_anchor" not in override:
                    needed=legend.get_window_extent(fig.canvas.get_renderer()).height/fig.dpi+.43
                    delta=needed-legend_space
                    if rect[3]-delta/h>.3/h:
                        rect[1]+=delta/h; rect[3]-=delta/h; ax.set_position(rect)
                        legend.set_bbox_to_anchor((bbox.x0+bbox.width/2,rect[1]-.37/h),transform=fig.transFigure)
                lid=key+"/"+item.key+"/legend"
                fig._publication_artists[lid]=legend
                ov=settings["annotations"].get(lid,{})
                if "anchor" in ov:
                    legend.set_loc(ov.get("loc","lower left")); legend.set_bbox_to_anchor(ov["anchor"],transform=fig.transFigure)
        if shared_legend:
            from matplotlib.patches import Patch
            from matplotlib.lines import Line2D
            from methodology_figure_data import BLUE,GRAY,PURPLE,MAGENTA,COLORS
            handles=[Patch(facecolor=BLUE,alpha=.48),Patch(facecolor=GRAY,alpha=.48),
                     Patch(facecolor=PURPLE,alpha=.48),Line2D([],[],color="#334155",linestyle=":"),
                     *(Patch(facecolor=COLORS["Source "+letter],alpha=.5) for letter in "ABC"),
                     Patch(facecolor=MAGENTA,alpha=.62)]
            labels=["Source / aggregate cell","Neighboring cells","Separator sheet","Shared interface",
                    "Source A provenance","Source B provenance","Source C provenance","Terminal representation"]
            legend=fig.legend(handles,labels,loc="lower center",bbox_to_anchor=(.5,.28/h),ncol=4,
                              frameon=False,fontsize=f["legend"],columnspacing=1.)
            lid=key+"/shared_legend"; fig._publication_artists[lid]=legend
            ov=settings["annotations"].get(lid,{})
            if "anchor" in ov:
                legend.set_loc(ov.get("loc","lower left")); legend.set_bbox_to_anchor(ov["anchor"],transform=fig.transFigure)
        if footer and l["show_footer"]:
            import textwrap
            register_text(fig,fig.text(.5,.06/h,textwrap.fill(footer,max(70,int(w*13))),ha="center",va="bottom",fontsize=f["footer"]),key+"/footer",settings)
        update_projected(fig)
        return fig


def capture_views(fig):
    views={}
    for ax in fig.axes:
        value={"xlim":[float(x) for x in ax.get_xlim()],"ylim":[float(x) for x in ax.get_ylim()]}
        if hasattr(ax,"get_zlim"):
            value.update(zlim=[float(x) for x in ax.get_zlim()],elev=float(ax.elev),azim=float(ax.azim),roll=float(ax.roll))
        views[ax.methodology_panel_key]=value
    return views


class TextEditor:
    """Drag public text in display space; store offsets in points or native coordinates."""
    def __init__(self,fig,settings):
        self.fig,self.settings=fig,settings
        self.enabled=False; self.selected=None; self.active=None
        self.buttons={}
        self.callbacks=[fig.canvas.mpl_connect(name,callback) for name,callback in (
            ("button_press_event",self.press),("motion_notify_event",self.motion),("button_release_event",self.release))]

    def set_enabled(self,enabled):
        self.enabled=enabled
        for ax in self.fig.axes:
            if hasattr(ax,"disable_mouse_rotation"):
                if enabled:
                    self.buttons[ax]=(ax._rotate_btn,ax._pan_btn,ax._zoom_btn)
                    ax.disable_mouse_rotation()
                elif ax in self.buttons:
                    ax.mouse_init(*self.buttons.pop(ax))

    def press(self,event):
        if not self.enabled or event.button!=1 or event.x is None: return
        renderer=self.fig.canvas.get_renderer()
        for key,artist in reversed(list(self.fig._publication_artists.items())):
            if artist.get_visible() and artist.get_window_extent(renderer).contains(event.x,event.y):
                self.selected=key
                if hasattr(artist,"set_bbox_to_anchor"):
                    bbox=artist.get_window_extent(renderer)
                    origin=self.fig.transFigure.inverted().transform((bbox.x0,bbox.y0))
                    kind="legend"
                else:
                    origin=artist.get_position(); kind="annotation" if isinstance(artist,Annotation) else "text"
                    if hasattr(artist,"_publication_axis_label"):
                        origin=artist.axes.transAxes.inverted().transform(artist.get_transform().transform(origin))
                        kind="axis_label"
                self.active=(key,artist,(event.x,event.y),origin,kind)
                break

    def motion(self,event):
        if self.active is None or event.x is None: return
        key,artist,start,origin,kind=self.active
        dx,dy=event.x-start[0],event.y-start[1]
        override=self.settings["annotations"].setdefault(key,{})
        if kind=="legend":
            position=[float(origin[0]+dx/self.fig.bbox.width),float(origin[1]+dy/self.fig.bbox.height)]
            artist.set_loc("lower left"); artist.set_bbox_to_anchor(position,transform=self.fig.transFigure)
            override.update(anchor=position,loc="lower left")
        elif kind=="annotation":
            position=[float(origin[0]+dx*72/self.fig.dpi),float(origin[1]+dy*72/self.fig.dpi)]
            artist.set_position(position); override["offset"]=position
        elif kind=="axis_label":
            pixel=artist.axes.transAxes.transform(origin)
            position=artist.axes.transAxes.inverted().transform((pixel[0]+dx,pixel[1]+dy))
            getattr(artist.axes,artist._publication_axis_label+"axis").set_label_coords(*position)
            override["axes_position"]=[float(x) for x in position]
        else:
            pixel=artist.get_transform().transform(origin)
            position=artist.get_transform().inverted().transform((pixel[0]+dx,pixel[1]+dy))
            artist.set_position(position); override["position"]=[float(x) for x in position]
        self.fig.canvas.draw_idle()

    def release(self,event): self.active=None

    def disconnect(self):
        self.set_enabled(False)
        for cid in self.callbacks: self.fig.canvas.mpl_disconnect(cid)


def available_memory():
    try:
        if os.name=="nt":
            class Status(ctypes.Structure):
                _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in (
                    "total","available","page_total","page_available","virtual_total","virtual_available","extended")]
            status=Status(); status.length=ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)): return status.available
        elif "SC_AVPHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_AVPHYS_PAGES")*os.sysconf("SC_PAGE_SIZE")
    except (AttributeError,OSError,ValueError): pass
    return None


def raster_spec(fig,settings,dpi=None):
    dpi=float(settings["export"]["dpi"] if dpi is None else dpi)
    if not math.isfinite(dpi) or dpi<=0: raise ValueError("DPI must be a finite positive number")
    values=(float(fig.get_figwidth())*dpi,float(fig.get_figheight())*dpi)
    if not all(math.isfinite(v) for v in values):
        return {"dpi":dpi,"width_px":None,"height_px":None,"estimated_memory_mib":None,
                "reason":"Requested pixel dimensions overflow numeric representation; vector exports remain available."}
    width=max(1,round(values[0])); height=max(1,round(values[1]))
    if width>=2**23 or height>=2**23:
        return {"dpi":dpi,"width_px":width,"height_px":height,"estimated_memory_mib":None,
                "reason":"Requested dimensions exceed the Agg backend's per-axis limit; vector exports remain available."}
    estimated=width*height*12+32*1024**2
    budget=int(settings["export"]["memory_budget_mib"]*1024**2)
    available=available_memory()
    if available is not None: budget=min(budget,int(available*.7))
    reason=None
    if estimated>budget:
        reason=(f"Estimated raster memory {estimated/1024**2:.0f} MiB exceeds the current budget "
                f"{budget/1024**2:.0f} MiB. Reduce dimensions/DPI or adjust memory_budget_mib; vector exports remain available.")
    return {"dpi":dpi,"width_px":width,"height_px":height,"estimated_memory_mib":round(estimated/1024**2,1),"reason":reason}


def save_formats(fig,output,key,settings,dpi=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    spec=raster_spec(fig,settings,dpi)
    results={}; paths=[]
    with mpl.rc_context(style(settings)):
        update_projected(fig)
        for suffix in ("svg","pdf","png"):
            filename=key+"."+suffix; path=output/filename
            if suffix=="png" and spec["reason"]:
                results[suffix]={"status":"unavailable","reason":spec["reason"]}
                continue
            temp=output/(key+".partial."+suffix)
            meta={"Creator":"DHE Methodology Figure Generator"}
            if suffix=="svg": meta["Date"]=None
            if suffix=="pdf": meta.update(CreationDate=None,ModDate=None)
            try:
                # Tiny size adjustment makes the requested integer PNG dimensions exact.
                old=fig.get_size_inches().copy()
                if suffix=="png": fig.set_size_inches(math.nextafter(spec["width_px"]/spec["dpi"],math.inf),
                                                       math.nextafter(spec["height_px"]/spec["dpi"],math.inf),forward=False)
                fig.savefig(temp,dpi=spec["dpi"] if suffix=="png" else 100,metadata=meta)
                temp.replace(path)
                results[suffix]={"status":"exported","filename":filename}
                paths.append(path)
            except (MemoryError,ValueError,OverflowError) as exc:
                if suffix!="png": raise
                results[suffix]={"status":"unavailable","reason":type(exc).__name__+": "+str(exc)}
            finally:
                fig.set_size_inches(old,forward=False)
                if temp.exists(): temp.unlink()
    return tuple(paths),results,spec
