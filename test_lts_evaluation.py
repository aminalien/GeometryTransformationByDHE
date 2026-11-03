"""Manual profiles, Hausdorff bounds, real topology and retained aggregation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import box,LineString
from shapely.ops import unary_union

import DHECore as dhe
from aggregation_geometry import build_aggregation_preview
from aggregation_layer import build_aggregation_model,validate_aggregation_model
from generalization_evaluation import EvaluationJournal
from lts_evaluation import DisplacementReference,evaluate_history,save_report,load_reports,profile_figure,structural_audit,live_audit
from plane_slicing import PlaneSlicer,SlicePlane
from test_generalization import session
from top_face_overlay import cell_volume


def evaluate(s,mode='last_operation',index=None):
    j=EvaluationJournal();j.start_session(s.data,s.origin)
    return evaluate_history(s.history,s.cursor if index is None else index,mode,s.model,j.dataset,j.session_id)


class DisplacementTests(unittest.TestCase):
    def test_analytical_translation_and_budget(self):
        a=box(0,0,2,2);b=box(3,4,5,6);ref=DisplacementReference([a])
        r=ref.compare([b]);self.assertAlmostEqual(r['hausdorff_m'],5)
        self.assertAlmostEqual(r['normalized_hausdorff'],5/np.sqrt(8))
        self.assertEqual(r['hd_sampling_bound_m'],ref.spacing/2)
        r=DisplacementReference([a],max_samples=3).compare([b])
        self.assertIsNone(r['hausdorff_m']);self.assertIn('No coarsening',r['hd_reason'])

    def test_sampling_bound_against_finer_actual_geometry_queries(self):
        a=LineString([(130,0),(0,0),(0,150)]);b=LineString([(10,10),(10,150),(130,10)])
        coarse=DisplacementReference([a],relative_spacing=.01).compare([b])
        fine=DisplacementReference([a],relative_spacing=.0001).compare([b])
        self.assertLessEqual(coarse['hausdorff_m'],fine['hausdorff_m']+fine['hd_sampling_bound_m'])
        self.assertLessEqual(fine['hausdorff_m'],coarse['hausdorff_m']+coarse['hd_sampling_bound_m'])


class ManyToOneTests(unittest.TestCase):
    def test_partially_shared_source_walls_are_conformed_before_sewing(self):
        sources={'a':box(0,0,4,4),'b':box(4,0,6,2),'c':box(4,2,6,4)}
        m=build_aggregation_model(build_aggregation_preview(sources));validate_aggregation_model(m)
        self.assertEqual(len(m.cells),4);self.assertEqual(len(m.shared_walls),6)
        self.assertAlmostEqual(sum(cell_volume(c) for c in m.cells),240)

    def test_standalone_three_lower_cells_duals_interfaces_and_volume(self):
        sources={'a':box(0,0,2,2),'b':box(2,0,4,2),'c':box(7,1,9,3)}
        model=build_aggregation_model(build_aggregation_preview(sources),merge_z=13,top_z=15)
        layer=model.aggregation;validate_aggregation_model(model)
        self.assertEqual(len(model.cells),4);self.assertEqual(len({c.dual_vertex for c in model.cells}),4)
        self.assertAlmostEqual(sum(cell_volume(c) for c in model.cells),layer.expected_volume)
        self.assertAlmostEqual(cell_volume(layer.cell),layer.expected_upper_volume)
        for key,c in layer.source_cells.items():
            self.assertAlmostEqual(cell_volume(c),sources[key].area*5)
            pairs=[(a,b) for a,b in model.shared_walls if {a.D.V,b.D.V}=={c.dual_vertex,layer.cell.dual_vertex}]
            self.assertEqual(len(pairs),1)
            a,b=pairs[0]
            self.assertTrue(all(e.V.z==5 and e.Adjacent().D.V is b.D.V for e in dhe.face_edges(a)))
        slicer=PlaneSlicer(model)
        for side,expected in (('below',set(sources)),('above',{'aggregate'})):
            actual=slicer.sections(SlicePlane.horizontal(5),horizontal_side=side)
            self.assertEqual(set(actual),expected)

    def test_repeated_workspace_aggregation_keeps_prior_cells(self):
        s=session([box(0,0,2,2),box(2,0,4,2),box(7,1,9,3),box(15,0,17,2)])
        s.apply(s.preview('aggregation',[1,2,3]));self.assertEqual(len(s.model.cells),5)
        s.apply(s.preview('aggregation',[4,5]));self.assertEqual(len(s.model.cells),6)
        pairs={frozenset((str(a.D.V.i),str(b.D.V.i))) for a,b in s.model.shared_walls}
        for a,b in ((1,5),(2,5),(3,5),(4,6),(5,6)):self.assertIn(frozenset((str(a),str(b))),pairs)
        slicer=PlaneSlicer(s.model)
        for z,owners in ((2.5,{'1','2','3','4'}),(10,{'4','5'}),(20,{'6'})):
            self.assertEqual(set(slicer.sections(SlicePlane.horizontal(z))),owners)
        s.undo();self.assertEqual(len(s.model.cells),5)

    def test_horizontal_owner_is_independent_of_lexical_id_order(self):
        polygons=[box(i*3,1000,2+i*3,1002) for i in range(11)]
        for k,x in ((0,0),(6,6),(9,12)):polygons[k]=box(x,0,x+2,2)
        s=session(polygons);s.apply(s.preview('aggregation',[1,7,10]))
        slicer=PlaneSlicer(s.model)
        below=slicer.sections(SlicePlane.horizontal(5),horizontal_side='below')
        above=slicer.sections(SlicePlane.horizontal(5),horizontal_side='above')
        self.assertTrue({'1','7','10'}<=set(below));self.assertNotIn('12',below)
        self.assertIn('12',above);self.assertTrue({'1','7','10'}.isdisjoint(above))


class ManualEvaluationTests(unittest.TestCase):
    def test_global_counts_and_invalid_shell_audits(self):
        from dataclasses import replace
        from generalization_model import Face
        s=session([box(0,0,2,2),box(2,0,4,2)])
        row=evaluate(s,'whole_history')['tables']['structure'][0]
        self.assertEqual(tuple(row['retained_'+k] for k in ('vertices','edges','faces','cells')),(12,20,11,2))
        face=s.state.faces[0]
        for faces,key in ((s.state.faces[1:],'missing_edges'),
                          ([*s.state.faces,face],'nonmanifold_edges'),
                          ([Face(face.owner,list(reversed(face.points)),face.part),*s.state.faces[1:]],'misoriented_edges')):
            result=structural_audit(replace(s.state,faces=faces),{})
            self.assertGreater(result[key],0);self.assertGreater(result['invalid_closed_cells'],0)
        edge=s.model.cells[0].seed;old=edge.D.D
        try:
            edge.D.D=edge.S
            self.assertEqual(live_audit(s.model)['live_dual_status'],'Failed')
        finally:edge.D.D=old

    def test_point_profile_mean_has_analytical_value(self):
        s=session([box(0,0,10,5)]);s.apply(s.preview('point',[1]))
        r=evaluate(s);stats=next(v for v in r['profile_summary'] if v['scope']=='Feature 1')
        self.assertAlmostEqual(stats['max_sampled_nhd'],.5,places=7)
        self.assertAlmostEqual(stats['sampled_z_weighted_mean_nhd'],.25,places=7)

    def test_all_cases_actual_sections_and_dimensional_na(self):
        for operation,selection,kwargs in [('simplification',[1],{}),('smoothing',[1],{}),('point',[1],{}),
                ('polyline',[1],{}),('merging',[1,2],{'winner':1}),('aggregation',[1,2],{})]:
            with self.subTest(operation=operation):
                s=session([box(0,0,10,5),box(10 if operation=='merging' else 14,0,20 if operation=='merging' else 24,5)])
                s.apply(s.preview(operation,selection,**kwargs));r=evaluate(s)
                scopes={row['scope'] for row in r['tables']['sections']}
                for scope in scopes:
                    rows=[v for v in r['tables']['sections'] if v['scope']==scope]
                    self.assertEqual([v['t'] for v in rows],[i/10 for i in range(11)])
                if operation in ('point','polyline'):
                    end=next(v for v in r['tables']['sections'] if v['scope']=='Feature 1' and v['t']==1)
                    self.assertIsNone(end['area_m2']);self.assertIsNone(end['perimeter_m'])
                    self.assertIn('N/A',end['polygon_metric_reason']);self.assertIsNotNone(end['hausdorff_m'])
                # Compare a recorded slice independently with live DHE extraction.
                context='Affected context';row=next(v for v in r['tables']['sections'] if v['scope']==context and v['t']==.5)
                actual=PlaneSlicer(s.model).sections(SlicePlane.horizontal(10))
                self.assertAlmostEqual(row['area_m2'],sum(g.area for k,g in actual.items() if int(k) in s.state.operation.affected),places=7)

    def test_mixed_history_current_prefix_missing_terminal_and_read_only(self):
        s=session();s.apply(s.preview('point',[3],dz=5));s.apply(s.preview('smoothing',[4],dz=15))
        core=(dhe.dhe,dhe.killdhe,dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        ledger=tuple(dhe.dhe);history=tuple(s.history);points=[tuple(f.points) for state in history for f in state.faces]
        with patch.multiple(dhe,**{name:unittest.mock.Mock(side_effect=AssertionError('Evaluation mutated DHE')) for name in ('MEVVFS','MEV','MEF','MVE','Sew','JoinByFace','reset_globals')}):r=evaluate(s,'whole_history')
        self.assertEqual(len(r['tables']['sections']),44)
        self.assertEqual(r['tables']['structure'][-1]['missing_terminal_features'],[3])
        self.assertEqual(r['tables']['structure'][-1]['actual_top_features'],3)
        self.assertEqual(tuple(s.history),history);self.assertEqual(tuple(dhe.dhe),ledger)
        self.assertEqual((dhe.dhe,dhe.killdhe,dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),core)
        self.assertEqual([tuple(f.points) for state in history for f in state.faces],points)
        s.undo();earlier=evaluate(s,'whole_history')
        self.assertEqual(earlier['end_index'],1);self.assertEqual(len(earlier['tables']['sections']),22)
        self.assertEqual(earlier['end_step_id'],s.state.step_id)

    def test_initial_only_save_explicit_legacy_and_report_definitions(self):
        s=session();r=evaluate(s,'whole_history')
        self.assertEqual(r['tables']['sections'],[])
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(list(Path(folder).iterdir()),[])
            path=save_report(r,folder)
            for name in ('summary.csv','sections.csv','structure.csv','analysis_report.md'):self.assertTrue(path.with_name(name).is_file())
            text=path.with_name('analysis_report.md').read_text(encoding='utf-8')
            self.assertIn('NHD=Hh/D',text);self.assertIn('Construction time and memory were not measured retrospectively',text)
            with self.assertRaises(FileExistsError):save_report(r,folder)
            loaded,warnings=load_reports(folder);self.assertFalse(warnings);self.assertEqual(loaded[0]['evaluation_id'],r['evaluation_id'])
            path.with_name('structure.csv').unlink()
            loaded,warnings=load_reports(folder);self.assertFalse(loaded);self.assertTrue(warnings)
        s.apply(s.preview('smoothing',[3]));r=evaluate(s)
        figure,_=profile_figure(r,'Feature 3');self.assertEqual(len(figure.axes),3);figure.clear()


if __name__=='__main__':unittest.main()
