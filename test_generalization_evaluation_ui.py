"""Native manual Evaluate/Save integration (DHE_GUI_TESTS=1)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from shapely.geometry import box
from test_generalization import session


@unittest.skipUnless(os.environ.get('DHE_GUI_TESTS')=='1','Set DHE_GUI_TESTS=1 for native Tk controls')
class EvaluationControlTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from generalization_session import GeneralizationWindow
        self.root=tk.Tk();self.root.withdraw();self.window=GeneralizationWindow(root=self.root,autoload=False)
        self.temp=tempfile.TemporaryDirectory();self.window.variables['evaluation_folder'].set(self.temp.name)
        self.window._loaded(session([box(0,0,10,5),box(14,0,24,5),box(30,0,40,5)]))
        self.errors=[];self.error_patch=patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:self.errors.append(a))
        self.error_patch.start()

    def wait(self):
        deadline=time.monotonic()+40
        while self.window.busy and time.monotonic()<deadline:self.root.update();time.sleep(.01)
        self.root.update();self.assertFalse(self.window.busy)

    def tearDown(self):
        if not self.window.closed:self.wait();self.window.close()
        self.error_patch.stop();self.temp.cleanup()

    def apply(self,operation='Smoothing',selection=(1,)):
        self.window._select(set(selection));self.window.operation.set(operation)
        self.window.compute(True);self.wait()

    def test_no_automatic_analysis_or_files_and_explicit_run_save(self):
        w=self.window
        with patch('lts_evaluation_viewer.evaluate_history',side_effect=AssertionError('Automatic evaluation')):
            w._select({1});w.operation.set('Smoothing');w.compute(False);self.wait()
            w.compute(True);self.wait()
            self.assertIsNone(w.evaluation_window)
            w.review_evaluations();self.root.update();self.assertIsNone(w.evaluation_window.record)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])
        e=w.evaluation_window;e.run();self.wait()
        self.assertEqual(e.record['mode'],'last_operation')
        self.assertEqual(len(e.tables['sections'].get_children()),22)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])
        e.save();self.wait()
        self.assertTrue(Path(e.record['_path']).is_file())
        self.assertTrue(Path(e.record['_path']).with_name('analysis_report.md').is_file())
        self.assertEqual(self.errors,[])

    def test_current_history_prefix_branch_and_saved_review(self):
        w=self.window;self.apply();w.review_evaluations();e=w.evaluation_window
        e.run();self.wait();first=e.record
        self.apply('Collapse to point',(3,));self.assertIs(e.record,first)
        e.mode.set('Whole history');e.run();self.wait();later=e.record
        self.assertEqual(later['end_index'],2);self.assertEqual(len(later['tables']['sections']),44)
        w.undo();self.wait();self.assertIs(e.record,later)
        e.run();self.wait();self.assertEqual(e.record['end_index'],1)
        self.apply('Simplification',(3,));e.run();self.wait();branch=e.record
        self.assertNotEqual(branch['end_step_id'],later['end_step_id'])
        e.save();self.wait();e.close();w.review_evaluations();w.evaluation_window.refresh()
        self.assertTrue(any(r['evaluation_id']==branch['evaluation_id'] for r in w.evaluation_window.records))
        self.assertEqual(self.errors,[])

    def test_feature_picker_family_expansion_and_history_refresh(self):
        w=self.window;self.apply();w.review_evaluations();e=w.evaluation_window
        w._select({2,3});e.use_workspace_selection()
        self.assertEqual(e.feature_mode.get(),'Choose features')
        self.assertEqual([e.feature_indices[i] for i in e.feature_list.curselection()],[2,3])
        e.run();self.wait();report=e.record
        self.assertEqual(report['feature_selection']['requested_ids'],[2,3])
        self.assertEqual(len(report['tables']['sections']),33)
        e.feature_list.selection_clear(0,'end');e.run();self.wait()
        self.assertIs(e.record,report);self.assertIn('at least one feature',e.status.cget('text'))
        self.apply('Aggregation',(1,2));aggregate=w.session.state.operation.winner
        self.assertIn(1,e.feature_indices);self.assertIn(aggregate,e.feature_indices)
        self.assertIn('retired',e.feature_list.get(e.feature_indices.index(1)))
        w._select({aggregate});e.use_workspace_selection();e.run();self.wait()
        self.assertEqual(e.record['feature_selection']['expanded_cell_ids'],[1,2])
        self.assertIn('additional family cell IDs',e.info.cget('text'))
        e.save();self.wait();self.assertTrue(Path(e.record['_path']).is_file())
        with patch('tkinter.filedialog.askdirectory',return_value=self.temp.name), \
             patch('methodology_publication.save_formats',return_value=([],{},{})):
            e.plot()
        import json
        metadata=json.loads(next(Path(self.temp.name).glob('lts_profile_*.json')).read_text(encoding='utf-8'))
        self.assertEqual(metadata['feature_selection']['requested_ids'],[aggregate])
        self.assertEqual(metadata['scope_definition']['cell_ids'],[1,2,aggregate])
        w.undo();self.wait();self.assertNotIn(aggregate,e.feature_indices)
        previous=e.record;e.run();self.wait();self.assertIs(e.record,previous)
        e.feature_mode.set('All features');e._feature_mode_changed();e.run();self.wait()
        self.assertNotIn('feature_selection',e.record)
        w._select({1});e.use_workspace_selection()
        w._loaded(session([box(100,0,110,5),box(120,0,130,5)]))
        self.assertFalse(e.feature_list.curselection())
        self.assertEqual(self.errors,[])

    def test_optional_family_checkbox_captures_policy_without_remeasuring(self):
        import json
        w=self.window;self.apply('Aggregation',(1,2));aggregate=w.session.state.operation.winner
        w.review_evaluations();e=w.evaluation_window
        self.assertTrue(e.consider_feature_family.get());self.assertIn('disabled',e.family_check.state())
        w._select({aggregate});e.use_workspace_selection();self.assertNotIn('disabled',e.family_check.state())
        e.run();self.wait();family=e.record
        self.assertEqual(family['feature_selection']['expanded_cell_ids'],[1,2])
        e.consider_feature_family.set(False);self.root.update()
        self.assertIs(e.record,family)
        e.run();e.consider_feature_family.set(True);self.wait();exact=e.record
        self.assertEqual(exact['feature_selection']['policy'],'exact_ids')
        self.assertFalse(exact['feature_selection']['consider_feature_family'])
        self.assertEqual(exact['feature_selection']['expanded_cell_ids'],[])
        self.assertIn('policy: exact IDs',e.info.cget('text'))
        self.assertEqual(set(exact['scope_definitions']),{f'Feature {aggregate}'})
        e.save();self.wait()
        self.assertEqual(json.loads(Path(exact['_path']).read_text())['feature_selection']['policy'],'exact_ids')
        with patch('tkinter.filedialog.askdirectory',return_value=self.temp.name), \
             patch('methodology_publication.save_formats',return_value=([],{},{})):
            e.plot()
        metadata=json.loads(next(Path(self.temp.name).glob('lts_profile_*.json')).read_text())
        self.assertEqual(metadata['feature_selection']['policy'],'exact_ids')
        self.assertEqual(metadata['scope_definition']['cell_ids'],[aggregate])
        family['feature_selection'].pop('consider_feature_family')
        e.show(family);self.assertIn('policy: full feature family',e.info.cget('text'))
        e.feature_mode.set('All features');e._feature_mode_changed()
        self.assertIn('disabled',e.family_check.state())
        self.assertEqual(self.errors,[])

    def test_six_operation_requests_save_failure_and_retry(self):
        w=self.window;w.review_evaluations();e=w.evaluation_window
        cases=[('Simplification',{1}),('Smoothing',{1}),('Collapse to point',{3}),
               ('Collapse to polyline',{3}),('Merging',{1,2}),('Aggregation',{1,2})]
        for name,selected in cases:
            with self.subTest(operation=name):
                w._loaded(session([box(0,0,10,5),box(10 if name=='Merging' else 14,0,20 if name=='Merging' else 24,5),box(30,0,40,5)]))
                w._select(selected);w.operation.set(name)
                if name=='Merging':w.winner.set('2')
                w.compute(True);self.wait();e.mode.set('Last operation');e.run();self.wait()
                self.assertEqual(e.record['end_index'],1)
                self.assertEqual(len(e.record['tables']['sections'])%11,0)
        identifier=e.record['evaluation_id']
        with patch('lts_evaluation_viewer.save_report',side_effect=OSError('write blocked')):e.save();self.wait()
        self.assertIn('write blocked',e.status.cget('text'));self.assertEqual(w.session.cursor,1)
        e.save();self.wait();self.assertEqual(e.record['evaluation_id'],identifier)
        self.assertTrue(Path(e.record['_path']).is_file());self.assertEqual(self.errors,[])

    def test_initial_whole_history_and_close_finishes_explicit_save(self):
        from threading import Event
        from lts_evaluation import save_report
        w=self.window;w.review_evaluations();e=w.evaluation_window
        e.run();self.wait();self.assertIsNone(e.record)
        self.assertIn('Apply an operation',e.status.cget('text'))
        e.mode.set('Whole history');e.run();self.wait();self.assertEqual(e.record['tables']['sections'],[])
        gate=Event()
        def delayed(*args):gate.wait(5);return save_report(*args)
        try:
            with patch('lts_evaluation_viewer.save_report',side_effect=delayed):
                e.save();w.close();self.assertFalse(w.closed);gate.set();deadline=time.monotonic()+40
                while not w.closed and time.monotonic()<deadline:self.root.update();time.sleep(.01)
                self.assertTrue(w.closed)
            self.assertEqual(len(list(Path(self.temp.name).rglob('evaluation.json'))),1)
        finally:gate.set()


if __name__=='__main__':unittest.main()
