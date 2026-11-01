"""Shared presentation of 3D geometry; never constructs or modifies topology."""
from math import isfinite


def levels_from_record(record, *, bottom=None):
    """Read recorded levels; do not infer stages from arbitrary vertex Zs."""
    def get(name):return record.get(name) if isinstance(record,dict) else getattr(record,name,None)
    levels={name:get(name) for name in ('z_b','z_s','z_m','z_t')} if record is not None else {}
    if bottom is not None:levels['z_b']=bottom
    return clean_levels(levels)


def levels_from_layer(layer):
    return clean_levels({key:getattr(layer,name,None) for key,name in (
        ('z_b','bottom_z'),('z_s','source_z'),('z_m','merge_z'),('z_t','target_z'))})


def clean_levels(levels):
    return {name:float(value) for name,value in (levels or {}).items()
            if name in ('z_b','z_s','z_m','z_t') and value is not None and isfinite(value)}


def guide_available(levels):
    levels=clean_levels(levels)
    return 'z_s' in levels and 'z_t' in levels and levels['z_t']>levels['z_s']


def style_3d(ax,levels=None,*,show_axes=True,show_z_transition=False,tick_size=None,guide_size=None):
    """White panes, no grid, numeric transition Zs, and an optional screen guide.

    Returns guide labels so publication tools can register/edit their positions.
    Calling repeatedly replaces the guide rather than accumulating artists.
    Camera, axis limits, geometry artists and face colours are untouched.
    """
    levels=clean_levels(levels)
    for artist in getattr(ax,'_dhe_z_guide',()):
        try:artist.remove()
        except ValueError:pass  # ax.clear() already removed it
    ax._dhe_z_guide=[]
    ax._dhe_levels=levels;ax._dhe_show_axes=bool(show_axes);ax._dhe_show_z_transition=bool(show_z_transition)
    ax.figure.set_facecolor('white');ax.set_facecolor('white');ax.grid(False)
    for axis in (ax.xaxis,ax.yaxis,ax.zaxis):
        axis.set_pane_color((1.,1.,1.,1.));axis.pane.set_edgecolor('white')
        axis.pane.fill=True
    ax.set_xlabel('X');ax.set_ylabel('Y');ax.set_zlabel('Z')
    ax.set_xticks([]);ax.set_yticks([])
    lo,hi=sorted(ax.get_zlim())
    values=sorted({v for v in levels.values() if lo<=v<=hi})
    ax.set_zticks(values)
    ax.set_zticklabels([f'{v:g}' for v in values],**({'fontsize':tick_size} if tick_size is not None else {}))
    height_points=max(1.,ax.get_window_extent().height*72/ax.figure.dpi)
    previous=None;column=0
    for value,tick in zip(values,ax.zaxis.get_major_ticks()):
        size=tick_size if tick_size is not None else tick.label1.get_fontsize()
        crowded=previous is not None and (value-previous)/max(hi-lo,1e-15)<size*1.2/(height_points*.7)
        column=column+1 if crowded else 0
        tick.set_pad(3.5+column*max(6.,size*.8));previous=value
    if show_axes:ax.set_axis_on()
    else:ax.set_axis_off()
    has_guide=show_z_transition and guide_available(levels)
    # The guide carries the same values: avoid two crowded numeric columns in
    # small multi-panel figures while retaining the coordinate axis and ticks.
    for tick in ax.zaxis.get_major_ticks():tick.label1.set_visible(not has_guide)
    if not has_guide:return []
    bottom=min(levels.values());top=max(levels.values())
    def y(value):return .16+.65*(value-bottom)/(top-bottom)
    family=ax.zaxis.label.get_fontfamily()
    size=guide_size if guide_size is not None else tick_size if tick_size is not None else ax.zaxis.label.get_fontsize()
    def line(a,b,**kwargs):
        artist=ax.annotate('',xy=b,xytext=a,xycoords=ax.transAxes,
            arrowprops=dict(arrowstyle='-',color='#404040',linewidth=.8)|kwargs,annotation_clip=False)
        ax._dhe_z_guide.append(artist)
    # Keep the guide on the opposite side from Matplotlib's default Z axis.
    line((.055,y(bottom)),(.055,y(top)))
    labels=[]
    ordered=sorted(set(levels.values()));groups=[]
    for value in ordered:
        if groups and y(value)-y(groups[-1][-1])<size*1.2/height_points:groups[-1].append(value)
        else:groups.append([value])
    shifts={v:(i-(len(group)-1)/2)*size*1.05 if len(group)>1 else 0.
            for group in groups for i,v in enumerate(group)}
    for value in ordered:
        line((.043,y(value)),(.068,y(value)))
        label=ax.annotate(f'{value:g}',xy=(.072,y(value)),xycoords=ax.transAxes,xytext=(3,shifts[value]),
            textcoords='offset points',va='center',ha='left',fontsize=size,fontfamily=family,
            color='#303030',annotation_clip=False,zorder=20,
            bbox=dict(facecolor='white',edgecolor='none',alpha=.92,pad=.15),
            arrowprops=dict(arrowstyle='-',color='#606060',linewidth=.5) if shifts[value] else None)
        label._dhe_z_value=value;labels.append(label);ax._dhe_z_guide.append(label)
    line((.015,y(levels['z_s'])),(.015,y(levels['z_t'])),arrowstyle='->',linewidth=1.2)
    return labels
