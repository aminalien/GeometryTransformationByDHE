"""Explicit, read-only evaluation of realized LTS history. No automatic saves."""
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime,timezone
import csv
import json
from math import ceil,hypot,isfinite,pi
from pathlib import Path
import tempfile
from time import perf_counter
from uuid import uuid4

import numpy as np
import shapely
from shapely.geometry import LineString,Point
from shapely.ops import unary_union

from plane_slicing import PlaneSlicer,SlicePlane
from shell_conformity import check_oriented_shell,check_interface_pairs


def parts(g):
    if g.is_empty:return
    if hasattr(g,'geoms'):
        for child in g.geoms:yield from parts(child)
    else:yield g


def graphic(geometries):
    return unary_union([p.boundary if p.geom_type=='Polygon' else p for g in geometries for p in parts(g)])


def _primitives(g):
    result=[]
    for p in parts(g):
        if p.geom_type=='Point':result.append(p)
        elif p.geom_type in ('LineString','LinearRing'):
            coords=list(p.coords)
            result.extend(LineString([a,b]) for a,b in zip(coords,coords[1:]) if a!=b)
    return result


class DisplacementReference:
    def __init__(self,geometries,relative_spacing=.001,max_samples=200000):
        if not isfinite(relative_spacing) or relative_spacing<=0:raise ValueError('HD relative spacing must be finite and positive.')
        self.geometry=graphic(geometries);self.primitives=_primitives(self.geometry)
        self.diagonal=0. if self.geometry.is_empty else hypot(self.geometry.bounds[2]-self.geometry.bounds[0],self.geometry.bounds[3]-self.geometry.bounds[1])
        self.spacing=self.diagonal*relative_spacing;self.max_samples=max_samples
        self.tree=shapely.STRtree(self.primitives) if self.primitives else None

    def _samples(self,primitives):
        count=sum(1 if p.geom_type=='Point' else ceil(p.length/self.spacing)+1 for p in primitives)
        if count>self.max_samples:raise ValueError(f'HD requires {count} samples; budget is {self.max_samples}. No coarsening was applied.')
        return shapely.points(shapely.get_coordinates(shapely.segmentize(np.array(primitives,dtype=object),self.spacing)))

    def compare(self,geometries):
        target=graphic(geometries);primitives=_primitives(target)
        result=dict(hausdorff_m=None,normalized_hausdorff=None,hd_spacing_m=self.spacing,
                    hd_reference_diagonal_m=self.diagonal,hd_sampling_bound_m=self.spacing/2,hd_reason='')
        if not self.primitives or not primitives:
            result['hd_reason']='An actual representation is empty.';return result
        if self.diagonal==0:
            # A single reference point has a valid distance but no diagonal normalization.
            result['hausdorff_m']=max(self.geometry.distance(p) for p in shapely.points(shapely.get_coordinates(target)))
            result['hd_reason']='Reference diagonal is zero; normalized HD is undefined.';return result
        try:
            a=self._samples(self.primitives);b=self._samples(primitives);tree=shapely.STRtree(primitives)
            result.update(hd_reference_samples=len(a),hd_result_samples=len(b))
            da=shapely.distance(a,tree.geometries.take(tree.nearest(a)))
            db=shapely.distance(b,self.tree.geometries.take(self.tree.nearest(b)))
            value=float(max(np.max(da),np.max(db)))
            result.update(hausdorff_m=value,normalized_hausdorff=value/self.diagonal)
        except (ValueError,MemoryError) as exc:result['hd_reason']=str(exc)
        return result


def _map_values(geometries,totals):
    flat=[p for g in geometries for p in parts(g)];polygons=[g for g in flat if g.geom_type=='Polygon']
    polygonal=bool(polygons) and len(polygons)==len(flat)
    reason='' if polygonal or totals else 'Polygon area/perimeter are N/A for a point/polyline or missing representation.'
    return dict(geometry_type='+'.join(sorted({g.geom_type for g in flat})) or 'Empty',
        area_m2=sum(g.area for g in polygons) if polygonal or totals else None,
        perimeter_m=sum(g.length for g in polygons) if polygonal or totals else None,
        line_length_m=sum(g.length for g in flat if g.geom_type=='LineString'),
        point_count=sum(g.geom_type=='Point' for g in flat),polygon_metric_reason=reason)


def _comparison(geometries,baseline,reference,totals):
    values=_map_values(geometries,totals);before=_map_values(baseline,totals)
    values['change_reason']=values['polygon_metric_reason'] or before['polygon_metric_reason']
    for key,label in (('area_m2','area'),('perimeter_m','perimeter')):
        a,b=before[key],values[key]
        values[label+'_change']=None if a is None or b is None else b-a
        values[label+'_change_pct']=None if a is None or b is None or a==0 else 100*(b-a)/a
        if a==0:values['change_reason']='Percentage change is N/A because the reference polygon measure is zero.'
    values.update(reference.compare(geometries));return values


def _state_sections(state):
    return PlaneSlicer.from_face_records((str(f.owner),f.points) for f in state.faces)


def _actual(slicer,z,expected=None):
    result=slicer.sections(SlicePlane.horizontal(z),include_degenerate=True,horizontal_side='below')
    if expected is not None:return {int(k):g for k,g in result.items() if int(k) in expected}
    return {int(k):g for k,g in result.items() if g.area>0}


def _entities(faces):
    vertices=set();edges=set();unique_faces=set();owners=set()
    for f in faces:
        pts=tuple(tuple(p) for p in f.points);owners.add(f.owner);vertices.update(pts)
        edges.update(frozenset((a,b)) for a,b in zip(pts,pts[1:]+pts[:1]));unique_faces.add(tuple(sorted(pts)))
    return dict(vertices=len(vertices),edges=len(edges),faces=len(unique_faces),cells=len(owners))


def structural_audit(state,actual):
    groups=defaultdict(list);edge_uses=defaultdict(list);face_uses=defaultdict(list)
    for f in state.faces:
        groups[f.owner].append(f);face_uses[tuple(sorted(f.points))].append(f)
        for a,b in zip(f.points,f.points[1:]+f.points[:1]):edge_uses[f.owner,frozenset((a,b))].append((a,b))
    invalid=0
    for shell in groups.values():
        try:check_oriented_shell(shell)
        except ValueError:invalid+=1
    bad_interfaces=0
    for uses in face_uses.values():
        if len(uses)>1:
            try:check_interface_pairs(uses)
            except ValueError:bad_interfaces+=1
    active=set(state.features)&set(actual)
    retained=_entities(state.faces);front=_entities([f for f in state.faces if f.owner in active])
    missing=sorted(set(state.features)-set(actual))
    return dict(**{'retained_'+k:v for k,v in retained.items()},**{'top_supporting_'+k:v for k,v in front.items()},
        logical_features=len(state.features),actual_top_features=len(active),missing_logical_features=missing,
        missing_terminal_features=[k for k in missing if state.features[k].geometry.geom_type in ('Point','LineString')],
        missing_edges=sum(len(v)==1 for v in edge_uses.values()),nonmanifold_edges=sum(len(v)>2 for v in edge_uses.values()),
        misoriented_edges=sum(len(v)==2 and v[0]!=tuple(reversed(v[1])) for v in edge_uses.values()),
        invalid_closed_cells=invalid,inconsistent_interfaces=bad_interfaces,
        live_dual_status='N/A: historical realized-boundary audit only')


def live_audit(model):
    from main import validate_model
    try:
        validate_model(model)
        return dict(live_dual_status='Passed: live primal/dual pointers and reciprocal interfaces',live_validation_error='')
    except ValueError as exc:return dict(live_dual_status='Failed',live_validation_error=str(exc))


def _case_descriptor(operation,source,target):
    if source is None or target is None or source.is_empty or target.is_empty:return 'N/A','Actual source or target is missing.'
    if operation=='simplification':return 'Vertex reduction (%)',100*(1-(len(target.exterior.coords)-1)/(len(source.exterior.coords)-1))
    if operation=='smoothing':return 'Compactness change',4*pi*target.area/target.length**2-4*pi*source.area/source.length**2
    if operation=='point':return 'Normalized centroid displacement',source.centroid.distance(target)/np.sqrt(source.area/pi)
    if operation=='polyline':
        p=list(source.minimum_rotated_rectangle.exterior.coords)
        return 'Relative line length (%)',100*target.length/max(Point(a).distance(Point(b)) for a,b in zip(p,p[1:]))
    if operation=='merging':return 'Union preservation error (%)',100*source.symmetric_difference(target).area/source.area
    hull=source.convex_hull
    return 'Target hull deviation (%)',100*hull.symmetric_difference(target).area/hull.area


def evaluate_history(history,end_index,mode,live_model,dataset,session_id,*,relative_spacing=.001,max_samples=200000):
    """Explicit request only. History must be the active prefix; no DHE allocation."""
    started=perf_counter()
    if mode not in ('last_operation','whole_history'):raise ValueError('Choose Last operation or Whole history.')
    states=list(history[:end_index+1])
    if not states:raise ValueError('No history state to evaluate.')
    if mode=='last_operation' and end_index==0:raise ValueError('Apply an operation before evaluating Last operation.')
    registry={k:set(f.lineage) for s in states for k,f in s.features.items()};changed=set()
    for i,s in enumerate(states[1:],1):
        for k in set(s.operation.selected)|set(s.operation.affected):
            changed.update(registry.get(k,{k}))
    if mode=='whole_history':
        scopes={'Entire map':(set(registry),True),'Changed lineage':({k for k,v in registry.items() if v&changed},True)}
        first=0;steps=range(1,len(states))
    else:
        op=states[-1].operation;first=len(states)-2;steps=[len(states)-1]
        if op.name in ('merging','aggregation'):
            scopes={'Selected group':(set(op.selected)|{op.winner},True)}
            if op.name=='merging':scopes[f'Winner {op.winner}']=({op.winner},False)
        else:scopes={f'Feature {k}':({k},False) for k in op.selected}
        scopes['Affected context']=(set(op.affected)|set(op.selected),True)
    slicers={i:_state_sections(states[i]) for i in range(first,len(states))}
    actual={i:_actual(slicers[i],states[i].z,states[i].features) for i in slicers}
    references={};baselines={}
    for scope,(keys,totals) in scopes.items():
        baseline=[g for k,g in actual[first].items() if k in keys]
        baselines[scope]=baseline;references[scope]=DisplacementReference(baseline,relative_spacing,max_samples)
    summary=[];sections=[];structure=[]
    for i in range(first,len(states)):
        s=states[i]
        for scope,(keys,totals) in scopes.items():
            gs=[g for k,g in actual[i].items() if k in keys]
            row=dict(scope=scope,step=i,step_id=s.step_id,z=s.z,reference_step=first,
                reference_z=states[first].z,**_comparison(gs,baselines[scope],references[scope],totals))
            if mode=='last_operation' and i==len(states)-1 and scope!='Affected context' and not scope.startswith('Winner'):
                op=s.operation
                try:label,value=_case_descriptor(op.name,unary_union(baselines[scope]),unary_union(gs))
                except (ValueError,AttributeError,ZeroDivisionError):label,value='N/A','Descriptor is undefined for actual geometry.'
                row.update(operation_descriptor=label,descriptor_value=None if label=='N/A' else value,
                           descriptor_reason=value if label=='N/A' else '')
            summary.append(row)
        row=dict(step=i,step_id=s.step_id,z=s.z,**structural_audit(s,actual[i]))
        if i==end_index:row.update(live_audit(live_model))
        structure.append(row)
    for i in steps:
        s=states[i];lo=states[i-1].z;hi=s.z
        for j in range(11):
            t=j/10;z=lo+t*(hi-lo)
            expected=states[i-1].features if j==0 else s.features if j==10 else None
            gs=_actual(slicers[i],z,expected)
            for scope,(keys,totals) in scopes.items():
                sections.append(dict(scope=scope,step=i,step_id=s.step_id,t=t,z=z,z_s=lo,z_t=hi,
                    reference_step=first,reference_z=states[first].z,
                    **_comparison([g for k,g in gs.items() if k in keys],baselines[scope],references[scope],totals)))
    aggregates=[]
    for scope in scopes:
        selected=[r for r in sections if r['scope']==scope];weights=[];values=[]
        for r in selected:
            if r['normalized_hausdorff'] is not None:
                weights.append((r['z_t']-r['z_s'])/10*(.5 if r['t'] in (0.,1.) else 1.));values.append(r['normalized_hausdorff'])
        aggregates.append(dict(scope=scope,max_sampled_nhd=max(values) if values else None,
            sampled_z_weighted_mean_nhd=float(np.average(values,weights=weights)) if values and sum(weights)>0 else None,
            valid_samples=len(values),requested_samples=len(selected)))
    return dict(schema_version=2,evaluation_id=uuid4().hex,session_id=session_id,
        created_utc=datetime.now(timezone.utc).isoformat(),mode=mode,source_step_id=states[first].step_id,
        end_step_id=states[-1].step_id,end_index=end_index,dataset=dict(dataset),
        scope_definitions={scope:dict(cell_ids=sorted(keys),initial_lineage=sorted(set().union(*(registry.get(k,{k}) for k in keys))),
            polygon_totals=totals,reference_z=states[first].z,reference_diagonal_m=references[scope].diagonal)
            for scope,(keys,totals) in scopes.items()},
        lineage_registry={str(k):sorted(v) for k,v in registry.items()},
        operations=[dict(step=i,step_id=s.step_id,parent_step_id=s.parent_step_id,**asdict(s.operation)) for i,s in enumerate(states) if s.operation and (mode=='whole_history' or i==end_index)],
        source_provenance=[dict(step=i,step_id=s.step_id,**asdict(s.operation)) for i,s in enumerate(states[:first+1]) if s.operation],
        settings=dict(relative_hd_spacing=relative_spacing,max_hd_samples=max_samples,horizontal_side='below'),
        tables=dict(summary=summary,sections=sections,structure=structure),profile_summary=aggregates,
        software=dict(shapely=shapely.__version__,geos=shapely.geos_version_string),
        evaluation_elapsed_seconds=perf_counter()-started)


def analysis_report(record):
    lines=['# LTS quantitative analysis report','',f"Mode: {record['mode']}. End state: {record['end_step_id']} (history index {record['end_index']}).",
        f"Dataset: {record['dataset']['source']}. Fingerprint: {record['dataset']['fingerprint']}.",
        '', '## Applied operations and parameters',
        *[f"- Step {o['step']} ({o['step_id']}): {o['name']}; selected={o['selected']}; affected={o['affected']}; winner={o['winner']}; parameters={o['parameters']}; Zs={o['z_s']}, Zt={o['z_t']}, Zm={o['z_m']}." for o in record['operations']],
        '', '## Definitions and comparison inputs',
        *[f"- {scope}: family cell IDs={definition['cell_ids']}; initial lineage={definition['initial_lineage']}; reference Z={definition['reference_z']}; D={definition['reference_diagonal_m']} m." for scope,definition in record.get('scope_definitions',{}).items()],
        'Every geometry is an actual horizontal section of realized DHE faces. No geometry or measurement is interpolated between section levels. Each operation has 11 levels, z(t)=zs+t(zt-zs), t=0,0.1,...,1. Source-interface ownership is taken from below.',
        'Area A is polygonal coverage in m². Perimeter P sums polygon-feature boundaries in m (shared boundaries count per feature). Signed differences are ΔA=A−Aref and ΔP=P−Pref; percentages are 100ΔA/Aref and 100ΔP/Pref. For individual collapsed targets these polygon metrics are N/A. Whole-map coverage totals exclude points and polylines; their counts and line lengths are reported separately.',
        'Displacement compares polygon boundaries, line geometries, and points. The symmetric sampled Hausdorff Hh is the largest sampled point-to-original-geometry distance in either direction. NHD=Hh/D, where D is the fixed reference bounding-box diagonal. Maximum sample spacing h=D·relative_hd_spacing; the sampling-only continuous-distance bound is Hh≤H≤Hh+h/2. Floating-point errors are separate. A budget overflow yields N/A without coarsening.',
        'Whole-history references are the initial top representation; last-operation references are the preceding source representation. Reference step and Z, reference diagonal D, spacing, sample counts and sampling bound are recorded in the numerical rows. Changed-lineage scope includes selected ancestors and affected neighbours.',
        'Retained V/E/F/C counts are globally unique vertices, undirected edges, faces and bounded cells across the complete stored complex. Top-supporting counts use the full closed shells of cells supporting actual current logical features; they are not the topology of a clipped planar mesh.',
        'Missing/non-manifold/misoriented edges and invalid shells are counted per cell. Historical checks inspect realized boundary records. Only the live model receives a primal/dual pointer and reciprocal-interface check. These checks do not constitute an exact-arithmetic proof of geometric embedding.',
        'Operation descriptors: vertex reduction=100(1−nT/nS); compactness change=4πAT/PT²−4πAS/PS²; point displacement=|cT−cS|/sqrt(AS/π); relative line length=100LT/Drect; merging error=100A(T△U)/A(U); aggregation hull deviation=100A(T△CH(U))/A(CH(U)). They are descriptors, not calibrated quality scores.',
        'Profile summaries use maximum sampled NHD and a Z-weighted trapezoidal mean of the sampled NHD values. These summarize measured samples, not exact continuous integrals. Missing values are omitted and coverage is disclosed. No counts or heterogeneous case descriptors are averaged.',
        '', '## Numerical tables and observed results']
    for name,rows in record['tables'].items():
        lines.extend(['',f'### {name.title()} table',f'{len(rows)} measured rows; see {name}.csv for unrounded values.'])
        if name=='summary':
            for r in rows:lines.append(f"- {r['scope']}, step {r['step']}, Z={r['z']:g}: A={r['area_m2']}, P={r['perimeter_m']}, Hh={r['hausdorff_m']}, NHD={r['normalized_hausdorff']}. "+r['polygon_metric_reason']+' '+r['hd_reason'])
        if name=='structure':
            for r in rows:lines.append(f"- Step {r['step']}: retained V/E/F/C={r['retained_vertices']}/{r['retained_edges']}/{r['retained_faces']}/{r['retained_cells']}; top-supporting cells={r['top_supporting_cells']}; invalid shells={r['invalid_closed_cells']}; interface errors={r['inconsistent_interfaces']}; missing logical features={r['missing_logical_features']}; {r['live_dual_status']}.")
    lines.extend(['','## Profile summaries',json.dumps(record['profile_summary'],indent=2),
        '', '## Limitations and paper presentation',
        'Values measure representational change rather than error against independent ground truth. Boundary removal and dimensional collapse can intentionally increase displacement. DHE counts include retained historical cells and must not be conflated with the number of symbols on a map. Terminal symbols absent from higher DHE sections are disclosed as missing, never synthesized.',
        'Construction time and memory were not measured retrospectively. evaluation_elapsed_seconds measures this analysis only.',
        'Recommended paper tables: endpoint changes and displacement; retained versus top-supporting complexity and validation. Recommended figures: area/perimeter/NHD against actual Z (sampled profiles), and the actual primal/dual many-to-one aggregation graph showing retained lower cells and the upper aggregate.',
        '', 'Metric definitions and library semantics: DHE_Generalization_Evaluation_Methodology.md; https://shapely.readthedocs.io/en/stable/reference/shapely.segmentize.html .'])
    return '\n\n'.join(lines)+'\n'


def save_report(record,folder):
    if record.get('schema_version')!=2:raise ValueError('Use the legacy exporter for version-1 reports.')
    payload={k:v for k,v in record.items() if not k.startswith('_')}
    from uuid import UUID
    for key in ('session_id','evaluation_id'):
        if UUID(payload[key]).hex!=payload[key]:raise ValueError('Invalid report identifier.')
    content=json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)
    parent=Path(folder).expanduser().resolve()/record['session_id'];parent.mkdir(parents=True,exist_ok=True)
    destination=parent/(record['evaluation_id']+'_'+record['mode'])
    if destination.exists():raise FileExistsError(f'Report already saved: {destination}')
    with tempfile.TemporaryDirectory(dir=parent,prefix='.pending-') as temporary:
        temp=Path(temporary);(temp/'evaluation.json').write_text(content,encoding='utf-8')
        for name,rows in record['tables'].items():
            columns=list(dict.fromkeys(k for row in rows for k in row)) or ['status']
            with (temp/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
                for row in rows:writer.writerow({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})
        (temp/'analysis_report.md').write_text(analysis_report(record),encoding='utf-8')
        temp.rename(destination)
    return destination/'evaluation.json'


def load_reports(folder):
    from generalization_evaluation import _validate_report
    records=[];warnings=[]
    for path in Path(folder).expanduser().glob('*/[!.]*/evaluation.json'):
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            if record.get('schema_version')==1:_validate_report(record)
            elif record.get('schema_version')==2:
                if record['mode'] not in ('last_operation','whole_history') or not all(isinstance(record['tables'][k],list) for k in ('summary','sections','structure')):raise ValueError('Invalid evaluation tables.')
                from uuid import UUID
                for key in ('evaluation_id','session_id','source_step_id','end_step_id'):UUID(record[key])
                datetime.fromisoformat(record['created_utc'])
                if not isinstance(record['dataset']['source'],str):raise ValueError('Missing dataset source.')
                for key in ('summary','sections','structure'):
                    if not path.with_name(key+'.csv').is_file():raise ValueError('Incomplete numerical tables.')
                    if not all(isinstance(row,dict) for row in record['tables'][key]):raise ValueError('Invalid table row.')
                if not path.with_name('analysis_report.md').is_file():raise ValueError('Missing analysis report.')
                json.dumps(record,allow_nan=False)
            else:raise ValueError('Unsupported report version.')
            record['_path']=str(path);records.append(record)
        except (ValueError,KeyError,TypeError,OSError) as exc:warnings.append(f'{path}: {exc}')
    return sorted(records,key=lambda r:r.get('created_utc',''),reverse=True),warnings


def profile_figure(record,scope):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from methodology_publication import PublicationSettings,style
    import matplotlib as mpl
    settings=PublicationSettings()
    with mpl.rc_context(style(settings)):
        fig=Figure(figsize=(10,7));FigureCanvasAgg(fig);fig._publication_artists={}
        axes=fig.subplots(3,1,sharex=True)
        rows=[r for r in record['tables']['sections'] if r['scope']==scope]
        for step in sorted({r['step'] for r in rows}):
            part=[r for r in rows if r['step']==step]
            for ax,key,title in zip(axes,('area_change_pct','perimeter_change_pct','normalized_hausdorff'),('Polygon area change (%)','Polygon perimeter change (%)','Sampled normalized Hausdorff')):
                ax.plot([r['z'] for r in part],[np.nan if r[key] is None else r[key] for r in part],'.-',label=f'Step {step}')
                ax.set_ylabel(title);ax.set_facecolor('white');ax.grid(False)
        axes[-1].set_xlabel('Transition Z');axes[0].legend(fontsize=8);fig.suptitle(scope);fig.tight_layout()
        return fig,settings
