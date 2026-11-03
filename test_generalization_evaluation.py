"""Actual measurements, append-only persistence and independent history links."""
import csv
import json
from math import pi,sqrt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import Polygon,box

import DHECore as dhe
from generalization_evaluation import (EvaluationJournal,evaluate_operation,evaluate_and_save,
    load_evaluations,save_evaluation,_feature_metrics)
from generalization_model import _core
from test_generalization import session


def applied(s,journal,operation,selected,**kwargs):
    base=s.state;model=s.model;cursor=s.cursor
    proposal=s.preview(operation,selected,**kwargs);s.apply(proposal)
    context=journal.register_applied(cursor,base,s.state)
    return base,model,context


def journal_for(s):
    journal=EvaluationJournal();journal.start_session(s.data,s.origin);return journal


def values(record):return {r['metric']:r['value'] for r in record['metrics']}


class EvaluationMetricTests(unittest.TestCase):
    def test_analytical_simplification_and_smoothing(self):
        s=session([Polygon([(0,0),(5,0),(10,0),(10,10),(0,10)])]);j=journal_for(s)
        base,model,context=applied(s,j,'simplification',[1],tolerance=.1)
        result=evaluate_operation(base,s.state,model,s.model,context);v=values(result)
        self.assertAlmostEqual(v['vertex_reduction'],20)
        self.assertEqual(v['relative_symmetric_difference'],0)
        self.assertAlmostEqual(v['added_transition_volume'],1000)
        s=session([box(0,0,10,10)]);j=journal_for(s)
        base,model,context=applied(s,j,'smoothing',[1])
        v=values(evaluate_operation(base,s.state,model,s.model,context))
        self.assertAlmostEqual(v['relative_symmetric_difference'],12.5)
        self.assertAlmostEqual(v['compactness_change'],350*pi/(20+10*sqrt(2))**2-pi/4)

    def test_analytical_point_and_actual_polyline(self):
        for operation in ('point','polyline'):
            with self.subTest(operation=operation):
                s=session([box(0,0,10,5)]);j=journal_for(s)
                base,model,context=applied(s,j,operation,[1])
                result=evaluate_operation(base,s.state,model,s.model,context);v=values(result)
                target=s.state.features[1].geometry
                self.assertAlmostEqual(v['normalized_centroid_displacement'],target.centroid.distance(base.features[1].geometry.centroid)/sqrt(50/pi))
                if operation=='point':
                    self.assertAlmostEqual(v['normalized_boundary_clearance'],2.5/sqrt(50/pi))
                    self.assertAlmostEqual(v['added_transition_volume'],500/3)
                else:self.assertAlmostEqual(v['relative_line_length'],100*target.length/10)

    def test_analytical_merge_and_aggregation(self):
        for operation,polygons,kwargs,metric,expected in (
            ('merging',[box(0,0,5,5),box(5,0,10,5)],{'winner':2},'union_preservation_error',0),
            ('aggregation',[box(0,0,2,2),box(6,0,8,2)],{},'area_expansion',100)):
            with self.subTest(operation=operation):
                s=session(polygons);j=journal_for(s)
                base,model,context=applied(s,j,operation,[1,2],**kwargs)
                result=evaluate_operation(base,s.state,model,s.model,context);v=values(result)
                self.assertAlmostEqual(v[metric],expected)
                self.assertAlmostEqual(v['boundary_length_reduction'],25 if operation=='merging' else -25)
                self.assertAlmostEqual(v['added_transition_volume'],500 if operation=='merging' else 128)
                self.assertEqual(result['winner'],s.state.operation.winner)
                self.assertIn(str(s.state.operation.winner),result['topology']['after']['cells'])
                if operation=='aggregation':self.assertEqual(len(result['topology']['after']['cells']),1)

    def test_batch_neighbours_and_read_only_topology(self):
        s=session();j=journal_for(s);base,model,context=applied(s,j,'point',[1,3])
        core=_core();ledger=tuple(dhe.dhe);killed=set(dhe.killdhe);history=list(s.history)
        edges={e for m in (model,s.model) for c in m.cells for e in dhe.Cell(c.seed)}
        pointers=[(e,e.S,e.D,e.NF,e.NV,e.V) for e in edges]
        vertices={(e.V,e.V.x,e.V.y,e.V.z) for e in edges}
        faces=[(f,tuple(f.points)) for state in (base,s.state) for f in state.faces]
        with patch.multiple(dhe,**{name:unittest.mock.Mock(side_effect=AssertionError('Evaluation used an operator'))
                                  for name in ('MEVVFS','MEV','MVE','MEF','Sew','JoinByFace','reset_globals')}):
            result=evaluate_operation(base,s.state,model,s.model,context)
        self.assertEqual(len([r for r in result['metrics'] if r['scope'].startswith('Feature')]),4)
        self.assertIn('2',result['topology']['after']['cells']) # changed neighbour
        self.assertEqual(s.history,history);self.assertEqual(_core(),core)
        self.assertEqual(tuple(dhe.dhe),ledger);self.assertEqual(set(dhe.killdhe),killed)
        self.assertTrue(all((e.S,e.D,e.NF,e.NV,e.V)==tuple(saved) for e,*saved in pointers))
        self.assertTrue(all((v.x,v.y,v.z)==(x,y,z) for v,x,y,z in vertices))
        self.assertTrue(all(tuple(f.points)==points for f,points in faces))

    def test_undefined_measurement_is_not_zero(self):
        rows=_feature_metrics('point',Polygon(),box(0,0,1,1).centroid,1)
        self.assertTrue(all(r['value'] is None and r['status']=='unavailable' and r['reason'] for r in rows))


class EvaluationPersistenceTests(unittest.TestCase):
    def test_separate_reports_reloads_and_history_branches(self):
        s=session();j=journal_for(s)
        with tempfile.TemporaryDirectory() as folder:
            records=[]
            for operation,selected in (('smoothing',[3]),('simplification',[3])):
                base,model,context=applied(s,j,operation,selected)
                result=evaluate_and_save(base,s.state,model,s.model,context,folder);j.complete(result);records.append(result)
            s.undo();self.assertEqual(j.current_id(s.cursor),records[0]['evaluation_id'])
            base,model,context=applied(s,j,'point',[4])
            branch=evaluate_and_save(base,s.state,model,s.model,context,folder);j.complete(branch)
            self.assertEqual(branch['history_step'],records[1]['history_step'])
            self.assertEqual(branch['parent_evaluation_id'],records[0]['evaluation_id'])
            self.assertNotEqual(branch['evaluation_id'],records[1]['evaluation_id'])
            self.assertEqual(len(list(Path(folder).rglob('metrics.csv'))),3)
            loaded,warnings=load_evaluations(folder);self.assertFalse(warnings);self.assertEqual(len(loaded),3)
            fresh=EvaluationJournal();reviewed,_=fresh.review(folder);self.assertEqual(len(reviewed),3)
            for record in loaded:
                path=Path(record['_path']);raw=json.loads(path.read_text(encoding='utf-8'))
                with path.with_name('metrics.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
                self.assertEqual(len(rows),len(raw['metrics']))
                for row,metric in zip(rows,raw['metrics']):
                    self.assertEqual(row['evaluation_id'],record['evaluation_id'])
                    self.assertEqual(float(row['value']),metric['value'])
                original=path.read_bytes()
                with self.assertRaises(FileExistsError):save_evaluation(record,folder)
                self.assertEqual(original,path.read_bytes())

    def test_save_and_computation_failures_do_not_modify_applied_model(self):
        s=session();j=journal_for(s);base,model,context=applied(s,j,'smoothing',[3])
        state=s.state;core=_core()
        with tempfile.TemporaryDirectory() as folder:
            blocked=Path(folder)/'file';blocked.write_text('occupied')
            result=evaluate_and_save(base,s.state,model,s.model,context,blocked)
            self.assertEqual(result['status'],'ok');self.assertIn('_save_error',result)
            self.assertTrue(result['metrics']);save_evaluation(result,folder)
            with patch('generalization_evaluation.evaluate_operation',side_effect=RuntimeError('measurement failure')):
                other=dict(context,evaluation_id=__import__('uuid').uuid4().hex)
                failure=evaluate_and_save(base,s.state,model,s.model,other,folder)
            self.assertEqual(failure['status'],'failed');self.assertEqual(failure['metrics'],[])
            self.assertIn('measurement failure',failure['error']);self.assertIn('_path',failure)
        self.assertIs(s.state,state);self.assertEqual(_core(),core)

    def test_atomic_pair_and_unreadable_report(self):
        s=session();j=journal_for(s);base,model,context=applied(s,j,'smoothing',[3])
        record=evaluate_operation(base,s.state,model,s.model,context)
        with tempfile.TemporaryDirectory() as folder:
            with patch('generalization_evaluation.csv.DictWriter.writerow',side_effect=OSError('CSV blocked')):
                with self.assertRaises(OSError):save_evaluation(record,folder)
            self.assertEqual(list(Path(folder).rglob('evaluation.json')),[])
            path=save_evaluation(record,folder)
            path.write_text('{}',encoding='utf-8')
            records,warnings=load_evaluations(folder);self.assertFalse(records);self.assertEqual(len(warnings),1)


if __name__=='__main__':unittest.main()
