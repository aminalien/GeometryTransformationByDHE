"""Read-only publication views of validated generalization history.

No DHE operators or global topology restoration are used here. Display clipping
and cut caps are illustrations; sections always come from the full stored shell.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math

import matplotlib as mpl
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split, unary_union

from generalization_geometry import feature_color
from methodology_publication import PublicationSettings, save_formats, style
from plane_slicing import PlaneSlicer, SlicePlane, _face_frame
from shell_conformity import arithmetic_bound


@dataclass(frozen=True)
class Boundary:
    owner: int
    points: tuple
    part: str


@dataclass(frozen=True)
class TransitionSnapshot:
    label: str
    faces: tuple[Boundary, ...]
    operation: dict
    affected: tuple[int, ...]
    names: tuple[tuple[int, str], ...]

    @classmethod
    def capture(cls, state, preceding=()):
        if state.operation is None:
            raise ValueError("Choose a validated operation; initial extrusion has no transition interval.")
        record=asdict(state.operation)
        owners=set(record['affected'])
        # Include earlier contributing cells in full-history views, including
        # intermediate aggregate IDs which are not original lineage members.
        for old in reversed([*preceding,state]):
            if old.operation and old.operation.winner in owners:
                owners.update(old.operation.selected)
        names={k:f.label for old in [*preceding,state] for k,f in old.features.items()}
        faces=tuple(Boundary(f.owner,tuple(tuple(p) for p in f.points),f.part) for f in state.faces)
        return cls(state.description,faces,record,tuple(sorted(owners)),tuple(sorted(names.items())))

    def slicer(self):
        return PlaneSlicer.from_face_records((str(f.owner),f.points) for f in self.faces)


@dataclass(frozen=True)
class ViewSettings:
    scope: str = 'affected'
    extent: str = 'interval'
    z_b: float | None = None
    hidden: tuple[int, ...] = ()
    opacity: float = .40
    elevation: float = 25.
    azimuth: float = -60.
    zoom: float = 1.
    exaggeration: float = 1.
    axis_limits: tuple | None = None
    outlines: bool = True
    legend: bool = True
    cut_caps: bool = True
    plane_visible: bool = False
    orientation: str = 'Horizontal'
    value: float | None = None
    tilt: float = 45.
    plane_azimuth: float = 0.

    def validate(self):
        if self.scope not in ('affected','all') or self.extent not in ('interval','history'):
            raise ValueError('Unknown feature scope or vertical extent.')
        if not all(math.isfinite(v) for v in (self.opacity,self.elevation,self.azimuth,self.zoom,self.exaggeration,self.tilt,self.plane_azimuth)):
            raise ValueError('View settings must be finite.')
        if not 0<=self.opacity<=1 or self.zoom<=0 or self.exaggeration<=0 or not 0<=self.tilt<=90:
            raise ValueError('Opacity must be 0–1, zoom/exaggeration positive and tilt 0–90.')
        if self.orientation not in ('Horizontal','Vertical','Inclined'):
            raise ValueError('Unknown plane orientation.')
        if any(v is not None and not math.isfinite(v) for v in (self.z_b,self.value)):
            raise ValueError('Plane level and Zb must be finite.')
        if self.axis_limits is not None:
            if len(self.axis_limits)!=3 or any(len(pair)!=2 or not all(math.isfinite(x) for x in pair) or pair[0]>=pair[1] for pair in self.axis_limits):
                raise ValueError('Axis limits must be three finite increasing pairs.')


def polygon_parts(geometry):
    if geometry.geom_type=='Polygon':
        if not geometry.is_empty: yield geometry
    elif hasattr(geometry,'geoms'):
        for child in geometry.geoms: yield from polygon_parts(child)


def view_bounds(snapshot, options):
    options.validate()
    minimum=min(p[2] for f in snapshot.faces for p in f.points)
    lower=minimum if options.extent=='history' else snapshot.operation['z_b'] if options.z_b is None else options.z_b
    upper=snapshot.operation['z_t']
    if not minimum<=lower<=snapshot.operation['z_s']:
        raise ValueError(f'Zb must lie in [{minimum:g}, {snapshot.operation["z_s"]:g}].')
    return lower,upper


def visible_owners(snapshot, options):
    present={f.owner for f in snapshot.faces}
    owners=present if options.scope=='all' else present.intersection(snapshot.affected)
    return owners-set(options.hidden)


def clip_ring(points, lower, upper):
    """Clip a planar, possibly concave face in its own frame (never fan it)."""
    arr=np.asarray(points)
    if arr[:,2].max()<lower or arr[:,2].min()>upper:return []
    if arr[:,2].min()>=lower and arr[:,2].max()<=upper:return [tuple(points)]
    frame=_face_frame(points)
    origin=frame.points[0]
    gradient=np.array([frame.u[2],frame.v[2]])
    length=np.linalg.norm(gradient)
    if length<1e-14:return []
    components=[frame.polygon]
    direction=np.array([-gradient[1],gradient[0]])/length
    for level,sign in ((lower,1),(upper,-1)):
        next_parts=[]
        for poly in components:
            xy=np.array(poly.exterior.coords)
            levels=origin[2]+xy@gradient
            if min(sign*(levels-level))>=0:next_parts.append(poly);continue
            if max(sign*(levels-level))<0:continue
            center=np.array(poly.representative_point().coords[0])
            on_line=center+gradient*(level-origin[2]-center@gradient)/(length*length)
            span=np.max(np.linalg.norm(xy-on_line,axis=1))+1.
            pieces=split(poly,LineString([on_line-direction*span,on_line+direction*span]))
            for piece in polygon_parts(pieces):
                at=np.array(piece.representative_point().coords[0])
                if sign*(origin[2]+at@gradient-level)>=0:next_parts.append(piece)
        components=next_parts
    result=[]
    for poly in components:
        xy=np.array(poly.exterior.coords[:-1])
        xyz=origin+xy[:,0,None]*frame.u+xy[:,1,None]*frame.v
        bound=arithmetic_bound(points)
        for level in (lower,upper):xyz[np.abs(xyz[:,2]-level)<=bound,2]=level
        result.append(tuple(map(tuple,xyz)))
    return result


def draw_geometry(ax, geometry, color, *, plane=None, alpha=.4, linewidth=1.3):
    """Render holes, components and true lower-dimensional terminal contacts."""
    if geometry.is_empty:return
    kind=geometry.geom_type
    if kind=='Polygon':
        polygon=orient(geometry,1)
        if plane is None:
            vertices=[];codes=[]
            for ring in [polygon.exterior,*polygon.interiors]:
                xy=list(ring.coords)
                vertices.extend(xy);codes.extend([MplPath.MOVETO]+[MplPath.LINETO]*(len(xy)-2)+[MplPath.CLOSEPOLY])
            ax.add_patch(PathPatch(MplPath(vertices,codes),facecolor=color,edgecolor=color,alpha=alpha,lw=linewidth))
        else:
            # Triangulate only a display polygon with holes, never the DHE model.
            pieces=list(constrained_delaunay_triangles(polygon).geoms) if polygon.interiors else [polygon]
            ax.add_collection3d(Poly3DCollection([plane.lift(np.array(p.exterior.coords[:-1])) for p in pieces],
                facecolors=color,edgecolors='none',alpha=alpha))
            for ring in [polygon.exterior,*polygon.interiors]:
                xyz=plane.lift(np.array(ring.coords));ax.plot(*xyz.T,color=color,lw=linewidth)
    elif kind in ('LineString','LinearRing','Point'):
        xy=np.array(geometry.coords)
        if plane is not None:xy=plane.lift(xy)
        ax.plot(*xy.T,color=color,lw=linewidth,marker='o' if kind=='Point' else None,ms=5)
    elif hasattr(geometry,'geoms'):
        for child in geometry.geoms:draw_geometry(ax,child,color,plane=plane,alpha=alpha,linewidth=linewidth)


def selected_sections(slicer, plane, owners):
    return {int(k):g for k,g in slicer.sections(plane,include_degenerate=True).items() if int(k) in owners}


def render_scene(ax,snapshot,slicer,options,settings,*,plane=None):
    lower,upper=view_bounds(snapshot,options)
    owners=visible_owners(snapshot,options)
    by_owner={};points=[];horizontal={}
    for f in snapshot.faces:
        if f.owner not in owners:continue
        for ring in clip_ring(f.points,lower,upper):
            by_owner.setdefault(f.owner,[]).append(ring);points.extend(ring)
            for level in (lower,upper):
                if all(p[2]==level for p in ring):
                    horizontal.setdefault((f.owner,level),[]).append(Polygon([p[:2] for p in ring]))
    if not points:raise ValueError('No visible geometry in this interval. Show at least one contributing feature.')
    for owner,rings in by_owner.items():
        ax.add_collection3d(Poly3DCollection(rings,facecolors=feature_color(owner),edgecolors=feature_color(owner),
            alpha=options.opacity,linewidths=.5))
    if options.cut_caps:
        for level in (lower,upper):
            cut=SlicePlane.horizontal(level)
            for owner,geometry in selected_sections(slicer,cut,owners).items():
                actual=unary_union(horizontal.get((owner,level),[]))
                remainder=geometry.difference(actual)
                draw_geometry(ax,remainder,feature_color(owner),plane=cut,alpha=options.opacity)
    if options.outlines:
        for level in (snapshot.operation['z_s'],upper):
            cut=SlicePlane.horizontal(level)
            for owner,geometry in selected_sections(slicer,cut,owners).items():
                boundary=geometry.boundary if geometry.geom_type in ('Polygon','MultiPolygon') else geometry
                draw_geometry(ax,boundary,feature_color(owner),plane=cut,linewidth=1.8)
    arr=np.asarray(points);lo,hi=arr.min(axis=0),arr.max(axis=0)
    span=np.maximum(hi-lo,1.);middle=(hi+lo)/2
    for axis,i in ((ax.set_xlim,0),(ax.set_ylim,1)):
        half=span[i]*.55/options.zoom;axis(middle[i]-half,middle[i]+half)
    ax.set_zlim(lower-(upper-lower)*.025,upper+(upper-lower)*.04)
    # Z denotes transition progression, not a metric terrain height. Balance its
    # display length against the XY domain; the explicit factor adjusts it.
    ax.set_box_aspect((span[0],span[1],max(span[:2])*.8*options.exaggeration))
    if settings['view']['aspect'] is not None:ax.set_box_aspect(settings['view']['aspect'])
    ax.set_proj_type(settings['view']['projection'],focal_length=settings['view']['focal_length'] if settings['view']['projection']=='persp' else None)
    if options.axis_limits is not None:
        ax.set_xlim(options.axis_limits[0]);ax.set_ylim(options.axis_limits[1]);ax.set_zlim(options.axis_limits[2])
    ax.view_init(elev=options.elevation,azim=options.azimuth)
    ax.set(xlabel='Local X',ylabel='Local Y',zlabel='Transition Z')
    ax.tick_params(labelsize=settings['fonts']['tick'])
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(nbins=2));ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
    for axis in (ax.xaxis,ax.yaxis):axis.get_major_formatter().set_useOffset(False)
    levels=sorted({lower,snapshot.operation['z_s'],upper})
    ax.set_zticks(levels)
    ax.set_zticklabels([f'{z:g}' for z in levels],fontsize=settings['fonts']['tick'])
    if plane is not None:
        # Outline is fitted to the visible domain, not unrelated distant cells.
        visible=object.__new__(PlaneSlicer);visible.minimum=lo;visible.maximum=hi
        outline=visible.plane_outline(plane)
        if len(outline):ax.add_collection3d(Poly3DCollection([outline],facecolors='purple',edgecolors='purple',alpha=.12))
        for owner,g in selected_sections(slicer,plane,owners).items():
            draw_geometry(ax,g,feature_color(owner),plane=plane,alpha=.65,linewidth=2.)
    return lo,hi


def current_plane(snapshot,slicer,options):
    lower,upper=view_bounds(snapshot,options)
    if options.orientation=='Horizontal':
        value=snapshot.operation['z_s'] if options.value is None else options.value
        if not lower<=value<=upper:raise ValueError('Horizontal slicing value must lie inside the displayed interval.')
        return SlicePlane.horizontal(value)
    owners=visible_owners(snapshot,options)
    points=np.array([p for f in snapshot.faces if f.owner in owners for p in f.points])
    center=(points.min(axis=0)+points.max(axis=0))/2;center[2]=(lower+upper)/2
    return SlicePlane.from_angles(center,90. if options.orientation=='Vertical' else options.tilt,
                                  options.plane_azimuth,0. if options.value is None else options.value)


def add_legend(fig,snapshot,options,settings):
    if not options.legend:return
    names=dict(snapshot.names)
    owners=sorted(visible_owners(snapshot,options))
    handles=[Line2D([],[],color=feature_color(k),lw=3,label=names.get(k,f'Feature {k}')) for k in owners]
    fig.legend(handles=handles,loc=settings['view']['legend_loc'],ncol=settings['view']['legend_columns'] or min(6,max(1,len(handles))),
               fontsize=settings['fonts']['legend'],frameon=False)


def make_figure(settings, *, grid=False):
    settings.validate()
    fig=Figure(figsize=(settings['layout']['width_in'],settings['layout']['height_in'] or (8. if grid else 6.)))
    fig._publication_artists={}
    FigureCanvasAgg(fig)
    if settings['export']['size_mode']=='pixels':
        dpi=settings['export']['dpi'];w=settings['export']['width_px'];h=settings['export']['height_px']
        aspect=fig.get_figheight()/fig.get_figwidth()
        width=w/dpi if w else h/dpi/aspect;height=h/dpi if h else width*aspect
        fig.set_size_inches(width,height)
    return fig


def default_publication_settings():
    return PublicationSettings({'view':{'legend_loc':'lower center'},'layout':{'top_in':.6,'bottom_in':.8,'hspace':.4}})


def apply_layout(fig,settings,*,grid=False):
    height=fig.get_figheight()
    top_in=settings['layout']['top_in']
    if settings['view']['legend_loc'].startswith('upper'):top_in=max(top_in,1.)
    if top_in+settings['layout']['bottom_in']>=height:
        raise ValueError('Canvas height is too small for the configured top/bottom margins. Increase height or pixel dimensions, lower DPI, or reduce the margins.')
    fig.subplots_adjust(left=settings['layout']['left'],right=settings['layout']['right'],
        top=1-top_in/height,bottom=settings['layout']['bottom_in']/height,
        wspace=settings['layout']['wspace'],hspace=max(.3,settings['layout']['hspace']) if grid else settings['layout']['hspace'])


def render_transition(snapshot,options=None,settings=None,*,with_section=False,slicer=None):
    options=options or ViewSettings();settings=settings or default_publication_settings();slicer=slicer or snapshot.slicer()
    with mpl.rc_context(style(settings)):
        fig=make_figure(settings)
        if with_section:
            grid=fig.add_gridspec(1,2,width_ratios=settings['layout']['width_ratios'] or [settings['layout']['three_d_width_ratio'],1]);ax=fig.add_subplot(grid[0,0],projection='3d');section=fig.add_subplot(grid[0,1])
        else:ax=fig.add_subplot(111,projection='3d');section=None
        plane=current_plane(snapshot,slicer,options)
        render_scene(ax,snapshot,slicer,options,settings,plane=plane if options.plane_visible else None)
        if settings['layout']['show_title']:ax.set_title(snapshot.label,fontsize=settings['fonts']['panel_title'])
        if section is not None:
            for owner,g in selected_sections(slicer,plane,visible_owners(snapshot,options)).items():
                draw_geometry(section,g,feature_color(owner))
            section.autoscale_view();section.set_aspect('equal');section.set_title('Cross-section',fontsize=settings['fonts']['panel_title'])
            section.set(xlabel='Local X' if options.orientation=='Horizontal' else 'Plane u',ylabel='Local Y' if options.orientation=='Horizontal' else 'Plane v')
            section.ticklabel_format(useOffset=False)
        add_legend(fig,snapshot,options,settings)
        apply_layout(fig,settings)
        return fig


def render_slices(snapshot,count=9,kind='2d',options=None,settings=None,*,levels=None,slicer=None):
    if count not in (6,9):raise ValueError('Choose six or nine slice levels.')
    if kind not in ('2d','3d'):raise ValueError('Choose 2d or 3d slices.')
    options=options or ViewSettings();settings=settings or default_publication_settings();slicer=slicer or snapshot.slicer()
    view_bounds(snapshot,options)
    zs,zt=snapshot.operation['z_s'],snapshot.operation['z_t']
    levels=list(np.linspace(zs,zt,count)) if levels is None else list(levels)
    owners=visible_owners(snapshot,options)
    points=np.array([p for f in snapshot.faces if f.owner in owners for p in f.points])
    if not len(points):raise ValueError('No visible features.')
    lo,hi=points[:,:2].min(axis=0),points[:,:2].max(axis=0);padding=np.maximum((hi-lo)*.05,.2)
    with mpl.rc_context(style(settings)):
        fig=make_figure(settings,grid=True)
        rows=1 if len(levels)==1 else count//3;columns=1 if len(levels)==1 else 3
        for i,z in enumerate(levels):
            plane=SlicePlane.horizontal(float(z))
            ax=fig.add_subplot(rows,columns,i+1,projection='3d' if kind=='3d' else None)
            if kind=='3d':
                render_scene(ax,snapshot,slicer,options,settings,plane=plane)
                if len(levels)>1:
                    if i//columns!=rows-1:ax.set_xlabel('');ax.set_xticklabels([])
                    if i%columns!=0:ax.set_ylabel('');ax.set_yticklabels([])
                    if i%columns!=columns-1:ax.set_zlabel('');ax.set_zticklabels([])
            else:
                for owner,g in selected_sections(slicer,plane,owners).items():draw_geometry(ax,g,feature_color(owner))
                ax.set(xlim=(lo[0]-padding[0],hi[0]+padding[0]),ylim=(lo[1]-padding[1],hi[1]+padding[1]),xlabel='Local X',ylabel='Local Y')
                ax.set_aspect('equal');ax.ticklabel_format(useOffset=False);ax.tick_params(labelsize=settings['fonts']['tick'])
                from matplotlib.ticker import MaxNLocator
                ax.xaxis.set_major_locator(MaxNLocator(nbins=2));ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
                if len(levels)>1:ax.label_outer()
            letter=i if len(levels)>1 else int(np.argmin(np.abs(np.linspace(zs,zt,count)-z)))
            ax.set_title(f'({chr(97+letter)}) Z = {z:g}',fontsize=settings['fonts']['panel_title'])
        if settings['layout']['show_title']:fig.suptitle(snapshot.label,fontsize=settings['fonts']['title'])
        add_legend(fig,snapshot,options,settings)
        apply_layout(fig,settings,grid=True)
        return fig


def export_transition(snapshot,output,*,options=None,settings=None,count=9,kind='2d',individual=False,content='scene',slicer=None):
    """Export vector and raster views plus reproducible settings/level metadata."""
    options=options or ViewSettings();settings=settings or default_publication_settings();slicer=slicer or snapshot.slicer()
    lower,upper=view_bounds(snapshot,options)
    if content not in ('scene','slices'):raise ValueError('Export content must be scene or slices.')
    if content=='slices' and count not in (6,9):raise ValueError('Choose six or nine slice levels.')
    levels=[float(z) for z in np.linspace(snapshot.operation['z_s'],upper,count)] if content=='slices' else []
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    stem=f"{snapshot.operation['name']}_zs{snapshot.operation['z_s']:g}_zt{upper:g}_{options.scope}_{options.extent}"
    results=[];paths=[]
    def save(figure,key):
        try:
            files,formats,spec=save_formats(figure,output,key,settings)
            paths.extend(files);results.append(dict(key=key,formats=formats,raster=spec))
        finally:
            figure.clear()
            if hasattr(figure.canvas,'renderer'):del figure.canvas.renderer
    if content=='scene':save(render_transition(snapshot,options,settings,slicer=slicer),stem+'_3d')
    else:
        save(render_slices(snapshot,count,kind,options,settings,slicer=slicer),stem+f'_{kind}_{count}slices')
        if individual:
            for i,z in enumerate(levels):
                save(render_slices(snapshot,count,kind,options,settings,levels=[z],slicer=slicer),stem+f'_{kind}_slice{i+1:02d}')
    record=dict(description=snapshot.label,operation=snapshot.operation,affected=snapshot.affected,
        visible_owners=sorted(visible_owners(snapshot,options)),display_interval=[lower,upper],slice_levels=levels,
        geometry_source='Validated realized face records; sections from the complete shell.',
        display_caps='Clipped-boundary closing surfaces are visualization only.',
        view=asdict(options),publication=settings.values,exports=results)
    manifest=output/(stem+('_3d' if content=='scene' else f'_{kind}_{count}slices')+'.json')
    manifest.write_text(json.dumps(record,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    return tuple(paths),record
