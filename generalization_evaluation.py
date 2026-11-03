"""Read-only measurements and append-only reports for GUI-applied operations.

No evaluator calls a DHE operator, restores global topology, or changes history.
The GUI owns the trigger; programmatic Apply and previews remain unchanged.
"""
from __future__ import annotations

from datetime import datetime,timezone
import csv
import hashlib
import json
from math import isfinite,pi,sqrt
from pathlib import Path
import tempfile
from uuid import UUID,uuid4

import shapely
from shapely.geometry import Point
from shapely.ops import unary_union

import DHECore as dhe
from top_face_overlay import cell_volume

DEFAULT_EVALUATION_FOLDER=Path(__file__).parent/'artifacts'/'evaluations'
OPERATIONS={'simplification','smoothing','point','polyline','merging','aggregation'}
CSV_METADATA=('evaluation_id','session_id','parent_evaluation_id','history_step','operation','selected',
              'affected','winner','parameters','levels','dataset_source','dataset_fingerprint')
CSV_FIELDS=CSV_METADATA+('scope','metric','label','value','unit','before','after','delta','status','reason')


def _fraction(numerator,denominator):
    if denominator<=0:raise ValueError('The reference denominator is not positive.')
    return numerator/denominator


def _metric(scope,key,label,unit,compute,**extra):
    row=dict(scope=scope,metric=key,label=label,unit=unit,**extra)
    try:
        value=compute()
        if not isfinite(value):raise ValueError('The calculated value is not finite.')
        row.update(value=value,status='ok',reason='')
    except Exception as exc:
        row.update(value=None,status='unavailable',reason=f'{type(exc).__name__}: {exc}')
    return row


def _geometry_values(geometry):
    if geometry is None or geometry.is_empty:raise ValueError('Result geometry is missing or empty.')
    return dict(type=geometry.geom_type,area=geometry.area,length=geometry.length,
                centroid=list(geometry.centroid.coords[0]),
                vertices=len(geometry.exterior.coords)-1 if geometry.geom_type=='Polygon' else None)


def _feature_metrics(operation,source,target,index):
    scope=f'Feature {index}';rows=[]
    def add(key,label,unit,compute):rows.append(_metric(scope,key,label,unit,compute))
    area=source.area
    if operation in ('simplification','smoothing'):
        if operation=='simplification':
            add('vertex_reduction','Vertex reduction','%',lambda:100*(1-_fraction(
                len(target.exterior.coords)-1,len(source.exterior.coords)-1)))
        else:
            add('compactness_change','Compactness change','1',lambda:
                _fraction(4*pi*target.area,target.length**2)-_fraction(4*pi*area,source.length**2))
        add('relative_symmetric_difference','Symmetric-difference area','%',
            lambda:100*_fraction(source.symmetric_difference(target).area,area))
    elif operation in ('point','polyline'):
        def radius():return sqrt(_fraction(area,pi))
        add('normalized_centroid_displacement','Normalized centroid displacement','1',
            lambda:_fraction(source.centroid.distance(target.centroid),radius()))
        if operation=='point':
            add('normalized_boundary_clearance','Normalized boundary clearance','1',
                lambda:_fraction(target.distance(source.boundary),radius()))
        else:
            def relative_length():
                ring=list(source.minimum_rotated_rectangle.exterior.coords)
                longest=max(Point(a).distance(Point(b)) for a,b in zip(ring,ring[1:]))
                return 100*_fraction(target.length,longest)
            add('relative_line_length','Relative line length','%',relative_length)
    return rows


def _topology_values(model,owners):
    """Per-cell primal counts and volume, read directly from realized DHE links."""
    cells={}
    for cell in model.cells:
        if int(cell.feature_id) not in owners:continue
        edges=dhe.Cell(cell.seed)
        cells[str(cell.feature_id)]=dict(vertices=len({e.V for e in edges}),
            edges=len({frozenset((e.V,e.S.V)) for e in edges}),
            faces=len(dhe.get_UniqueFace_in_cell(cell.seed)),volume=cell_volume(cell))
    totals={key:sum(c[key] for c in cells.values()) for key in ('vertices','edges','faces','volume')}
    return dict(cells=cells,totals=totals)


def evaluate_operation(base,applied,before_model,after_model,context):
    """Return numerical measurements of two supplied states; never write files."""
    record=dict(context);operation=applied.operation
    if operation is None or operation.name not in OPERATIONS:raise ValueError('Unsupported applied operation.')
    rows=[];raw={}
    if operation.name in ('merging','aggregation'):
        sources=[base.features[k].geometry for k in operation.selected]
        union=unary_union(sources);target=applied.features[operation.winner].geometry
        raw['group']=dict(source_union=_geometry_values(union),target=_geometry_values(target),
            source_total_perimeter=sum(g.length for g in sources))
        if operation.name=='merging':
            rows.append(_metric('Selected group','union_preservation_error','Union-preservation error','%',
                lambda:100*_fraction(target.symmetric_difference(union).area,union.area)))
        else:
            rows.append(_metric('Selected group','area_expansion','Area expansion','%',
                lambda:100*_fraction(target.area-union.area,union.area)))
        rows.append(_metric('Selected group','boundary_length_reduction','Total boundary-length reduction','%',
            lambda:100*(1-_fraction(target.length,sum(g.length for g in sources)))))
    else:
        for index in operation.selected:
            source=base.features[index].geometry;target=applied.features[index].geometry
            raw[str(index)]=dict(source=_geometry_values(source),target=_geometry_values(target))
            rows.extend(_feature_metrics(operation.name,source,target,index))
    owners=set(operation.affected)|set(operation.selected)
    if operation.winner is not None:owners.add(operation.winner)
    before=_topology_values(before_model,owners);after=_topology_values(after_model,owners)
    for key in ('vertices','edges','faces'):
        a,b=before['totals'][key],after['totals'][key]
        rows.append(_metric('Affected DHE cells','dhe_'+key,'DHE '+key,'count',lambda b=b:b,
                            before=a,after=b,delta=b-a))
    rows.append(_metric('Affected DHE cells','added_transition_volume','Added transition volume',
        'm^2 * transition-Z',lambda:after['totals']['volume']-before['totals']['volume']))
    record.update(metrics=rows,raw_geometry=raw,topology=dict(before=before,after=after),
                  status='ok' if all(r['status']=='ok' for r in rows) else 'partial')
    return record


def _uuid(value):
    if not isinstance(value,str) or UUID(value).hex!=value:raise ValueError('Invalid report identifier.')
    return value


def _validate_report(record):
    if record.get('schema_version')!=1 or record.get('operation') not in OPERATIONS:
        raise ValueError('Unsupported evaluation report.')
    for key in ('evaluation_id','session_id'):_uuid(record[key])
    if record.get('parent_evaluation_id') is not None:_uuid(record['parent_evaluation_id'])
    if record.get('status') not in ('ok','partial','failed'):raise ValueError('Incomplete evaluation report.')
    if not isinstance(record.get('history_step'),int) or record['history_step']<1:raise ValueError('Invalid history step.')
    datetime.fromisoformat(record['created_utc'])
    for key in ('selected','affected'):
        if not isinstance(record.get(key),list) or not all(isinstance(v,int) for v in record[key]):raise ValueError('Invalid feature indices.')
    if not isinstance(record.get('parameters'),dict):raise ValueError('Missing parameters.')
    if not isinstance(record.get('dataset'),dict) or not isinstance(record['dataset'].get('source'),str):raise ValueError('Missing dataset context.')
    if not isinstance(record.get('levels'),dict) or any(not isinstance(record['levels'].get(k),(int,float)) for k in ('z_s','z_t')):
        raise ValueError('Missing transition levels.')
    if not isinstance(record.get('metrics'),list):raise ValueError('Missing metric rows.')
    for row in record['metrics']:
        if not all(isinstance(row.get(k),str) for k in ('scope','metric','label','unit','status','reason')):
            raise ValueError('Invalid metric row.')
        if row['status'] not in ('ok','unavailable'):raise ValueError('Invalid metric status.')
        if row['status']=='ok' and (not isinstance(row.get('value'),(int,float)) or not isfinite(row['value'])):
            raise ValueError('Invalid numerical metric.')
        if row['status']=='unavailable' and row.get('value') is not None:raise ValueError('Unavailable values must be null.')
        for key in ('before','after','delta'):
            if key in row and (not isinstance(row[key],(int,float)) or not isfinite(row[key])):raise ValueError('Invalid topology count.')
    json.dumps(record,allow_nan=False)


def save_evaluation(record,folder):
    """Publish one JSON/CSV pair together, without replacing any previous report."""
    payload={k:v for k,v in record.items() if not k.startswith('_')}
    _validate_report(payload)
    parent=Path(folder).expanduser().resolve()/payload['session_id'];parent.mkdir(parents=True,exist_ok=True)
    destination=parent/(payload['evaluation_id']+'_'+payload['operation'])
    if destination.exists():raise FileExistsError(f'Evaluation already exists: {destination}')
    with tempfile.TemporaryDirectory(prefix='.pending-',dir=parent) as temporary:
        temp=Path(temporary)
        (temp/'evaluation.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
        with (temp/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=CSV_FIELDS);writer.writeheader()
            metadata={k:payload.get(k,'') for k in CSV_METADATA}
            for key in ('selected','affected','parameters','levels'):metadata[key]=json.dumps(payload[key],sort_keys=True)
            metadata.update(dataset_source=payload['dataset']['source'],dataset_fingerprint=payload['dataset']['fingerprint'])
            for row in payload['metrics']:writer.writerow(metadata|{k:row.get(k,'') for k in CSV_FIELDS[len(CSV_METADATA):]})
        # The destination contains no files until both reports are complete.
        temp.rename(destination)
        # TemporaryDirectory's cleanup tolerates its path having been renamed.
    return destination/'evaluation.json'


def load_evaluations(folder):
    records=[];warnings=[];root=Path(folder).expanduser()
    if not root.exists():return records,warnings
    try:paths=sorted(root.glob('*/[!.]*/evaluation.json'))
    except OSError as exc:return [],[str(exc)]
    for path in paths:
        try:
            record=json.loads(path.read_text(encoding='utf-8'));_validate_report(record)
            if not path.with_name('metrics.csv').is_file():raise ValueError('Missing numerical CSV.')
            record['_path']=str(path);records.append(record)
        except (OSError,ValueError,KeyError,TypeError) as exc:warnings.append(f'{path.name} ({path.parent.name}): {exc}')
    return sorted(records,key=lambda r:r.get('created_utc',''),reverse=True),warnings


class EvaluationJournal:
    """GUI-side history links; independent of model State and Undo implementation."""
    def __init__(self):
        self.records={};self.chain=[None];self.session_id=None;self.dataset={}

    def start_session(self,data,origin,history_size=1):
        digest=hashlib.sha256()
        for _,row in data.frame.sort_values('_index').iterrows():
            digest.update(str(int(row['_index'])).encode());digest.update(b'\0');digest.update(row.geometry.wkb)
        digest.update(data.frame.crs.to_wkt().encode())
        self.session_id=uuid4().hex;self.chain=[None]*history_size
        self.dataset=dict(source=data.source,fingerprint=digest.hexdigest(),crs=data.frame.crs.to_wkt(),
                          xy_units='m',origin=list(origin))

    def register_applied(self,base_cursor,base,applied):
        if self.session_id is None:raise ValueError('Evaluation session is not initialized.')
        operation=applied.operation
        context=dict(schema_version=1,evaluation_id=uuid4().hex,session_id=self.session_id,
            parent_evaluation_id=self.chain[base_cursor],history_step=base_cursor+1,
            created_utc=datetime.now(timezone.utc).isoformat(),dataset=dict(self.dataset),
            operation=operation.name,selected=list(operation.selected),affected=list(operation.affected),
            description=applied.description,
            winner=operation.winner,retired=list(operation.retired),parameters=dict(operation.parameters),
            levels={k:getattr(operation,k) for k in ('z_b','z_s','z_m','z_t')},
            lineage_before={str(k):list(base.features[k].lineage) for k in operation.selected},
            lineage_after={str(k):list(f.lineage) for k,f in applied.features.items() if k in operation.affected},
            software=dict(shapely=shapely.__version__,geos=shapely.geos_version_string),
            metric_version=1)
        self.chain=self.chain[:base_cursor+1]+[context['evaluation_id']]
        return context

    def current_id(self,cursor):return self.chain[cursor] if cursor<len(self.chain) else None

    def review(self,folder):
        records,warnings=load_evaluations(folder)
        for record in records:self.records.setdefault(record['evaluation_id'],record)
        return sorted(self.records.values(),key=lambda r:r['created_utc'],reverse=True),warnings

    def complete(self,record):self.records[record['evaluation_id']]=record


def evaluate_and_save(base,applied,before_model,after_model,context,folder):
    """Contain evaluation failures after Apply; never roll back a completed model."""
    try:record=evaluate_operation(base,applied,before_model,after_model,context)
    except Exception as exc:
        record=dict(context,status='failed',metrics=[],error=f'{type(exc).__name__}: {exc}')
    try:record['_path']=str(save_evaluation(record,folder))
    except Exception as exc:record['_save_error']=f'{type(exc).__name__}: {exc}'
    return record
