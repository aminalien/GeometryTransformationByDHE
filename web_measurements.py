"""Small, immutable measurements of prepared XY geometry, independent of rendering."""
from math import isfinite
from collections import Counter


def geometry_summary(geometries):
    """Inputs use the application's projected metre CRS. Ring closures aren't vertices."""
    geometries=list(geometries)
    counts=Counter(g.geom_type for g in geometries)
    area=perimeter=0.
    vertices=0

    def visit(g):
        nonlocal area,perimeter,vertices
        if g.is_empty:return
        if hasattr(g,'geoms'):
            for part in g.geoms:visit(part)
        elif g.geom_type=='Polygon':
            area+=g.area;perimeter+=g.length
            vertices+=len(g.exterior.coords)-1
            vertices+=sum(len(r.coords)-1 for r in g.interiors)
        else:vertices+=len(g.coords)

    for geometry in geometries:visit(geometry)
    return dict(features=len(geometries),geometry_types=dict(counts),vertices=vertices,
                polygon_area=area if isfinite(area) else None,
                polygon_perimeter=perimeter if isfinite(perimeter) else None)


def comparison(before,after,*,scope,before_label,after_label):
    differences={}
    for key in ('features','vertices','polygon_area','polygon_perimeter'):
        a,b=before[key],after[key]
        differences[key]=dict(absolute=b-a if a is not None and b is not None else None,
                              percent=100*(b-a)/a if a not in (None,0) and b is not None else None)
    return dict(scope=scope,before_label=before_label,after_label=after_label,before=before,after=after,
                changes=differences,units=dict(polygon_area='m²',polygon_perimeter='m'))


def layer_comparison(layers):
    if not all(side in layers for side in ('source','target')):return None
    return comparison(*(geometry_summary(f.geometry for f in layers[side].features.values()) for side in ('source','target')),
                      scope='Prepared source and target layers',before_label='Source',after_label='Target')
