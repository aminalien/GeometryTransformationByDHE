"""Many-to-one DHE aggregation with retained lower source cells."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass,field
from itertools import product
from math import hypot, isfinite

from shapely import constrained_delaunay_triangles, union_all
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

import DHECore as dhe
from DHECore import face_edges
from aggregation_geometry import AggregationPreview, SOURCE_COLORS, polygon_parts, validate_aggregation
from top_face_overlay import cell_volume
from transition_layer import _check_shell, _planned_volume
from shell_conformity import TransitionShellError, arithmetic_bound

AGGREGATE_COLOR = (0.28, 0.48, 0.68, 1.0)
EXTERIOR_COLOR = (0.55, 0.2, 0.75, 1.0)


@dataclass
class AggregationFacet:
    seed: dhe.DualHalfEdge
    part: str
    source_id: str | None

    @property
    def cell_id(self):return str(self.seed.D.V.logical_feature_id)

    @property
    def xyz(self):
        return [(e.V.x, e.V.y, e.V.z) for e in face_edges(self.seed)]

    @property
    def geometry(self):
        return Polygon([p[:2] for p in self.xyz])


@dataclass
class _FacePlan:
    points: list
    part: str
    source_id: str | None


@dataclass
class AggregationLayer:
    preview: AggregationPreview
    cell: object
    model: object
    bottom_z: float
    source_z: float
    merge_z: float
    target_z: float
    maximum_time: float
    facets: list[AggregationFacet]
    expected_volume: float
    source_colors: dict
    _face_sources: dict
    planned_growth: tuple = ()
    source_cells: dict = field(default_factory=dict)
    expected_upper_volume: float = 0.

    @property
    def connecting_facets(self):
        return [f for f in self.facets if f.part == "connecting"]

    def face_color(self, edge):
        if edge.D.V is self.model.exterior:
            return EXTERIOR_COLOR
        key = tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(edge)))
        return self.source_colors.get(self._face_sources.get(key), AGGREGATE_COLOR)

    def horizontal_section(self, z):
        """Analytical wavefront section, independent of the DHE slicing code."""
        if not isfinite(z) or not self.bottom_z <= z <= self.target_z:
            raise ValueError("Section height must lie within the aggregate.")
        if z <= self.source_z:
            return self.preview.union
        if z >= self.merge_z or self.maximum_time == 0:
            return self.preview.hull
        reference_max=max((_arrival(face,p) for face in self.preview.growth_faces
            for polygon in polygon_parts(face.geometry) for ring in [polygon.exterior,*polygon.interiors]
            for p in ring.coords),default=0.)
        time = (z - self.source_z) / (self.merge_z - self.source_z) * reference_max
        pieces = [self.preview.union]
        for face in self.preview.growth_faces:
            for polygon in polygon_parts(face.geometry):
                # Clip a bounding rectangle by the source edge's moving line;
                # this handles concave faces and faces with holes without a mesh.
                xmin, ymin, xmax, ymax = self.preview.hull.bounds
                pad = max(xmax - xmin, ymax - ymin, 1)
                rectangle = [(xmin-pad, ymin-pad), (xmax+pad, ymin-pad),
                             (xmax+pad, ymax+pad), (xmin-pad, ymax+pad)]
                clipped = []
                for a, b in zip(rectangle, rectangle[1:] + rectangle[:1]):
                    va, vb = _arrival(face, a) - time, _arrival(face, b) - time
                    if va <= 0:
                        clipped.append(a)
                    if (va < 0 < vb) or (vb < 0 < va):
                        fraction = va / (va - vb)
                        clipped.append(tuple(a[k] + fraction * (b[k] - a[k]) for k in range(2)))
                if len(clipped) >= 3:
                    pieces.append(polygon.intersection(Polygon(clipped)))
        return union_all(pieces)

    def event_time_section(self,z):
        """Independent section of the immutable planned event-time field.

        Unlike horizontal_section's ideal edge-plane reference, this uses the
        backend's shared event times. It never reads live DHE vertices.
        """
        if not isfinite(z) or not self.bottom_z<=z<=self.target_z:
            raise ValueError('Section height must lie within the aggregate.')
        if z<=self.source_z:return self.preview.union
        if z>=self.merge_z:return self.preview.hull
        pieces=[self.preview.union]
        for triangle in self.planned_growth:
            clipped=[]
            for a,b in zip(triangle,triangle[1:]+triangle[:1]):
                if a[2]<=z:clipped.append(a[:2])
                if (a[2]<z<b[2]) or (b[2]<z<a[2]):
                    t=(z-a[2])/(b[2]-a[2]);clipped.append(tuple(a[k]+t*(b[k]-a[k]) for k in (0,1)))
            if len(clipped)>=3:pieces.append(Polygon(clipped))
        return union_all(pieces)


def _arrival(face, xy):
    a, b = face.source_edge.coords
    return ((b[0]-a[0]) * (xy[1]-a[1]) - (b[1]-a[1]) * (xy[0]-a[0])) / hypot(b[0]-a[0], b[1]-a[1])


def _growth_times(preview, nodes):
    """Use one skeleton-provenance time at a shared node, not one per face.

    Clipped arc endpoints already carry interpolated event times. The backend
    rounds event times and treats nearly collinear edges specially, so signed
    edge distances are an independent numerical reference, not competing Zs.
    """
    from py_straight_skeleton.constants import DISTANCE_EPSILON, TIME_EPSILON
    from skeleton_timing import TimingEnvelope
    envelope=TimingEnvelope(preview.timing_graphs)
    endpoint_times=defaultdict(list)
    for i,arc in enumerate(preview.skeleton_arcs):
        fractions=arc.fractions or (None,None)
        for point,time,fraction in zip((arc.geometry.coords[0],arc.geometry.coords[-1]),arc.times,fractions):
            endpoint_times[nodes.canonical(point)].append((time,i,arc.provenance,fraction))
    times={};maximum_delta=0.;maximum_extra=0.;sources_used=defaultdict(int);explained=[];used_seeds=set()
    for point in nodes.points:
        p=Point(point)
        if preview.union.distance(p)<=preview.tolerance:
            evidence=endpoint_times.get(point,())
            if any(abs(value[0])>max(DISTANCE_EPSILON,TIME_EPSILON)+arithmetic_bound([point]) for value in evidence):
                raise TransitionShellError('A source-boundary vertex carries a nonzero skeleton event time.',
                    'skeleton_time_conflict',stage='source_vertex_time',point=point,
                    times=[e[0] for e in evidence],arcs=[e[1:] for e in evidence])
            times[point]=0.;sources_used['source_boundary']+=1;continue
        faces=[f for f in preview.growth_faces if f.geometry.distance(p)<=preview.tolerance]
        estimates=[_arrival(f,point) for f in faces]
        evidence=list(endpoint_times.get(point,()))
        if not evidence:
            bound=arithmetic_bound([point])
            for i,arc in enumerate(preview.skeleton_arcs):
                if arc.geometry.distance(p)<=bound:
                    fraction=arc.geometry.project(p)/arc.geometry.length
                    full_fraction=arc.fractions[0]+fraction*(arc.fractions[1]-arc.fractions[0]) if arc.fractions else None
                    evidence.append((arc.times[0]+fraction*(arc.times[1]-arc.times[0]),i,arc.provenance,full_fraction))
        samples=[]
        if evidence:
            candidates=[e[0] for e in evidence]
            if max(candidates)-min(candidates)>arithmetic_bound([(t,) for t in candidates]):
                raise TransitionShellError('Skeleton arcs carry conflicting times at one shared vertex.',
                    'skeleton_time_conflict',stage='growth_vertex_time',point=point,
                    times=candidates,arcs=[e[1:] for e in evidence])
            time=candidates[0];sources_used['skeleton_arc']+=1
            for candidate,index,provenance,fraction in evidence:
                expected,weights=envelope.sample(provenance,fraction) if envelope.graphs else (None,{})
                if expected is not None and abs(expected-candidate)>arithmetic_bound([(expected,),(candidate,)]):
                    raise TransitionShellError('Clipped arc time disagrees with its recorded event nodes.',
                        'skeleton_time_conflict',stage='timing_provenance',point=point,arc=provenance,
                        fraction=fraction,time=candidate,expected_time=expected)
                samples.append(weights)
        elif estimates and max(estimates)-min(estimates)<=arithmetic_bound([(t,) for t in estimates]):
            time=estimates[0];sources_used['edge_interior']+=1
        else:
            raise TransitionShellError('A growth vertex has no consistent skeleton timing provenance.',
                'skeleton_time_conflict',stage='growth_vertex_time',point=point,
                edge_times=estimates,owners=[f.source_id for f in faces])
        base_limit=max(DISTANCE_EPSILON,TIME_EPSILON)+arithmetic_bound([point])
        weights=min(samples,key=lambda w:sum(w.values())) if samples else {}
        extra=sum(weights.values());limit=base_limit+extra
        delta=max((abs(t-time) for t in estimates),default=0.)
        true_event=any(e[3] in (0.,1.) for e in evidence) and bool(envelope.graphs)
        anchored=not true_event or any(abs(t-time)<=base_limit for t in estimates)
        if not estimates or delta>limit or not isfinite(time) or time<0 or not anchored:
            raise TransitionShellError('Skeleton event time disagrees with its incident source-edge geometry.',
                'skeleton_time_conflict',stage='growth_vertex_time',point=point,time=time,
                edge_times=estimates,owners=[f.source_id for f in faces],backend_time_bound=base_limit,
                approximation_bound=extra,effective_time_bound=limit,approximation_sources=list(weights),event_time_anchored=anchored)
        if delta>base_limit:
            explained.append(dict(point=point,time=time,edge_times=estimates,owners=[f.source_id for f in faces],
                backend_time_bound=base_limit,approximation_bound=extra,effective_time_bound=limit,
                approximation_sources=list(weights)))
        used_seeds.update(weights);maximum_extra=max(maximum_extra,extra)
        maximum_delta=max(maximum_delta,delta);times[point]=time
    preview.timing_diagnostics=dict(maximum_edge_time_difference=maximum_delta,
        backend_distance_epsilon=DISTANCE_EPSILON,backend_time_epsilon=TIME_EPSILON,
        timing_sources=dict(sources_used),assignment='shared skeleton event/arc times; exact source zero',
        maximum_propagated_approximation_bound=maximum_extra,explained_vertices=explained,
        approximation_sources={k:envelope.seeds[k] for k in sorted(used_seeds)})
    return times


class _XYNodes:
    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.bins = defaultdict(list)
        self.points = []

    def canonical(self, point):
        point = tuple(float(v) for v in point[:2])
        key = tuple(round(v / self.tolerance) for v in point)
        for offset in product((-1, 0, 1), repeat=2):
            for other in self.bins[(key[0]+offset[0], key[1]+offset[1])]:
                if hypot(point[0]-other[0], point[1]-other[1]) <= self.tolerance:
                    return other
        self.bins[key].append(point)
        self.points.append(point)
        return point

    def add_geometry(self, geometry):
        for polygon in polygon_parts(geometry):
            for ring in [polygon.exterior, *polygon.interiors]:
                for point in list(ring.coords)[:-1]:
                    self.canonical(point)

    def ring(self, ring):
        coords = [self.canonical(p) for p in ring.coords]
        result = []
        for a, b in zip(coords, coords[1:]):
            line = LineString([a, b])
            at = [(0, a)]
            for point in self.points:
                distance = line.project(Point(point))
                if self.tolerance < distance < line.length-self.tolerance and line.distance(Point(point)) <= self.tolerance:
                    at.append((distance, point))
            for _, point in sorted(at):
                if not result or point != result[-1]:
                    result.append(point)
        return result


def _check_vertex_links(plans):
    """Reject point pinches even when every edge has two incident faces."""
    links = defaultdict(lambda: defaultdict(list))
    for plan in plans:
        points = plan.points
        for i, vertex in enumerate(points):
            a, b = points[i-1], points[(i+1) % len(points)]
            links[vertex][a].append(b)
            links[vertex][b].append(a)
    for graph in links.values():
        if any(len(neighbours) != 2 for neighbours in graph.values()):
            raise ValueError("Aggregate has a non-manifold vertex neighbourhood.")
        reached, pending = set(), [next(iter(graph))]
        while pending:
            vertex = pending.pop()
            if vertex not in reached:
                reached.add(vertex)
                pending.extend(graph[vertex])
        if len(reached) != len(graph):
            raise ValueError("Aggregate source polygons meet at a non-manifold point pinch.")


def _plan_upper_shell(preview, source_z, merge_z, top_z):
    """Validate the real upper cell, whose bottom is at the source roofs.

    Extruding the union below source_z creates an artificial nonmanifold
    vertical edge when distinct retained sources touch at a point. Those
    lower sources are separate cells; that union prism is never realized.
    """
    validate_aggregation(preview)
    if any(p.interiors for p in preview.sources.values()):
        raise ValueError("Aggregate source footprints must have single-ring bottom faces.")
    nodes = _XYNodes(preview.tolerance)
    for geometry in [*preview.sources.values(), preview.union, preview.hull,
                     *[f.geometry for f in preview.growth_faces]]:
        nodes.add_geometry(geometry)
    times=_growth_times(preview,nodes)
    maximum_time=max(times.values(),default=0.)
    heights = {}
    for point,time in times.items():
        heights[point]=source_z if time==0 else merge_z if time==maximum_time else source_z+(merge_z-source_z)*time/maximum_time
    plans = []
    for key, source in preview.sources.items():
        plans.append(_FacePlan([(*p, source_z) for p in nodes.ring(orient(source, -1).exterior)], "bottom_cap", key))
    expected_volume = preview.union.area * (top_z-source_z)
    for face in preview.growth_faces:
        for polygon in polygon_parts(face.geometry):
            polygon = Polygon(nodes.ring(polygon.exterior), [nodes.ring(r) for r in polygon.interiors])
            for triangle in constrained_delaunay_triangles(polygon).geoms:
                coords = [nodes.canonical(p) for p in list(orient(triangle, -1).exterior.coords)[:-1]]
                xyz = [(*p, heights[p]) for p in coords]
                if max(p[2] for p in xyz)-min(p[2] for p in xyz) <= 1e-10:
                    raise ValueError("A growth triangle is horizontal.")
                plans.append(_FacePlan(xyz, "connecting", face.source_id))
                expected_volume += triangle.area * (top_z-sum(p[2] for p in xyz)/3)
    hull = nodes.ring(orient(preview.hull, 1).exterior)
    for a, b in zip(hull, hull[1:]+hull[:1]):
        midpoint = Point((a[0]+b[0])/2, (a[1]+b[1])/2)
        owner = next((key for key, partition in preview.partitions.items()
                      if partition.distance(midpoint) <= preview.tolerance), None)
        plans.append(_FacePlan([(*a, heights[a]), (*b, heights[b]), (*b, top_z), (*a, top_z)], "hull_wall", owner))
    plans.append(_FacePlan([(*p, top_z) for p in hull], "upper_cap", None))
    _check_shell(plans,context=dict(case='aggregation',cell='aggregate',bottom_z=source_z,source_z=source_z,
                                  merge_z=merge_z,target_z=top_z,stage='growth_boundary'))
    _check_vertex_links(plans)
    from shell_conformity import check_face_intersections
    check_face_intersections(plans)
    if abs(_planned_volume(plans)-expected_volume) > max(1e-6, expected_volume*1e-9):
        raise ValueError("Aggregate boundary volume disagrees with its integrated growth surface.")
    return plans, maximum_time, expected_volume


def _plan_shell(preview, bottom_z, source_z, merge_z, top_z):
    """Compatibility adapter: upper facets and total retained-cell volume."""
    plans, maximum_time, upper_volume = _plan_upper_shell(preview,source_z,merge_z,top_z)
    return plans, maximum_time, upper_volume+preview.union.area*(source_z-bottom_z)


def plan_aggregation_cells(preview,bottom_z,source_z,merge_z,top_z):
    """Coordinate-only plans for every retained source and the upper cell."""
    from generalization_model import Face,_prism,_conform_input_interfaces
    if not all(isfinite(v) for v in (bottom_z, source_z, merge_z, top_z)) or not bottom_z < source_z < merge_z < top_z:
        raise ValueError("Aggregation requires finite z-down < height < merge-z < target-z.")
    plans, maximum_time, expected_volume = _plan_shell(preview, bottom_z, source_z, merge_z, top_z)
    owners={key:i for i,key in enumerate(preview.sources,1)};upper=len(owners)+1
    faces=[];metadata={}
    conformed=_conform_input_interfaces(preview.sources)
    for key,owner in owners.items():
        for f in _prism(owner,conformed[key],bottom_z,source_z):
            part='source_roof' if f.part=='top' else 'bottom_cap' if f.part=='bottom' else 'base_wall'
            faces.append(f);metadata[owner,tuple(sorted(f.points))]=(part,key)
    for plan in plans:
        pts=plan.points
        part='source_interface' if plan.part=='bottom_cap' else plan.part
        f=Face(upper,pts,'bottom' if part=='source_interface' else 'top' if part=='upper_cap' else 'transition' if part=='connecting' else 'wall')
        faces.append(f);metadata[upper,tuple(sorted(pts))]=(part,plan.source_id)
    return faces,metadata,owners,upper,maximum_time,expected_volume,plans


def build_aggregation_model(preview, *, bottom_z=0.0, source_z=5.0, merge_z=9.0, top_z=10.0):
    """Retain lower prisms and sew their roofs to a separate upper aggregate."""
    from generalization_model import realize
    faces,metadata,owners,upper,maximum_time,expected_volume,plans=plan_aggregation_cells(preview,bottom_z,source_z,merge_z,top_z)
    saved = dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId
    try:
        model,_,_=realize(faces,preview.origin)
        source_cells={}
        for c in model.cells:
            owner=int(c.feature_id)
            key=next((k for k,v in owners.items() if v==owner),None)
            label='aggregate' if owner==upper else 'source:aggregate' if key=='aggregate' else key
            c.feature_id=c.logical_feature_id=str(label)
            c.dual_vertex.i=str(label);c.dual_vertex.logical_feature_id=str(label)
            if owner==upper:cell=c
            else:source_cells[key]=c
        model.exterior.color=EXTERIOR_COLOR
        facets=[]
        for f,seed in model.generalization_face_records:
            part,source=metadata.get((f.owner,tuple(sorted(f.points))),(None,None))
            if part is None:
                from plane_slicing import _face_frame
                import numpy as np
                frame=_face_frame(f.points);matches=[];pieces=[]
                for candidate in faces:
                    if candidate.owner!=f.owner:continue
                    arr=np.asarray(candidate.points)-frame.points[0]
                    if np.max(np.abs(arr@frame.normal))>arithmetic_bound([*f.points,*candidate.points]):continue
                    projected=Polygon(np.column_stack((arr@frame.u,arr@frame.v)))
                    if projected.intersection(frame.polygon).area>arithmetic_bound(f.points)**2:
                        matches.append(metadata[candidate.owner,tuple(sorted(candidate.points))]);pieces.append(projected)
                if matches and frame.polygon.difference(union_all(pieces).buffer(arithmetic_bound(f.points)*8)).area<=frame.polygon.length*2e-8:
                    parts={p for p,s in matches};sources={s for p,s in matches}
                    part=next(iter(parts)) if len(parts)==1 else 'boundary'
                    source=next(iter(sources)) if len(sources)==1 else None
            if part is None:raise ValueError('Unresolved aggregation facet provenance.')
            facets.append(AggregationFacet(seed,part,source))
        cell.source_ids = tuple(preview.sources)
        colors = {key: tuple(int(SOURCE_COLORS[i % len(SOURCE_COLORS)][j:j+2], 16)/255
                             for j in (1, 3, 5)) + (1.0,) for i, key in enumerate(preview.sources)}
        face_sources = {tuple(sorted(f.xyz)): f.source_id for f in facets}
        model.aggregation = AggregationLayer(preview, cell, model, bottom_z, source_z, merge_z, top_z,
                                             maximum_time, facets, expected_volume, colors, face_sources,
                                             tuple(tuple(p.points) for p in plans if p.part=='connecting'),source_cells,
                                             expected_volume-preview.union.area*(source_z-bottom_z))
        validate_aggregation_model(model)
        return model
    except Exception:
        dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId = saved
        raise


def validate_aggregation_model(model):
    from main import validate_model
    from plane_slicing import _face_frame
    summaries = validate_model(model)
    layer = model.aggregation
    if len(model.cells)!=len(layer.preview.sources)+1 or set(layer.source_cells)!=set(layer.preview.sources):
        raise ValueError('Aggregation must retain each lower source and a separate upper cell.')
    for cell in model.cells:
        plans=[_FacePlan(f.xyz,f.part,f.source_id) for f in layer.facets if f.seed.D.V is cell.dual_vertex]
        _check_shell(plans);_check_vertex_links(plans)
    if sum(len(dhe.get_UniqueFace_in_cell(c.seed)) for c in model.cells) != len(layer.facets):
        raise ValueError("Aggregation facet metadata does not match its live DHE faces.")
    if sum(f.part == "upper_cap" for f in layer.facets) != 1:
        raise ValueError("Aggregate must have exactly one polygon top face.")
    top = next(f for f in layer.facets if f.part == "upper_cap")
    if top.geometry.symmetric_difference(layer.preview.hull).area > layer.preview.area_tolerance:
        raise ValueError("Aggregate top face is not the convex hull.")
    bottoms = {f.source_id: f for f in layer.facets if f.part == "bottom_cap"}
    if set(bottoms) != set(layer.preview.sources):
        raise ValueError("Aggregate bottom faces do not preserve every source ID.")
    for key, source in layer.preview.sources.items():
        if bottoms[key].geometry.symmetric_difference(source).area > layer.preview.area_tolerance:
            raise ValueError(f"Aggregate bottom face changes source {key}.")
    for face in layer.facets:
        _face_frame(face.xyz)
        heights = [p[2] for p in face.xyz]
        if face.part == "bottom_cap" and any(z != layer.bottom_z for z in heights):
            raise ValueError("Aggregate bottom cap has the wrong elevation.")
        if face.part == "upper_cap" and any(z != layer.target_z for z in heights):
            raise ValueError("Aggregate top cap has the wrong elevation.")
        if face.part == "connecting" and (len(heights) != 3 or min(heights) < layer.source_z or max(heights) > layer.merge_z):
            raise ValueError("An aggregate growth facet is outside its elevation interval.")
        if max(heights)-min(heights) <= 1e-10 and face.part not in ("bottom_cap", "upper_cap",'source_roof','source_interface'):
            raise ValueError("Aggregate contains an internal horizontal face.")
    for key,source in layer.source_cells.items():
        interfaces=[(a,b) for a,b in model.shared_walls if {a.D.V,b.D.V}=={source.dual_vertex,layer.cell.dual_vertex}]
        if len(interfaces)!=1:raise ValueError(f'Source {key} lacks exactly one aggregate interface.')
        a,b=interfaces[0]
        if any(z!=layer.source_z for _,_,z in [(e.V.x,e.V.y,e.V.z) for e in face_edges(a)]):
            raise ValueError('Source-aggregate interface is not at the source level.')
        if abs(cell_volume(source)-layer.preview.sources[key].area*(layer.source_z-layer.bottom_z))>1e-6:
            raise ValueError('A lower source cell changed its occupied geometry.')
    if abs(cell_volume(layer.cell)-layer.expected_upper_volume)>max(1e-6,layer.expected_volume*1e-9):
        raise ValueError('Upper aggregate volume disagrees with its planned volume.')
    volume = sum(cell_volume(c) for c in model.cells)
    if volume <= 0 or abs(volume-layer.expected_volume) > max(1e-6, layer.expected_volume*1e-9):
        raise ValueError("Aggregate DHE volume does not match its planned volume.")
    return summaries
