"""Reproduce local transition outcomes without publishing dataset coordinates.

Outputs go to ignored artifacts/manifold. An optional Git baseline is loaded
only in this audit process; production modules and the user's files are never
replaced. Legacy baseline builders use the current diagnostic validator.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

from shapely.ops import unary_union
from generalization_geometry import DEFAULT_DATA,load_shapefile
from generalization_model import GeneralizationSession
from plane_slicing import PlaneSlicer,SlicePlane
from top_face_overlay import cell_volume
import DHECore as dhe


def baseline_class(ref):
    process=subprocess.run(['git','-c',f'safe.directory={Path(__file__).parent.as_posix()}',
                            'show',f'{ref}:generalization_model.py'],capture_output=True,text=True,check=True)
    module=types.ModuleType('_dhe_audit_baseline')
    sys.modules[module.__name__]=module
    exec(compile(process.stdout,f'{ref}:generalization_model.py','exec'),module.__dict__)
    return module.GeneralizationSession


def topology(model):
    result={}
    for cell in model.cells:
        edges=dhe.Cell(cell.seed)
        faces=dhe.get_UniqueFace_in_cell(cell.seed)
        cycles=[]
        for face in faces:
            ring=tuple((e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(face))
            cycles.append(min(ring[i:]+ring[:i] for i in range(len(ring))))
        result[cell.feature_id]=dict(vertices=len({e.V for e in edges}),
            edges=len({frozenset((e.V,e.S.V)) for e in edges}),faces=len(faces),volume=cell_volume(cell),
            oriented_cycles_sha256=hashlib.sha256(repr(sorted(cycles)).encode()).hexdigest())
    return result


def run_case(cls,data,operation,selected,parameters):
    session=cls(data)
    before=topology(session.model)
    lower=PlaneSlicer(session.model).sections(SlicePlane.horizontal(2.5))
    result=dict(operation=operation,selected=selected,parameters=parameters,initial=before)
    try:
        proposal=session.preview(operation,selected,**parameters)
        session.apply(proposal)
        result.update(status='passed',final=topology(session.model),shared_interfaces=len(session.model.shared_walls),notes=proposal.notes)
        result['same_owner_comparison']={owner:dict(
            oriented_cycles_equal=value['oriented_cycles_sha256']==result['final'][owner]['oriented_cycles_sha256'],
            count_delta={name:result['final'][owner][name]-value[name] for name in ('vertices','edges','faces')})
            for owner,value in before.items() if owner in result['final']}
        sections=PlaneSlicer(session.model).sections(SlicePlane.horizontal(2.5))
        differences={}
        for owner,feature in session.state.features.items():
            expected=unary_union([lower[str(k)] for k in feature.lineage if str(k) in lower])
            differences[str(owner)]=expected.symmetric_difference(sections[str(owner)]).area
        result['historical_section_difference_area']=differences
        terminal=PlaneSlicer(session.model).sections(SlicePlane.horizontal(session.state.z),include_degenerate=True)
        result['terminal_geometry_types']={k:g.geom_type for k,g in terminal.items()}
    except ValueError as exc:
        cause=exc
        while not hasattr(cause,'report') and cause.__cause__ is not None:cause=cause.__cause__
        result.update(status='rejected',reason=str(exc),diagnostic=getattr(cause,'report',{}),
                      session_unchanged=session.state.z==5 and len(session.history)==1)
    except Exception as exc:
        result.update(status='error',reason=f'{type(exc).__name__}: {exc}',
                      session_unchanged=session.state.z==5 and len(session.history)==1)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-shp',default=DEFAULT_DATA)
    parser.add_argument('--baseline-ref',default='32eeaf4')
    parser.add_argument('--all-features',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('artifacts/manifold'))
    args=parser.parse_args(argv)
    data=load_shapefile(args.input_shp)
    cases=[('simplification',[3],dict(tolerance=t)) for t in (.01,.1,.5,1.,2.,5.)]
    cases += [('smoothing',[1,4],{}),('point',[2],{}),('polyline',[1,4],{}),
              ('aggregation',[1,4],{}),('merging',[2,5],dict(winner=2))]
    report=dict(baseline_ref=args.baseline_ref,comparisons=[],support=[])
    args.output.mkdir(parents=True,exist_ok=True)
    def save():
        (args.output/'transition_audit.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    try:before=baseline_class(args.baseline_ref)
    except (subprocess.CalledProcessError,FileNotFoundError) as exc:
        before=None;report['baseline_unavailable']=str(exc)
    for operation,selected,parameters in cases:
        row=dict(operation=operation,selected=selected,parameters=parameters)
        if before:row['before']=run_case(before,data,operation,selected,parameters)
        row['after']=run_case(GeneralizationSession,data,operation,selected,parameters)
        report['comparisons'].append(row)
        save()
        print(operation,selected,parameters,'->',row['after']['status'],flush=True)
    if args.all_features:
        for operation in ('simplification','smoothing','point','polyline'):
            passed=0
            for index in data.frame['_index']:
                result=run_case(GeneralizationSession,data,operation,[int(index)],{})
                report['support'].append(result)
                passed+=result['status']=='passed'
            print(operation,passed,'/',len(data.frame),flush=True)
            save()
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'transition_audit.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    lines=['# Local transition manifold audit','',
           'Raw diagnostic coordinates are local artifacts and are excluded from commits.','',
           '| Operation | Selection | Parameters | Before | After |','|---|---|---|---|---|']
    for row in report['comparisons']:
        lines.append(f"| {row['operation']} | {row['selected']} | {row['parameters']} | {row.get('before',{}).get('status','unavailable')} | {row['after']['status']} |")
    lines += ['','## Support and remaining failures','']
    groups=defaultdict(list)
    for row in report['support']:groups[row['operation']].append(row)
    for operation,rows in groups.items():
        lines.append(f"{operation}: {sum(r['status']=='passed' for r in rows)}/{len(rows)} passed.")
        lines.extend(f"- Feature {r['selected']}: {r['reason']}" for r in rows if r['status']!='passed')
        lines.append('')
    (args.output/'transition_audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':main()
