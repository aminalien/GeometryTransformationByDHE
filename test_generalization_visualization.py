"""Smooth ownership transfer and read-only publication views of actual shells."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import DHECore as dhe
from generalization_model import _core
from generalization_visualization import (TransitionSnapshot,ViewSettings,clip_ring,export_transition,
    render_slices,render_transition,view_bounds,visible_owners)
from methodology_publication import PublicationSettings
from plane_slicing import PlaneSlicer,SlicePlane
from test_generalization import session


class SmoothMergingTests(unittest.TestCase):
    def test_smooth_growth_complementary_shrink_and_preserved_history(self):
        for winner in (1,2):
            with self.subTest(winner=winner):
                s=session();before=s.state;source=s.state.features[winner].geometry
                proposal=s.preview('merging',[1,2],winner=winner,dz=7.5)
                self.assertIs(s.state,before)
                slicer=PlaneSlicer(proposal.model);target=unary_union([before.features[k].geometry for k in (1,2)])
                last=source;loser=3-winner
                for z in np.linspace(5,12.5,9):
                    sections=slicer.sections(SlicePlane.horizontal(z),include_degenerate=True)
                    current=sections[str(winner)]
                    self.assertLess(last.difference(current).area,1e-7)
                    donor=sections.get(str(loser),Polygon())
                    self.assertLess(current.intersection(donor).area,1e-7)
                    self.assertLess(target.symmetric_difference(unary_union([current,donor])).area,1e-7)
                    if 5<z<12.5:self.assertGreater(donor.area,0);self.assertLess(donor.area,100)
                    last=current
                for k in (1,2):self.assertLess(slicer.sections(SlicePlane.horizontal(2.5))[str(k)].symmetric_difference(before.features[k].geometry).area,1e-7)
                self.assertNotIn(loser,proposal.state.features)
                self.assertEqual(proposal.state.operation.retired,(loser,))
                self.assertEqual(proposal.state.operation.winner,winner)
                self.assertEqual(proposal.state.features[winner].lineage,(1,2))
                s.apply(proposal);s.apply(s.preview('smoothing',[winner]))
                self.assertLess(PlaneSlicer(s.model).sections(SlicePlane.horizontal(2.5))[str(loser)].symmetric_difference(before.features[loser].geometry).area,1e-7)
                s.undo();self.assertEqual(s.state.z,12.5)

    def test_multiple_losers_and_concave_union(self):
        s=session([box(0,0,5,5),box(5,0,10,5),box(0,5,5,10),box(20,0,25,5)])
        before=s.state;target=unary_union([before.features[k].geometry for k in (1,2,3)])
        proposal=s.preview('merging',[1,2,3],winner=1)
        slicer=PlaneSlicer(proposal.model)
        for z in (5,7.5,10,12.5,15):
            sections=slicer.sections(SlicePlane.horizontal(z))
            union=unary_union([g for k,g in sections.items() if k in ('1','2','3')])
            self.assertLess(target.symmetric_difference(union).area,1e-7)
        self.assertEqual(proposal.state.operation.retired,(2,3))
        self.assertEqual(set(proposal.state.features),{1,4})

    def test_repeated_merging_keeps_retired_owners(self):
        s=session([box(0,0,5,5),box(5,0,10,5),box(10,0,15,5)])
        old=s.state
        s.apply(s.preview('merging',[1,2],winner=1))
        s.apply(s.preview('merging',[1,3],winner=1))
        self.assertEqual(s.state.features[1].lineage,(1,2,3))
        for k,g in PlaneSlicer(s.model).sections(SlicePlane.horizontal(2.5)).items():
            self.assertLess(g.symmetric_difference(old.features[int(k)].geometry).area,1e-7)

    def test_merge_allocation_failure_leaves_session_and_core_unchanged(self):
        s=session();state=s.state;core=_core();history=list(s.history)
        with patch.object(dhe,'MEF',side_effect=RuntimeError('merge allocation probe')):
            with self.assertRaisesRegex(RuntimeError,'merge allocation probe'):
                s.preview('merging',[1,2],winner=1)
        self.assertIs(s.state,state);self.assertEqual(s.history,history);self.assertEqual(_core(),core)


class PublicationViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=session()
        cls.proposal=cls.session.preview('merging',[1,2],winner=1)
        cls.snapshot=TransitionSnapshot.capture(cls.proposal.state,[cls.session.state])

    def test_snapshot_slicing_matches_live_dhe_without_mutation(self):
        core=_core();before=self.session.state
        actual=PlaneSlicer(self.proposal.model);stored=self.snapshot.slicer()
        for plane in (SlicePlane.horizontal(5),SlicePlane.horizontal(10),SlicePlane.horizontal(15),
                      SlicePlane.from_angles((10,5,7),90,45),SlicePlane.from_angles((10,5,7),35,20)):
            a=actual.sections(plane,include_degenerate=True);b=stored.sections(plane,include_degenerate=True)
            self.assertEqual(set(a),set(b))
            for k,g in a.items():self.assertLess(g.hausdorff_distance(b[k]),1e-7)
        self.assertIs(self.session.state,before);self.assertEqual(_core(),core)
        with patch.object(dhe,'MEVVFS',side_effect=AssertionError('viewer allocated DHE')):
            figure=render_transition(self.snapshot,with_section=True);figure.clear()
        self.assertEqual(_core(),core)

    def test_interval_history_scopes_and_concave_clipping(self):
        self.assertEqual(view_bounds(self.snapshot,ViewSettings()),(0,15))
        self.assertEqual(visible_owners(self.snapshot,ViewSettings()),{1,2})
        self.assertEqual(visible_owners(self.snapshot,ViewSettings(scope='all',hidden=(4,))),{1,2,3})
        with self.assertRaises(ValueError):view_bounds(self.snapshot,ViewSettings(z_b=15))
        ring=[(x,y,y) for x,y in [(0,0),(4,0),(4,4),(3,4),(3,1),(1,1),(1,4),(0,4)]]
        clipped=clip_ring(ring,2,3)
        self.assertEqual(len(clipped),2)
        self.assertAlmostEqual(sum(Polygon([p[:2] for p in f]).area for f in clipped),2)
        for face in clipped:self.assertTrue(all(2-1e-10<=p[2]<=3+1e-10 for p in face))

    def test_later_snapshot_defaults_to_actual_previous_band(self):
        s=session();s.apply(s.preview('smoothing',[3]))
        proposal=s.preview('merging',[1,2],winner=1)
        snapshot=TransitionSnapshot.capture(proposal.state,s.history)
        self.assertEqual(view_bounds(snapshot,ViewSettings()),(5,25))
        self.assertEqual(view_bounds(snapshot,ViewSettings(extent='history')),(0,25))
        s.apply(proposal)
        later=s.preview('smoothing',[1])
        self.assertIn(2,TransitionSnapshot.capture(later.state,s.history).affected)

    def test_vector_raster_exports_levels_and_reproducibility(self):
        settings=PublicationSettings({'layout':{'width_in':4,'height_in':3},'export':{'dpi':100}})
        with tempfile.TemporaryDirectory(dir='artifacts') as output:
            for count,kind in ((6,'2d'),(9,'3d')):
                paths,record=export_transition(self.snapshot,output,count=count,kind=kind,content='slices',settings=settings)
                self.assertEqual(len(paths),3)
                self.assertEqual(len(record['slice_levels']),count)
                self.assertEqual(record['slice_levels'][0],5)
                self.assertEqual(record['slice_levels'][-1],15)
                svg=next(p for p in paths if p.suffix=='.svg');root=ET.parse(svg).getroot()
                self.assertTrue(root.findall('.//{http://www.w3.org/2000/svg}text'))
                self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}image'))
                self.assertGreater(next(p for p in paths if p.suffix=='.pdf').stat().st_size,1000)
                import matplotlib.image as mpimg
                self.assertEqual(mpimg.imread(next(p for p in paths if p.suffix=='.png')).shape[:2],(300,400))
            paths,record=export_transition(self.snapshot,output,settings=settings)
            self.assertEqual(len(paths),3)
            loaded=json.loads(next(Path(output).glob('*_3d.json')).read_text(encoding='utf-8'))
            self.assertEqual(loaded['operation']['retired'],[2])
            pixels=PublicationSettings({'layout':{'width_in':4,'height_in':3},
                'export':{'size_mode':'pixels','width_px':4800,'height_px':3600,'dpi':1200}})
            paths,record=export_transition(self.snapshot,Path(output)/'pixels',settings=pixels)
            self.assertEqual(record['exports'][0]['raster']['dpi'],1200)
            self.assertEqual(mpimg.imread(next(p for p in paths if p.suffix=='.png')).shape[:2],(3600,4800))
            files,record=export_transition(self.snapshot,Path(output)/'panels',count=6,kind='2d',
                content='slices',individual=True,settings=settings)
            self.assertEqual(len(files),21)
            last=next(p for p in files if p.name.endswith('slice06.svg'))
            self.assertIn('(f) Z = 15',''.join(ET.parse(last).getroot().itertext()))

    def test_terminal_point_polyline_and_aggregation_views(self):
        s=session([box(0,0,10,5),box(14,0,24,5)])
        for operation,selection in [('point',[1]),('polyline',[1]),('aggregation',[1,2])]:
            with self.subTest(operation=operation):
                proposal=s.preview(operation,selection)
                snapshot=TransitionSnapshot.capture(proposal.state,[s.state])
                actual=snapshot.slicer().sections(SlicePlane.horizontal(15),include_degenerate=True)
                if operation=='point':self.assertEqual(actual['1'].geom_type,'Point')
                if operation=='polyline':self.assertIn(actual['1'].geom_type,('LineString','MultiLineString'))
                fig=render_slices(snapshot,6,'2d');self.assertEqual(len(fig.axes),6);fig.clear()
                fig=render_slices(snapshot,9,'3d');self.assertEqual(len(fig.axes),9);fig.clear()


@unittest.skipUnless(os.environ.get('DHE_GUI_TESTS')=='1','Set DHE_GUI_TESTS=1 for native Tk controls')
class TransitionViewerControlTests(unittest.TestCase):
    def test_controls_history_preview_exports_and_close(self):
        import tkinter as tk
        from generalization_viewer import TransitionVisualizationWindow
        root=tk.Tk();root.withdraw();s=session();s.apply(s.preview('smoothing',[3]))
        proposal=s.preview('merging',[1,2],winner=1)
        core=_core();window=None
        try:
            window=TransitionVisualizationWindow(root,s,proposal);root.update()
            self.assertFalse(window.show_plane.get())
            window.step.set('.5');window.move(1);root.update()
            self.assertEqual(window.value.get(),15.5);self.assertTrue(window.show_plane.get())
            window.move(-1);self.assertEqual(window.value.get(),15)
            window.operation_menu.current(0);window._change_operation();root.update()
            self.assertEqual(window.snapshot.operation['name'],'smoothing')
            window.operation_menu.current(1);window._change_operation();root.update()
            for orientation in ('Vertical','Inclined','Horizontal'):
                window.orientation.set(orientation);window._orientation_changed();root.update()
            window.extent.set('Total history');window._extent_changed();root.update()
            window.scope.set('All features');window._scope_changed();root.update()
            for presentation in ('2D slice grid','3D slice grid','3D figure','3D + live section'):
                window.presentation.set(presentation);window.redraw();root.update()
            self.assertEqual(_core(),core);self.assertEqual(s.cursor,1)
            window.figure.axes[0].set_xlim((0,25))
            self.assertEqual(window.options().axis_limits[0],(0,25))
            with tempfile.TemporaryDirectory(dir='artifacts') as output,patch('tkinter.filedialog.askdirectory',return_value=output):
                window.variables['width'].set('4');window.variables['height'].set('3');window.variables['dpi'].set('100')
                window.export('2d');self.assertTrue(list(Path(output).glob('*.svg')))
        finally:
            if window:window.close()
            root.update();root.destroy()


if __name__=='__main__':unittest.main()
