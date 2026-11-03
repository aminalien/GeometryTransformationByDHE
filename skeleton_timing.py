"""Local uncertainty from verified backend aligned-edge rays, not a snap tolerance.

The immutable graph is captured before clipping. Bounds flow forward in event
time; unique seed IDs prevent double counting when branches meet. No point,
time, source edge, face or DHE record is modified here.
"""
from collections import defaultdict,deque
from dataclasses import dataclass
from math import atan2,degrees,isfinite
import numpy as np

from shell_conformity import arithmetic_bound,TransitionShellError


@dataclass(frozen=True)
class TimingNode:
    xy: tuple[float,float]
    time: float


@dataclass(frozen=True)
class TimingFace:
    edge: tuple
    node_ids: tuple[int,...]
    owner: str | None


@dataclass(frozen=True)
class TimingArc:
    start: int
    end: int
    face_ids: tuple[int,...]


@dataclass(frozen=True)
class TimingGraph:
    component: int
    nodes: tuple[TimingNode,...]
    faces: tuple[TimingFace,...]
    arcs: tuple[TimingArc,...]
    aligned_degrees: float


class TimingEnvelope:
    def __init__(self,graphs):
        self.graphs={g.component:g for g in graphs}
        self.weights={};self.seeds={};self.arc_lookup={}
        for graph in graphs:self._prepare(graph)

    def _prepare(self,g):
        if any(not isfinite(n.time) or n.time<0 or not all(isfinite(x) for x in n.xy) for n in g.nodes):
            raise TransitionShellError('Invalid skeleton event graph.','skeleton_time_conflict',stage='timing_provenance')
        weights={i:{} for i in range(len(g.nodes))};outgoing=defaultdict(list)
        for i,arc in enumerate(g.arcs):
            if any(n<0 or n>=len(g.nodes) for n in (arc.start,arc.end)):
                raise TransitionShellError('Skeleton arc references a missing node.','skeleton_time_conflict',stage='timing_provenance')
            self.arc_lookup[g.component,arc.start,arc.end]=arc
            a,b=arc.start,arc.end
            if g.nodes[a].time>g.nodes[b].time:a,b=b,a
            seed=self._seed(g,i,arc,a,b)
            outgoing[a].append((b,seed))
            if g.nodes[a].time==g.nodes[b].time:outgoing[b].append((a,None))
        # Monotone union also handles equal-time event components without
        # identifying their different geometric nodes or iterating budgets up.
        pending=deque(weights);queued=set(weights)
        while pending:
            a=pending.popleft();queued.discard(a)
            for b,seed in outgoing[a]:
                incoming=dict(weights[a])
                if seed:incoming[seed]=self.seeds[seed]['delta']
                changed=False
                for key,value in incoming.items():
                    if value>weights[b].get(key,0.):weights[b][key]=value;changed=True
                if changed and b not in queued:pending.append(b);queued.add(b)
        self.weights[g.component]=weights

    def _seed(self,g,index,arc,start,end):
        if len(arc.face_ids)!=2 or g.nodes[start].time==g.nodes[end].time:return None
        if any(i<0 or i>=len(g.faces) for i in arc.face_ids):return None
        faces=[g.faces[i] for i in arc.face_ids];units=[]
        for face in faces:
            a,b=np.asarray(face.edge);direction=b-a;length=np.linalg.norm(direction)
            if length==0:return None
            units.append(direction/length)
        u,v=units;cosine=float(np.dot(u,v));cross=float(u[0]*v[1]-u[1]*v[0])
        angle=degrees(atan2(abs(cross),cosine))
        if cosine<=0 or angle>g.aligned_degrees:return None
        a=np.asarray(g.nodes[start].xy);b=np.asarray(g.nodes[end].xy);travel=b-a
        bound=arithmetic_bound([a,b,*faces[0].edge,*faces[1].edge])
        for i,direction in enumerate(units):
            normal=np.array([-direction[1],direction[0]]);distance=float(travel@normal)
            if distance<=0 or abs(float(travel@direction))>bound:continue
            # For an original zero-time corner, verify the actual adjacent
            # source edges meet there; parallel but unrelated edges do not seed.
            if g.nodes[start].time==0 and not (
                np.linalg.norm(np.asarray(faces[i].edge[1])-a)<=bound and
                np.linalg.norm(np.asarray(faces[1-i].edge[0])-a)<=bound):continue
            delta=distance*cross*cross/(1+cosine)  # stable 1-cos(theta)
            n=np.array([-u[1],u[0]]);m=np.array([-v[1],v[0]])
            if delta<=bound or abs(abs(float((n-m)@travel))-delta)>bound:continue
            key=f'{g.component}:{index}'
            self.seeds[key]=dict(component=g.component,arc=index,nodes=[start,end],
                face_ids=list(arc.face_ids),owners=[f.owner for f in faces],
                angle_degrees=angle,aligned_degrees=g.aligned_degrees,
                normal_travel=distance,delta=float(delta))
            return key
        return None

    def sample(self,provenance,fraction):
        """Return recorded time and interpolated unique-seed contributions."""
        if provenance is None or fraction is None:return None,{}
        component,a,b=provenance
        if component not in self.graphs or (component,a,b) not in self.arc_lookup:
            raise TransitionShellError('Missing skeleton arc provenance.','skeleton_time_conflict',stage='timing_provenance',arc=provenance)
        if not isfinite(fraction) or not 0<=fraction<=1:
            raise TransitionShellError('Invalid skeleton clipping fraction.','skeleton_time_conflict',stage='timing_provenance',arc=provenance,fraction=fraction)
        g=self.graphs[component];wa,wb=self.weights[component][a],self.weights[component][b]
        weights={key:(1-fraction)*wa.get(key,0.)+fraction*wb.get(key,0.) for key in sorted(wa.keys()|wb.keys())}
        return g.nodes[a].time+fraction*(g.nodes[b].time-g.nodes[a].time),weights
