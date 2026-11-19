"""Segment-site medial-axis aggregation, used only after skeleton failure.

Voronoi cells and their generating sites define an outward circular wavefront.
Its radius is lifted into Z; no source/target boundary-distance ratio is used.
Original input coordinates are retained in the realized boundary. Integer
coordinates belong only to the Boost construction and have a measured bound.
"""
from collections import defaultdict
from dataclasses import dataclass
from math import ceil, hypot, isfinite, log10, sqrt

import numpy as np
from shapely import constrained_delaunay_triangles, union_all, STRtree
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

from aggregation_geometry import polygon_parts, line_parts
from generalization_model import Face, _node_faces
from shell_conformity import CoordinateRegistry, TransitionShellError, arithmetic_bound


@dataclass(frozen=True)
class MedialSite:
    segment: int
    category: int
    points: tuple
    source_ids: tuple

    def radius(self, xy):
        a = self.points[0]; dx,dy=xy[0]-a[0],xy[1]-a[1]
        if len(self.points) == 1:
            return hypot(dx,dy)
        b=self.points[1];ux,uy=b[0]-a[0],b[1]-a[1]
        t = min(1., max(0., (dx*ux+dy*uy)/(ux*ux+uy*uy)))
        return hypot(dx-t*ux,dy-t*uy)


def _curve(pv, edge, vertices, epsilon):
    """A shared polyline with an analytic parabolic chord-error bound."""
    a, b = vertices[edge.start], vertices[edge.end]
    if edge.is_linear:
        return [a, b], 0.
    focus, segment = pv.ReturnCurvedSiteInformation(edge)
    origin = np.asarray(segment[0]); u = np.subtract(segment[1], origin)
    u /= np.linalg.norm(u); normal = np.array([-u[1], u[0]])
    fx, fy = float((np.asarray(focus)-origin)@u), float((np.asarray(focus)-origin)@normal)
    if fy == 0:
        raise ValueError('A curved medial edge has a focus on its directrix.')
    xa, xb = float((np.asarray(a)-origin)@u), float((np.asarray(b)-origin)@u)
    # y(x)=((x-fx)^2+fy^2)/(2*fy); maximum chord deviation is dx^2/(8*abs(fy)).
    count = max(1, ceil(abs(xb-xa)/sqrt(8*abs(fy)*epsilon)))
    if count > 100000:
        raise ValueError('Medial parabola exceeds the bounded mesh budget.')
    points = [a]
    for i in range(1, count):
        x = xa+(xb-xa)*i/count; y = ((x-fx)**2+fy*fy)/(2*fy)
        points.append(tuple(origin+x*u+y*normal))
    points.append(b)
    return points, (xb-xa)**2/(8*abs(fy)*count*count)


def _diagram(source_union, sources, hull, tolerance):
    try:
        import pyvoronoi
    except ImportError as exc:
        raise ValueError('Medial-axis recovery needs pyvoronoi==1.2.8; install requirements.txt.') from exc
    raw = []
    for polygon in polygon_parts(source_union):
        for ring in [polygon.exterior, *polygon.interiors]:
            raw.extend((tuple(a), tuple(b)) for a,b in zip(ring.coords, list(ring.coords)[1:]))
    center = np.asarray(hull.centroid.coords[0]); span = max(hull.bounds[2]-hull.bounds[0], hull.bounds[3]-hull.bounds[1], 1.)
    frame = box(center[0]-4*span, center[1]-4*span, center[0]+4*span, center[1]+4*span)
    segments = raw+[(tuple(a), tuple(b)) for a,b in zip(frame.exterior.coords, list(frame.exterior.coords)[1:])]
    extent = max(abs(v) for segment in segments for p in segment for v in np.subtract(p, center))
    scale = 10.**ceil(log10(100*sqrt(2)/tolerance))
    while scale*extent < 2**30:
        pv = pyvoronoi.Pyvoronoi(scale); quantized = {}; snapped = []; error = 0.; collision = False
        for segment in segments:
            pair = []
            for point in segment:
                local = np.subtract(point, center); q = tuple(int(round(v*scale)) for v in local)
                if q in quantized and quantized[q] != point:
                    collision = True
                quantized[q] = point; actual = np.asarray(q)/scale
                error = max(error, float(np.linalg.norm(actual-local))); pair.append(tuple(actual))
            if pair[0] == pair[1]:collision = True
            snapped.append(pair); pv.AddSegment(pair)
        # The backend diagnostic flags ordinary ring corners too. Enforce its
        # documented input constraint directly: intersections may share ONLY
        # endpoints, including junctions between touching source contours.
        lines = [LineString(pair) for pair in snapped]; tree = STRtree(lines)
        if not collision:
            for i,line in enumerate(lines):
                for j in tree.query(line,predicate='intersects'):
                    if j>=i:continue
                    meet = line.intersection(lines[j])
                    if meet.geom_type!='Point' or tuple(meet.coords[0]) not in snapped[i] or tuple(meet.coords[0]) not in snapped[j]:
                        collision=True;break
                if collision:break
        if not collision and not pv.GetDegenerateSegments():
            pv.Construct()
            break
        scale *= 10
    else:
        raise ValueError('Cannot quantize medial sites within the precision/range and topology bounds.')
    owners = []
    for a,b in raw:
        midpoint = Point((a[0]+b[0])/2, (a[1]+b[1])/2)
        owners.append(tuple(k for k,g in sources.items() if g.boundary.distance(midpoint) <= arithmetic_bound([a,b])))
    sites = {}
    for i,cell in pv.EnumerateCells():
        if cell.site >= len(raw):continue
        a,b = raw[cell.site]
        if cell.contains_segment:points = (a,b)
        elif cell.source_category == 1:points = (a,)
        elif cell.source_category == 2:points = (b,)
        else:raise ValueError('Unsupported medial generating-site category.')
        source_ids=owners[cell.site]
        if len(points)==1:
            point=Point(points[0]);source_ids=tuple(k for k,g in sources.items() if g.boundary.distance(point)<=arithmetic_bound(points))
        sites[i] = MedialSite(cell.site, cell.source_category, points, source_ids)
        if cell.is_open:raise ValueError('An unbounded source medial cell reaches the temporary frame.')
    endpoint_registry=CoordinateRegistry([p for segment in snapped[:len(raw)] for p in segment],dimension=2)
    endpoints={}
    for original,quantized_pair in zip(raw,snapped):
        for point,quantized_point in zip(original,quantized_pair):
            endpoints[endpoint_registry.canonical(quantized_point)]=point
    return pv, sites, center, error, scale, endpoint_registry, endpoints


def _edge_radius_error(a,b,ha,hb,site):
    error=max(abs(ha-site.radius(a)),abs(hb-site.radius(b)))
    if len(site.points)==2:return error
    focus=site.points[0];ax,ay=a[0]-focus[0],a[1]-focus[1];dx,dy=b[0]-a[0],b[1]-a[1]
    length=hypot(dx,dy)
    if length==0:return error
    q=(ax*dx+ay*dy)/length;k=max(0.,ax*ax+ay*ay-q*q);slope=hb-ha
    if abs(slope)<length:
        u=slope*sqrt(k/max(length*length-slope*slope,2.2250738585072014e-308))
        t=min(1.,max(0.,(u-q)/length))
        error=max(error,abs(ha+t*slope-hypot(ax+t*dx,ay+t*dy)))
    return error


def _radius_error(triangle, heights, site):
    """Bound affine radius interpolation using its extrema on a triangle.

    In each segment cell radius is affine. In an endpoint cell it is a cone:
    affine-minus-norm is concave and has no isolated smooth interior maximum.
    Its extrema are on the edges or at the cone apex. The edge stationary
    parameter below is analytic, rather than a sampled error estimate.
    """
    error = max(abs(h-site.radius(p)) for p,h in zip(triangle,heights))
    if len(site.points) == 2:return error
    focus = site.points[0]
    for i,j in ((0,1),(1,2),(2,0)):
        error=max(error,_edge_radius_error(triangle[i],triangle[j],heights[i],heights[j],site))
    cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    a,b,c=triangle;area=cross(a,b,c)
    if area:
        weights=(cross(focus,b,c)/area,cross(a,focus,c)/area,cross(a,b,focus)/area)
        if min(weights)>=0:
            error=max(error,abs(sum(w*h for w,h in zip(weights,heights))))
    return error+1.4210854715202004e-14*max(1.,*(abs(v) for p in [*triangle,focus] for v in p))


def _refine(triangles, sites, radius, tolerance, cancelled, progress=lambda text:None):
    maximum = 0.
    for _ in range(32):
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        split = set(); maximum = 0.
        for triangle, site_id in triangles:
            error = _radius_error(triangle, [radius(p) for p in triangle], sites[site_id])
            maximum = max(maximum, error)
            if error > tolerance:
                edges = [tuple(sorted((triangle[i],triangle[(i+1)%3]))) for i in range(3)]
                split.add(max(edges, key=lambda e:(
                    _edge_radius_error(e[0],e[1],radius(e[0]),radius(e[1]),sites[site_id]),
                    hypot(e[0][0]-e[1][0],e[0][1]-e[1][1]))))
        if not split:return triangles, maximum
        progress(f'Refining medial surface: {len(triangles)} facets, radius error {maximum:g}')
        refined = []
        for triangle,site in triangles:
            pieces = [triangle]
            # Split every incident triangle at the SAME shared midpoint.
            for edge in [tuple(sorted((triangle[i],triangle[(i+1)%3]))) for i in range(3)]:
                if edge not in split:continue
                a,b = edge; mid = ((a[0]+b[0])/2,(a[1]+b[1])/2); next_pieces = []
                for piece in pieces:
                    if a in piece and b in piece:
                        k = next(i for i in range(3) if piece[i] not in edge)
                        c,d,e = piece[k],piece[(k+1)%3],piece[(k+2)%3]
                        next_pieces.extend([(c,d,mid),(c,mid,e)])
                    else:next_pieces.append(piece)
                pieces = next_pieces
            refined.extend((piece,site) for piece in pieces)
        triangles = refined
        if len(triangles)>250000:raise ValueError('Medial surface exceeds the bounded mesh budget.')
    raise ValueError('Medial surface did not meet the radius interpolation bound.')


def _conform_roof(triangles, extra_points):
    """Node XY before lifting, so arithmetic-flat CDT pieces cannot be flaps.

    A collinear T junction must split the opposite triangle's edge before
    assigning nonlinear radii. Noding only in 3D would retain a vertical flap
    between a chord and its differently lifted midpoint.
    """
    points=sorted({p for triangle,_ in triangles for p in triangle}|set(extra_points))
    registry=CoordinateRegistry(points,dimension=2)
    array=np.asarray(points);tree=STRtree([Point(p) for p in points]);result=[];flat=0
    for triangle,site in triangles:
        a,b,c=triangle;length=max(hypot(p[0]-q[0],p[1]-q[1]) for p,q in ((a,b),(b,c),(c,a)))
        area2=abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
        if length==0 or area2/length<=registry.bound:
            flat+=1;continue
        ring=[]
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):
            av=np.asarray(a);d=np.subtract(b,a);length2=float(d@d)
            indices=tree.query(box(*(np.minimum(a,b)-registry.axis_bounds),*(np.maximum(a,b)+registry.axis_bounds)))
            delta=array[indices]-av;ts=delta@d/length2
            hits=np.flatnonzero((ts>0)&(ts<1)&np.all(np.abs(delta-ts[:,None]*d)<=registry.axis_bounds,axis=1))
            ring.append(a);ring.extend(points[int(indices[i])] for i in sorted(hits,key=lambda i:ts[i]))
        if len(ring)==3:result.append((triangle,site));continue
        center=tuple(sum(p[k] for p in triangle)/3 for k in range(2))
        for a,b in zip(ring,ring[1:]+ring[:1]):
            if a!=b:result.append(((center,a,b),site))
    return result,flat


def medial_aggregation_faces(owner, sources, lo, merge_z, hi, tolerance, *, cancelled=lambda:False,progress=lambda text:None):
    """Return a closed source-to-hull roof with explicit medial provenance.

    The combined Mapping realization performs intersection validation after
    the hull morph and interface conformance; do not duplicate that scan here.
    """
    source_union = union_all(list(sources.values())); hull = source_union.convex_hull
    if not source_union.is_valid or not isinstance(hull,Polygon):raise ValueError('Invalid medial aggregation source union.')
    hull = orient(hull,1)
    gap = hull.difference(source_union)
    if gap.is_empty:
        from generalization_model import _prism
        return _prism(owner,hull,lo,hi),hull,dict(method='medial_axis',sites=[],maximum_radius=0.,surface_facets=0)
    progress('Constructing segment Voronoi medial axis')
    pv,sites,center,quantization,scale,endpoint_registry,endpoints = _diagram(source_union,sources,hull,tolerance)
    vertices = {i:(v.X,v.Y) for i,v in pv.EnumerateVertices()}; edges = dict(pv.EnumerateEdges())
    shared = {}; maximum_curve_error = 0.; site_regions = []; triangles = []
    # Keep room for both curved-site ownership error and shared-vertex radius
    # adjustment. Spending half the total budget on each would leave fixed
    # boundary vertices with no refinable interpolation margin.
    curve_tolerance = tolerance/8
    for cell_id,site in sites.items():
        if cancelled():raise ValueError('Mapping cancelled; previous models preserved.')
        cell = pv.GetCell(cell_id); ring = []
        for edge_id in cell.edges:
            edge = edges[edge_id]; key = min(edge_id,edge.twin)
            if key not in shared:
                points,error = _curve(pv,edges[key],vertices,curve_tolerance)
                shared[key] = [tuple(np.asarray(p)+center) for p in points]
                # Zero-radius diagram nodes are the original input endpoints.
                # Restore those nodes by site provenance, rather than leaving
                # quantization-width slivers against the exact source caps.
                for at in (0,-1):
                    original=endpoints.get(endpoint_registry.canonical(points[at]))
                    if original is not None:shared[key][at]=original
                maximum_curve_error = max(maximum_curve_error,error)
            points = shared[key] if edge_id == key else list(reversed(shared[key]))
            ring.extend(points[:-1])
        polygon = Polygon(ring)
        if not polygon.is_valid:raise ValueError('A segment Voronoi cell is invalid after bounded curve subdivision.')
        region = polygon.intersection(gap)
        for piece in polygon_parts(region):
            site_regions.append((cell_id,piece))
    coverage = union_all([p for _,p in site_regions])
    area_error = gap.symmetric_difference(coverage).area
    if area_error > arithmetic_bound(hull.exterior.coords)*max(hull.length,1.):
        raise ValueError(f'Medial cells do not cover the aggregation gap ({area_error:g} square metres).')
    # Node every cell boundary once. Independent clipping/CDT must not create
    # incompatible coordinates at three-site vertices or on the source rings.
    from shapely.ops import polygonize
    progress('Building conforming medial growth surface')
    constraints = [gap.boundary,*[p.boundary for _,p in site_regions]]
    # Endpoint cells are cones. Radial constraints preserve their straight
    # generators instead of repeatedly subdividing long, skinny CDT fans.
    for site_id,piece in site_regions:
        site=sites[site_id]
        if len(site.points)!=1:continue
        focus=site.points[0]
        for ring in [piece.exterior,*piece.interiors]:
            for point in ring.coords[:-1]:
                if tuple(point)!=focus:
                    constraints.append(LineString([focus,point]).intersection(piece))
    network = union_all(constraints)
    region_tree = STRtree([p for _,p in site_regions])
    registry = CoordinateRegistry([p for ring in [hull.exterior] for p in ring.coords]+
        [p for g in sources.values() for ring in [g.exterior,*g.interiors] for p in ring.coords],dimension=2)
    for g in sources.values():
        for ring in [g.exterior,*g.interiors]:
            for p in ring.coords:registry.canonical(p)
    for region in polygonize(network):
        bound=arithmetic_bound(region.exterior.coords)
        if region.area<=bound*max(region.length,1.):
            # GEOS can emit doubled-back arithmetic-width rings when a curved
            # cell is clipped to the exact hull. They are not surface regions.
            continue
        point = region.representative_point()
        if not gap.covers(point):continue
        matches = region_tree.query(point,predicate='covered_by')
        if len(matches)!=1:
            raise TransitionShellError('Medial growth regions overlap or lack generating-site ownership.',
                'medial_partition_failure',point=tuple(point.coords[0]),area=region.area,
                matches=[int(i) for i in matches],ring=list(region.exterior.coords))
        site_id = site_regions[int(matches[0])][0]
        for tri in constrained_delaunay_triangles(region).geoms:
            triangle = tuple(registry.canonical(p) for p in orient(tri,1).exterior.coords[:-1])
            if len(set(triangle))<3:continue
            triangles.append((triangle,site_id))
    extra_points=[tuple(p) for source in sources.values() for ring in [source.exterior,*source.interiors] for p in ring.coords[:-1]]
    triangles,flat_triangles=_conform_roof(triangles,extra_points)
    cache = {}; site_geometries = [Point(s.points[0]) if len(s.points)==1 else LineString(s.points) for s in sites.values()]
    site_ids = list(sites); site_tree = STRtree(site_geometries)
    def radius(point):
        if point not in cache:
            # Interior refinement vertices obtain their generator from the
            # explicit segment/endpoint diagram, never from a target distance.
            index = int(site_tree.nearest(Point(point))); value = sites[site_ids[index]].radius(point)
            cache[point] = 0. if value <= arithmetic_bound([point]) else value
        return cache[point]
    # The total radius bound includes site quantization and the displacement
    # of a true curved bisector by its shared polyline, not just mesh samples.
    diagram_error=2*maximum_curve_error+4*quantization
    interpolation_budget=tolerance-diagram_error
    if interpolation_budget<=0:raise ValueError('Medial diagram exhausts the permitted radius error budget.')
    triangles,error = _refine(triangles,sites,radius,interpolation_budget,cancelled,progress)
    maximum = max(radius(p) for triangle,_ in triangles for p in triangle)
    if not isfinite(maximum) or maximum<=0:raise ValueError('Medial growth has no positive finite event radius.')
    height = lambda p:lo if radius(p)==0 else merge_z if radius(p)==maximum else lo+(merge_z-lo)*radius(p)/maximum
    faces = [Face(owner,[(*p,height(p)) for p in reversed(triangle)],'mapping') for triangle,_ in triangles]
    for source in sources.values():
        caps = constrained_delaunay_triangles(source).geoms if source.interiors else [source]
        faces.extend(Face(owner,[(*p,lo) for p in orient(cap,-1).exterior.coords[:-1]],'bottom') for cap in caps)
    # Roof boundary vertices on the hull are inserted into its walls by 3D
    # noding. Heights along each wall's lower chain must follow the roof mesh.
    uses = defaultdict(list)
    for triangle,_ in triangles:
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):uses[tuple(sorted((a,b)))].append((a,b))
    boundary = []
    for edge,entries in uses.items():
        if len(entries)!=1:continue
        a,b = entries[0]; middle = Point((a[0]+b[0])/2,(a[1]+b[1])/2)
        if hull.boundary.distance(middle)<=arithmetic_bound([a,b]):boundary.append((a,b))
    # Parts of the hull already occupied by a source have a constant zero roof.
    for line in getattr(hull.boundary.intersection(source_union), 'geoms', [hull.boundary.intersection(source_union)]):
        if line.geom_type not in ('LineString','LinearRing'):continue
        boundary.extend(zip(line.coords,list(line.coords)[1:]))
    for a,b in boundary:
        faces.append(Face(owner,[(*a,height(a)),(*b,height(b)),(*b,hi),(*a,hi)],'mapping'))
    faces.append(Face(owner,[(*p,hi) for p in orient(hull,1).exterior.coords[:-1]],'top'))
    progress(f'Validating medial aggregation shell: {len(faces)} faces')
    faces = _node_faces(faces)
    from transition_layer import _check_shell,_planned_volume
    _check_shell(faces,context=dict(case='medial_axis_aggregation',cell=owner,stage='growth_boundary'))
    expected_volume=source_union.area*(hi-lo)+sum(Polygon(triangle).area*(hi-sum(height(p) for p in triangle)/3) for triangle,_ in triangles)
    volume=_planned_volume(faces)
    if abs(volume-expected_volume)>max(1e-6,expected_volume*1e-9):
        raise ValueError('Medial shell volume disagrees with its integrated growth surface.')
    medial_edges=[]
    for i,points in shared.items():
        e=edges[i]
        if not e.is_primary or e.cell not in sites or edges[e.twin].cell not in sites:continue
        for line in line_parts(LineString(points).intersection(gap)):
            coordinates=list(line.coords)
            medial_edges.append(dict(edge=i,sites=[e.cell,edges[e.twin].cell],points=coordinates,
                                     radii=[radius(p) for p in coordinates]))
    details = dict(method='medial_axis',backend='pyvoronoi',backend_version='1.2.8',
        quantization_error=quantization,integer_scale=scale,curve_error_bound=maximum_curve_error+quantization,
        radius_interpolation_error_bound=error+diagram_error,maximum_radius=maximum,surface_facets=len(triangles),
        coverage_area_error=area_error,arithmetic_flat_triangles=flat_triangles,integrated_volume=expected_volume,shell_volume=volume,
        source_holes=sum(len(p.interiors) for p in sources.values()),
        sites=[dict(cell=i,segment=s.segment,category=s.category,points=s.points,source_ids=s.source_ids) for i,s in sites.items()],
        medial_edges=medial_edges)
    return faces,hull,details
