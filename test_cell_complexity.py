"""Closed range topology, operation lifetimes, filtering and export compatibility."""
from copy import deepcopy
import csv
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import Polygon,box
from shapely.errors import GEOSException

import DHECore as dhe
from cell_complexity import range_counts
from generalization_model import Face,GeneralizationSession,_node_faces,_prism
from lts_evaluation import cell_complexity_rows,load_reports,save_report
from lts_evaluation_viewer import _table_rows
from test_generalization import session
from test_lts_evaluation import evaluate


def counts(values):return tuple(values[k] for k in ('vertices','edges','faces'))


class RangeCountTests(unittest.TestCase):
    def test_prism_caps_subdivisions_and_read_only(self):
        faces=_prism(1,box(0,0,2,2),0.,5.)
        saved=deepcopy(faces)
        for lo,hi in ((0,5),(1,4),(0,4),(1,5)):
            self.assertEqual(counts(range_counts(faces,lo,hi)),(8,12,6))
        self.assertEqual(faces,saved)
        wall=faces.pop();a,b,c,d=wall.points
        m=tuple((x+y)/2 for x,y in zip(a,b));n=tuple((x+y)/2 for x,y in zip(c,d))
        faces.extend([Face(1,[a,m,n,d],'wall'),Face(1,[m,b,c,n],'wall')])
        faces=_node_faces(faces)
        self.assertEqual(counts(range_counts(faces,1,4)),(10,15,7))
        for bounds in ((6,8),(3,3),(5,0),(0,float('inf'))):
            with self.assertRaises(ValueError):range_counts(faces,*bounds)
        with self.assertRaises(ValueError):range_counts(faces[:-1],1,4)

    def test_concave_faces_split_into_disconnected_portions(self):
        section=Polygon([(0,0),(4,0),(4,4),(3,4),(3,1),(1,1),(1,4),(0,4)])
        faces=[Face(1,[(x,z,y) for x,y,z in reversed(f.points)],f.part)
               for f in _prism(1,section,0.,1.)]
        self.assertEqual(counts(range_counts(faces,2,3)),(16,24,12))

    def test_caps_with_holes_are_not_triangulated(self):
        faces=[f for f in _prism(1,box(0,0,4,4),0.,4.) if f.part!='top']
        for panel in (box(0,0,4,1),box(0,3,4,4),box(0,1,1,3),box(3,1,4,3)):
            faces.extend(f for f in _prism(1,panel,0.,4.) if f.part=='top')
        for f in _prism(1,box(1,1,3,3),2.,4.):
            if f.part!='top':faces.append(Face(1,list(reversed(f.points)),f.part))
        faces=_node_faces(faces)
        # Four outer walls, four cavity walls, and two annular caps.
        self.assertEqual(counts(range_counts(faces,3,3.5)),(16,24,10))


class ComparisonTableTests(unittest.TestCase):
    def test_group_baselines_sum_shared_cells_and_single_result(self):
        for op in ('merging','aggregation'):
            with self.subTest(operation=op):
                s=session([box(0,0,2,2),box(2 if op=='merging' else 4,0,4 if op=='merging' else 6,2)])
                s.apply(s.preview(op,[1,2],**({'winner':1} if op=='merging' else {})))
                report=evaluate(s,feature_ids=[2],consider_feature_family=False)
                rows=report['tables']['cell_complexity'];self.assertEqual(len(rows),1)
                row=rows[0];self.assertEqual(row['status'],'ok',row['reason'])
                self.assertEqual(row['baseline_cell_ids'],[1,2])
                self.assertEqual(tuple(row['baseline_'+k] for k in ('vertices','edges','faces')),(16,24,12))
                self.assertEqual(row['result_cell_id'],s.state.operation.winner)
                actual=range_counts([f for f in s.state.faces if f.owner==row['result_cell_id']],5,15)
                for key in ('vertices','edges','faces'):
                    self.assertEqual(row['upper_'+key],actual[key])
                    self.assertAlmostEqual(row[key+'_change_pct'],100*(actual[key]-row['baseline_'+key])/row['baseline_'+key])
                self.assertEqual(_table_rows(report,'cell_complexity','Feature 2','1'),rows)
                self.assertEqual(_table_rows(report,'cell_complexity','Feature 2','0'),[])

    def test_original_ancestors_lifetimes_same_id_and_late_birth(self):
        s=session([box(0,0,2,2),box(4,0,6,2),box(8,0,10,2),box(20,0,22,2)])
        s.apply(s.preview('aggregation',[1,2]));first=s.state.operation.winner
        s.apply(s.preview('smoothing',[4]))
        s.apply(s.preview('aggregation',[first,3]));second=s.state.operation.winner
        report=evaluate(s,'whole_history');rows=report['tables']['cell_complexity']
        first_row=next(r for r in rows if r['result_cell_id']==first)
        second_row=next(r for r in rows if r['result_cell_id']==second)
        self.assertEqual(first_row['lifetime_end_step'],2)
        self.assertEqual(first_row['actual_upper_z_max'],25)
        self.assertEqual(second_row['baseline_cell_ids'],[1,2,3])
        self.assertEqual(second_row['baseline_vertices'],24)
        self.assertEqual(second_row['actual_upper_z_min'],25)
        self.assertEqual(second_row['upper_z_min'],5)
        self.assertEqual(second_row['status'],'ok',second_row['reason'])
        s.undo();earlier=evaluate(s,'whole_history')['tables']['cell_complexity']
        self.assertNotIn(second,[r['result_cell_id'] for r in earlier])
        s=session([box(0,0,2,2),box(2,0,4,2),box(4,0,6,2)])
        s.apply(s.preview('merging',[1,2],winner=1));s.apply(s.preview('merging',[1,3],winner=1))
        rows=evaluate(s,'whole_history')['tables']['cell_complexity']
        self.assertEqual([r['lifetime_end_step'] for r in rows],[1,2])
        self.assertEqual(rows[1]['baseline_cell_ids'],[1,2,3])

    def test_custom_height_terminal_counts_mixed_actions_and_no_mutation(self):
        s=session([box(0,0,2,2),box(4,0,6,2),box(8,0,10,2)])
        s=GeneralizationSession(s.data,initial_height=3.)
        s.apply(s.preview_level([dict(operation='aggregation',selected=[1,2]),dict(operation='point',selected=[3])],dz=7))
        history=tuple(s.history);faces=deepcopy([state.faces for state in history])
        ledger=tuple(dhe.dhe);counters=(dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        with patch.object(dhe,'reset_globals',side_effect=AssertionError('Evaluation allocated DHE')):
            report=evaluate(s)
        rows=report['tables']['cell_complexity'];self.assertEqual(len(rows),2)
        self.assertEqual({r['action_index'] for r in rows},{1,2})
        self.assertTrue(all(r['baseline_z_max']==3 and r['upper_z_min']==3 and r['status']=='ok' for r in rows))
        point=next(r for r in rows if r['operation']=='point')
        self.assertEqual(tuple(point['upper_'+k] for k in ('vertices','edges','faces')),(5,8,5))
        self.assertLess(point['vertices_change_pct'],0)
        self.assertEqual(tuple(s.history),history);self.assertEqual([state.faces for state in history],faces)
        self.assertEqual(tuple(dhe.dhe),ledger);self.assertEqual((dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),counters)

    def test_zero_denominator_and_invalid_boundary_are_unavailable(self):
        s=session([box(0,0,2,2)]);s.apply(s.preview('point',[1]))
        scopes={'Selected':({1},False)}
        with patch('cell_complexity.range_counts',return_value=dict(vertices=0,edges=0,faces=0,actual_z_min=5,actual_z_max=15)):
            row=cell_complexity_rows(s.history,[1],scopes)[0]
        self.assertIsNone(row['vertices_change_pct']);self.assertIn('baseline is zero',row['reason'])
        with patch('cell_complexity.range_counts',side_effect=ValueError('Not closed')):
            row=cell_complexity_rows(s.history,[1],scopes)[0]
        self.assertEqual(row['status'],'unavailable');self.assertIsNone(row['upper_vertices'])
        self.assertIn('Not closed',row['reason'])
        with patch('cell_complexity.range_counts',side_effect=GEOSException('Cut failed')):
            row=cell_complexity_rows(s.history,[1],scopes)[0]
        self.assertEqual(row['status'],'unavailable');self.assertIn('Cut failed',row['reason'])

    def test_exports_roundtrip_and_older_reports(self):
        s=session([box(0,0,2,2)]);s.apply(s.preview('point',[1]));report=evaluate(s)
        with tempfile.TemporaryDirectory() as folder:
            path=save_report(report,folder);loaded,warnings=load_reports(folder)
            self.assertFalse(warnings)
            self.assertEqual(loaded[0]['tables']['cell_complexity'],report['tables']['cell_complexity'])
            with path.with_name('cell_complexity.csv').open(encoding='utf-8-sig',newline='') as stream:
                row=next(csv.DictReader(stream))
            self.assertEqual(row['upper_vertices'],'5')
            self.assertIn('100(upper-baseline)/baseline',path.with_name('analysis_report.md').read_text(encoding='utf-8'))
            path.with_name('cell_complexity.csv').unlink()
            self.assertTrue(load_reports(folder)[1])
        old=deepcopy(report);old['tables'].pop('cell_complexity')
        with tempfile.TemporaryDirectory() as folder:
            save_report(old,folder);loaded,warnings=load_reports(folder)
            self.assertFalse(warnings);self.assertEqual(_table_rows(loaded[0],'cell_complexity'),[])
        self.assertEqual(evaluate(session(),'whole_history')['tables']['cell_complexity'],[])


if __name__=='__main__':unittest.main()
