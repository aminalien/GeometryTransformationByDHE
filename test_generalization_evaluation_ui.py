"""Native Apply-only integration; opt in with DHE_GUI_TESTS=1."""
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
        while self.window.busy and time.monotonic()<deadline:
            self.root.update();time.sleep(.01)
        self.root.update();self.assertFalse(self.window.busy)

    def tearDown(self):
        if not self.window.closed:
            self.wait();self.window.close()
        self.error_patch.stop();self.temp.cleanup()

    def test_preview_apply_direct_apply_history_branch_and_reopen(self):
        w=self.window;w._select({1});w.operation.set('Smoothing');w.compute(False);self.wait()
        self.assertEqual(list(Path(self.temp.name).rglob('evaluation.json')),[])
        self.assertIsNone(w.evaluation_window)
        w.compute(True);self.wait()
        first=w.evaluation_window.record
        self.assertEqual(len(first['metrics']),6)
        self.assertEqual(len(w.evaluation_window.table.get_children()),6)
        self.assertTrue(Path(first['_path']).is_file());self.assertEqual(first['selected'],[1])
        w._select({3});w.operation.set('Collapse to point');w.compute(True);self.wait()
        second=w.evaluation_window.record
        self.assertEqual(second['parent_evaluation_id'],first['evaluation_id'])
        w.undo();self.wait()
        self.assertEqual(w.evaluation_window.record['evaluation_id'],first['evaluation_id'])
        self.assertEqual(len(list(Path(self.temp.name).rglob('evaluation.json'))),2)
        w._select({3});w.operation.set('Simplification');w.compute(True);self.wait()
        branch=w.evaluation_window.record
        self.assertEqual(branch['history_step'],second['history_step'])
        self.assertEqual(branch['parent_evaluation_id'],first['evaluation_id'])
        self.assertNotEqual(branch['evaluation_id'],second['evaluation_id'])
        w.evaluation_window.close();w.review_evaluations()
        self.assertEqual(len(w.evaluation_window.records),3)
        self.assertEqual(self.errors,[])

    def test_all_operation_callbacks_and_failed_apply(self):
        w=self.window
        cases=[('Simplification',{1}),('Smoothing',{1}),('Collapse to point',{3}),
               ('Collapse to polyline',{3}),('Merging',{1,2}),('Aggregation',{1,2})]
        # Use edge-connected sources for merge; separated sources for aggregation.
        for name,selected in cases:
            with self.subTest(operation=name):
                polygons=[box(0,0,10,5),box(10 if name=='Merging' else 14,0,20 if name=='Merging' else 24,5),box(30,0,40,5)]
                w._loaded(session(polygons));w._select(selected);w.operation.set(name)
                if name=='Merging':w.winner.set('2')
                w.compute(True);self.wait()
                self.assertEqual(w.evaluation_window.record['history_step'],1)
                self.assertEqual(w.evaluation_window.record['status'],'ok')
        count=len(list(Path(self.temp.name).rglob('evaluation.json')))
        w._loaded(session([box(0,0,2,2),box(6,0,8,2)]));w._select({1,2})
        w.operation.set('Merging');w.winner.set('1');w.compute(True);self.wait()
        self.assertEqual(w.session.cursor,0)
        self.assertEqual(len(list(Path(self.temp.name).rglob('evaluation.json'))),count)
        self.assertEqual(len(self.errors),1)

    def test_save_failure_retry_and_new_window_loads_saved_reports(self):
        w=self.window;w._select({3});w.operation.set('Collapse to point')
        with patch('generalization_evaluation.save_evaluation',side_effect=OSError('write blocked')):
            w.compute(True);self.wait()
        record=w.evaluation_window.record
        self.assertEqual(w.session.cursor,1);self.assertEqual(record['status'],'ok')
        self.assertIn('write blocked',record['_save_error'])
        self.assertEqual(len(w.evaluation_window.table.get_children()),6)
        w.evaluation_window.retry_save();self.wait()
        self.assertNotIn('_save_error',w.evaluation_window.record)
        self.assertEqual(w.evaluation_window.record['evaluation_id'],record['evaluation_id'])
        from generalization_evaluation import EvaluationJournal
        w.evaluation_window.close();w.evaluation_journal=EvaluationJournal();w.review_evaluations()
        self.assertEqual(len(w.evaluation_window.records),1)
        self.assertEqual(w.evaluation_window.record['evaluation_id'],record['evaluation_id'])
        self.assertEqual(self.errors,[])

    def test_close_finishes_pending_evaluation(self):
        from threading import Event
        from generalization_evaluation import evaluate_and_save
        gate=Event();w=self.window;w._select({3});w.operation.set('Smoothing')
        w.compute(False);self.wait()
        def delayed(*args):
            gate.wait(5)
            return evaluate_and_save(*args)
        try:
            with patch('generalization_evaluation.evaluate_and_save',side_effect=delayed):
                w.compute(True);w.close()
                self.assertFalse(w.closed);self.assertTrue(w.close_after_evaluation)
                gate.set();deadline=time.monotonic()+40
                while not w.closed and time.monotonic()<deadline:self.root.update();time.sleep(.01)
                self.assertTrue(w.closed)
            self.assertEqual(len(list(Path(self.temp.name).rglob('evaluation.json'))),1)
            self.assertEqual(self.errors,[])
        finally:gate.set()


if __name__=='__main__':unittest.main()
