"""Mixed intervals, atomic automatic runs, history, and actual DHE sections."""
from dataclasses import asdict
import os
import time
import json
import threading
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box,Polygon
from shapely.ops import unary_union

import DHECore as dhe
from generalization_geometry import prepare_frame
from generalization_model import GeneralizationSession,_core
from automatic_generalization import AutomaticSettings,AutomaticRunError,build_automatic_run,apply_automatic_run
from generalization_visualization import TransitionSnapshot,ViewSettings,visible_outlines
from plane_slicing import PlaneSlicer,SlicePlane
from main import validate_model
from lts_evaluation import evaluate_history


def session(polygons=None,classes=None):
    polygons=polygons or [box(0,0,6,4),box(20,0,26,4),box(26,0,32,4),box(45,0,51,4),box(55,0,61,4)]
    attrs={} if classes is None else {'kind':classes}
    return GeneralizationSession(prepare_frame(gpd.GeoDataFrame(attrs,geometry=polygons,crs=2180)))


class MixedLevelTests(unittest.TestCase):
    def test_simplification_and_merge_share_one_interval(self):
        detailed=Polygon([(0,0),(6,0),(6,4),(3.1,4),(3,4.2),(2.9,4),(0,4)])
        s=session([detailed,box(20,0,26,4),box(26,0,32,4)])
        p=s.preview_level([dict(operation='simplification',selected=[1],tolerance=.5),dict(operation='merging',selected=[2,3],winner=2)])
        self.assertEqual(p.state.z,15);self.assertEqual(set(p.state.features),{1,2})
        self.assertLess(len(p.state.features[1].geometry.exterior.coords),len(detailed.exterior.coords))
        self.assertEqual(p.state.features[2].lineage,(2,3));validate_model(p.model)

    def test_mixed_contacts_merging_aggregation_and_order_independence(self):
        s=session();core=_core();base=s.state
        actions=[dict(operation='point',selected=[1]),dict(operation='merging',selected=[2,3],winner=2),dict(operation='aggregation',selected=[4,5])]
        a=s.preview_level(actions);b=s.preview_level(list(reversed(actions)))
        self.assertIs(s.state,base);self.assertEqual(_core(),core)
        self.assertEqual(a.state.description,b.state.description)
        self.assertEqual([(f.owner,f.points) for f in a.state.faces],[(f.owner,f.points) for f in b.state.faces])
        self.assertEqual(a.state.operation.name,'batch');self.assertEqual(len(a.state.operation.actions),3)
        self.assertEqual(len(validate_model(a.model)),6)
        sections=PlaneSlicer(a.model).sections(SlicePlane.horizontal(15),include_degenerate=True)
        self.assertEqual(sections['1'].geom_type,'Point')
        self.assertLess(sections['2'].symmetric_difference(unary_union([base.features[k].geometry for k in (2,3)])).area,1e-8)
        self.assertEqual(set(a.state.features),{1,2,6})
        for source in (4,5):
            cell=next(c for c in a.model.cells if c.feature_id==str(source))
            self.assertTrue(any(cell.dual_vertex in {x.D.V,y.D.V} and str(x.D.V.i) in (str(source),'6') and str(y.D.V.i) in (str(source),'6') for x,y in a.model.shared_walls))
        s.apply(a);s.undo();self.assertIs(s.state,base);validate_model(s.model)

    def test_duplicate_assignment_and_failure_preserve_original(self):
        s=session();core=_core();base=s.state;history=list(s.history)
        cases=[([dict(operation='smoothing',selected=[1]),dict(operation='point',selected=[1])],'more than one'),
               ([dict(operation='point',selected=[999])],'current source'),
               ([dict(operation='merging',selected=[2,3],winner=1)],'winner')]
        for actions,message in cases:
            with self.assertRaisesRegex(ValueError,message):s.preview_level(actions)
            self.assertIs(s.state,base);self.assertEqual(s.history,history);self.assertEqual(_core(),core)
        with patch.object(dhe,'MEF',side_effect=RuntimeError('allocation failure')):
            with self.assertRaisesRegex(RuntimeError,'allocation'):
                s.preview_level([dict(operation='point',selected=[1]),dict(operation='merging',selected=[2,3],winner=2)])
        self.assertEqual(_core(),core);self.assertIs(s.state,base)

    def test_multiple_aggregates_have_distinct_duals_and_class_provenance(self):
        s=session([box(0,0,5,5),box(8,0,13,5),box(30,0,35,5),box(38,0,43,5)],['a','a','b','b'])
        p=s.preview_level([dict(operation='aggregation',selected=[1,2]),dict(operation='aggregation',selected=[3,4])])
        self.assertEqual(set(p.state.features),{5,6});self.assertEqual(len(validate_model(p.model)),6)
        self.assertEqual(p.state.features[5].attributes['kind'],'a');self.assertEqual(p.state.features[6].attributes['kind'],'b')
        self.assertEqual(p.state.features[5].lineage,(1,2));self.assertEqual(p.state.features[6].lineage,(3,4))
        self.assertEqual(len({c.dual_vertex for c in p.model.cells}),6)

    def test_overlapping_group_targets_reject_the_whole_level(self):
        s=session([box(0,0,2,2),box(10,10,12,12),box(0,10,2,12),box(10,0,12,2)])
        core=_core();base=s.state
        with self.assertRaisesRegex(ValueError,'overlap'):
            s.preview_level([dict(operation='aggregation',selected=[1,2]),dict(operation='aggregation',selected=[3,4])])
        self.assertIs(s.state,base);self.assertEqual(_core(),core)

    def test_mixed_evaluation_and_historical_outline_ancestry(self):
        s=session();p=s.preview_level([dict(operation='point',selected=[1]),dict(operation='merging',selected=[2,3],winner=2),dict(operation='aggregation',selected=[4,5])])
        s.apply(p)
        result=evaluate_history(s.history,s.cursor,'last_operation',s.model,dict(source='test',fingerprint='test'),'test')
        summaries=result['tables']['summary'];point=[r for r in summaries if r['scope'].startswith('Action 1') and r['step']==1][0]
        self.assertIsNone(point['area_m2']);self.assertIn('centroid',point['operation_descriptor'].lower())
        self.assertTrue(any('merging group' in r['scope'] for r in summaries))
        self.assertEqual(len({r['t'] for r in result['tables']['sections']}),11)
        json.dumps(result,allow_nan=False)
        p=s.preview('smoothing',[6]);snap=TransitionSnapshot.capture(p.state,s.history)
        self.assertTrue({4,5,6}<=set(snap.affected))
        self.assertTrue({4,5}<={o.owner for o in visible_outlines(snap,ViewSettings(extent='history'))})


class AutomaticTests(unittest.TestCase):
    def test_final_only_atomic_commit_lineage_history_and_determinism(self):
        s=session();base=s.state;core=_core();history=list(s.history)
        settings=dict(final_kind='polygon')
        a=build_automatic_run(s,list(base.features),settings)
        self.assertIs(s.state,base);self.assertEqual(s.history,history);self.assertEqual(_core(),core)
        b=build_automatic_run(s,list(reversed(base.features)),settings)
        def choices(run):return [(r['scale'],[(x['operation'],x['selected']) for x in r['actions']]) for r in run.report['steps']]
        self.assertEqual(choices(a),choices(b));self.assertGreater(len(a.states),1)
        json.dumps(a.report,allow_nan=False)
        apply_automatic_run(s,a);self.assertEqual(len(s.state.features),1)
        self.assertEqual(set(next(iter(s.state.features.values())).lineage),set(base.features))
        self.assertEqual(s.cursor,len(a.states));validate_model(s.model)
        s.go(0);self.assertIs(s.state,base)
        s.go(len(a.states));self.assertEqual(len(s.state.features),1);validate_model(s.model)

    def test_class_constraint_and_forced_final_dimensions(self):
        s=session([box(0,0,5,5),box(8,0,13,5)],['a','b']);base=s.state;core=_core()
        with self.assertRaisesRegex(ValueError,'matching'):build_automatic_run(s,[1,2],dict(class_field='kind'))
        self.assertIs(s.state,base);self.assertEqual(_core(),core)
        for kind in ('point','polyline'):
            with self.subTest(kind=kind):
                s=session([box(0,0,10,5),box(10,0,20,5)],['a','a'])
                run=build_automatic_run(s,[1,2],dict(class_field='kind',final_kind=kind))
                apply_automatic_run(s,run);feature=next(iter(s.state.features.values()))
                self.assertEqual(feature.geometry.geom_type,'Point' if kind=='point' else 'LineString')
                self.assertEqual(feature.lineage,(1,2));self.assertEqual(feature.attributes['kind'],'a')

    def test_limits_late_failure_and_cancellation_are_atomic(self):
        s=session();base=s.state;core=_core();history=list(s.history)
        with self.assertRaises(AutomaticRunError):build_automatic_run(s,list(base.features),dict(max_levels=1,final_kind='polygon'))
        flag=threading.Event()
        def progress(message):
            if 'private level' in message:flag.set()
        with self.assertRaisesRegex(AutomaticRunError,'cancelled'):
            build_automatic_run(s,list(base.features),cancelled=flag.is_set,progress=progress)
        original=GeneralizationSession.preview_level;count=0
        def fail_later(owner,*args,**kwargs):
            nonlocal count
            count+=1
            if count>1:raise ValueError('late construction failure')
            return original(owner,*args,**kwargs)
        with patch.object(GeneralizationSession,'preview_level',fail_later):
            with self.assertRaises(AutomaticRunError):build_automatic_run(s,list(base.features),dict(max_scale_advances=2,final_kind='polygon'))
        self.assertIs(s.state,base);self.assertEqual(s.history,history);self.assertEqual(_core(),core)

    def test_invalid_settings_and_stale_publication(self):
        for settings in (dict(scale=-1),dict(scale_multiplier=1),dict(final_kind='eliminate'),dict(max_levels=1.5)):
            with self.assertRaises(ValueError):AutomaticSettings(**settings).validate()
        s=session([box(0,0,5,5),box(5,0,10,5)])
        run=build_automatic_run(s,[1,2],dict(final_kind='polygon'))
        s.apply(s.preview('smoothing',[1]))
        with self.assertRaisesRegex(ValueError,'stale'):apply_automatic_run(s,run)

    def test_automatic_final_choice_uses_scale_indicators(self):
        s=session([box(0,0,.2,.2)])
        run=build_automatic_run(s,[1])
        self.assertEqual(run.report['final_geometry'],'Point')
        self.assertIn('source_indicators',run.report['steps'][-1]['actions'][0]['measurements'])
        s=session([box(0,0,20,10)])
        run=build_automatic_run(s,[1],dict(final_kind='polygon'))
        self.assertEqual(run.report['final_geometry'],'Polygon');self.assertFalse(run.states)


@unittest.skipUnless(os.environ.get('DHE_GUI_TESTS')=='1','Set DHE_GUI_TESTS=1 for native controls')
class DesktopWorkflowTests(unittest.TestCase):
    def test_level_editor_and_final_only_automatic_run(self):
        import tkinter as tk
        from generalization_session import GeneralizationWindow
        root=tk.Tk();root.withdraw();window=GeneralizationWindow(root=root,autoload=False)
        errors=[]
        def wait():
            deadline=time.monotonic()+40
            while window.busy and time.monotonic()<deadline:root.update();time.sleep(.02)
            root.update();self.assertFalse(window.busy)
        try:
            with patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:errors.append(a)):
                window._loaded(session())
                window.operation.set('Collapse to point');window._select({1});window.workflow.add()
                window.operation.set('Smoothing');window.workflow.add()
                self.assertEqual(len(window.workflow.actions),1);self.assertIn('more than one',window.status.get())
                window.operation.set('Merging');window._select({2,3});window.winner.set('2');window.workflow.add()
                self.assertEqual(len(window.workflow.actions),2)
                window.compute(False);wait();self.assertEqual(window.session.cursor,0);self.assertEqual(window.proposal.state.operation.name,'batch')
                window.compute(True);self.assertEqual(window.session.cursor,1);self.assertFalse(window.workflow.actions)
                window.undo();wait();self.assertEqual(window.session.cursor,0)
                window._select({1,2,3,4,5});before=window.session.state
                window.workflow.settings=AutomaticSettings(final_kind='polygon')
                window.workflow.dialog();root.update()
                top=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel) and w.title().startswith('Automatic generalization'))
                button=next(w for w in top.winfo_children() if w.winfo_class()=='TButton' and w.cget('text').startswith('Run all levels'))
                button.invoke();self.assertIs(window.session.state,before)
                wait();self.assertEqual(len(window.session.state.features),1)
                self.assertEqual(window.workflow.report['status'],'complete');self.assertFalse(errors,errors)
                validate_model(window.session.model)
        finally:window.close()


if __name__=='__main__':unittest.main()
