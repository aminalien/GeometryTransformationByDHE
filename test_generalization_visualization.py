"""Smooth ownership transfer and read-only publication views of actual shells."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from dataclasses import replace

import matplotlib
matplotlib.use('Agg')
import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import DHECore as dhe
from generalization_model import _core
from generalization_visualization import (TransitionSnapshot,ViewSettings,clip_ring,export_transition,
    render_slices,render_transition,view_bounds,visible_owners,visible_outlines,resolve_slice_levels,dual_geometry)
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

    def test_dual_illustration_adjacency_filters_and_independent_toggles(self):
        core=_core();faces=self.snapshot.faces
        options=ViewSettings(scope='all',z_b=5)
        centres,links=dual_geometry(self.snapshot,options)
        self.assertEqual(set(centres),{1,2,3,4});self.assertEqual(links,((1,2),))
        self.assertTrue(all(5<=p[2]<=15 for p in centres.values()))
        hidden,filtered=dual_geometry(self.snapshot,replace(options,hidden=(2,)))
        self.assertEqual(set(hidden),{1,3,4});self.assertFalse(filtered)
        for vertices,edges in ((False,False),(True,False),(False,True),(True,True)):
            fig=render_transition(self.snapshot,replace(options,show_dual_vertices=vertices,show_dual_halfedges=edges))
            gids={a.get_gid() for a in fig.axes[0].collections if a.get_gid()}
            self.assertEqual({g for g in gids if g.startswith('dual-vertex')},
                             {f'dual-vertex-{k}' for k in centres} if vertices else set())
            self.assertEqual({g for g in gids if g.startswith('dual-halfedge')},
                             {'dual-halfedge-1-2','dual-halfedge-2-1'} if edges else set())
            fig.clear()
        self.assertIs(self.snapshot.faces,faces);self.assertEqual(_core(),core)

    def test_dual_export_and_unchanged_2d_sections(self):
        settings=PublicationSettings({'layout':{'width_in':4,'height_in':3},'export':{'dpi':100}})
        options=ViewSettings(show_dual_vertices=True,show_dual_halfedges=True)
        with tempfile.TemporaryDirectory(dir='artifacts') as output:
            paths,record=export_transition(self.snapshot,output,options=options,settings=settings)
            svg=next(p for p in paths if p.suffix=='.svg').read_text(encoding='utf-8')
            self.assertIn('dual-vertex-1',svg);self.assertIn('dual-halfedge-1-2',svg)
            self.assertTrue(record['view']['show_dual_vertices']);self.assertTrue(record['view']['show_dual_halfedges'])
        fig=render_slices(self.snapshot,6,'2d',options,settings)
        self.assertFalse(any(a.get_gid() for ax in fig.axes for a in ax.collections))
        self.assertFalse(any('Dual' in t.get_text() for legend in fig.legends for t in legend.texts))
        fig.clear()

    def test_custom_slice_ranges_validation_and_display_extent(self):
        self.assertEqual(resolve_slice_levels(self.snapshot,ViewSettings(),6),list(np.linspace(5,15,6)))
        for count in (6,9):
            options=ViewSettings(slice_z_start=2.,slice_z_end=12.)
            levels=resolve_slice_levels(self.snapshot,options,count)
            np.testing.assert_allclose(levels,np.linspace(2,12,count))
            for kind in ('2d','3d'):
                figure=render_slices(self.snapshot,count,kind,options)
                self.assertEqual([a.get_title() for a in figure.axes],
                    [f'({i+1}) Z = {z:g}' for i,z in enumerate(levels)])
                figure.clear()
        for start,end in ((-1,10),(5,16),(7,7),(9,8),(float('nan'),10),(5,float('inf'))):
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                resolve_slice_levels(self.snapshot,ViewSettings(slice_z_start=start,slice_z_end=end),6)
        s=session();s.apply(s.preview('smoothing',[3]))
        p=s.preview('merging',[1,2],winner=1);snap=TransitionSnapshot.capture(p.state,s.history)
        options=ViewSettings(slice_z_start=2,slice_z_end=20)
        with self.assertRaisesRegex(ValueError,'Total history'):resolve_slice_levels(snap,options,6)
        self.assertEqual(resolve_slice_levels(snap,replace(options,extent='history'),6)[0],2)
        # Grid limits do not change the live section or scene-only render.
        figure=render_transition(snap,replace(options,value=17));figure.clear()

    def test_custom_range_exports_and_individual_numbers(self):
        settings=PublicationSettings({'layout':{'width_in':4,'height_in':3},'export':{'dpi':100}})
        options=ViewSettings(slice_z_start=2,slice_z_end=12)
        with tempfile.TemporaryDirectory(dir='artifacts') as output:
            paths,record=export_transition(self.snapshot,output,options=options,count=6,
                content='slices',kind='2d',individual=True,settings=settings)
            self.assertEqual(record['slice_range'],[2,12])
            self.assertEqual(record['slice_levels'],list(np.linspace(2,12,6)))
            self.assertEqual(record['view']['slice_z_start'],2)
            self.assertTrue(all('range2_12' in p.name for p in paths))
            last=next(p for p in paths if p.name.endswith('slice06.svg'))
            self.assertIn('(6) Z = 12',''.join(ET.parse(last).getroot().itertext()))
            metadata=json.loads(next(Path(output).glob('*6slices.json')).read_text())
            self.assertEqual(metadata['slice_range'],[2,12])

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
            self.assertIn('(6) Z = 15',''.join(ET.parse(last).getroot().itertext()))

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

    def test_wireframe_coordinate_numbers_and_zero_grid_gaps(self):
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        options=ViewSettings(fill_cells=False,outlines=False,slice_wspace=0,slice_hspace=0)
        figure=render_transition(self.snapshot,options);figure.canvas.draw()
        collections=[c for c in figure.axes[0].collections if isinstance(c,Poly3DCollection)]
        self.assertTrue(collections)
        for c in collections:
            self.assertEqual(len(c.get_facecolor()),0)
            self.assertTrue(any(color[3]>0 for color in c.get_edgecolor()))
        figure.clear()
        for kind in ('2d','3d'):
            figure=render_slices(self.snapshot,6,kind,options)
            self.assertEqual(figure.subplotpars.wspace,0);self.assertEqual(figure.subplotpars.hspace,0)
            for ax in figure.axes:
                self.assertFalse(any(t.get_text() for t in [*ax.get_xticklabels(),*ax.get_yticklabels()]))
            figure.clear()
        figure=render_slices(self.snapshot,6,'2d',replace(options,slice_xy_numbers=True))
        self.assertTrue(any(t.get_text() for ax in figure.axes for t in ax.get_xticklabels()))
        figure.clear()


class HistoryOutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=session()
        cls.session.apply(cls.session.preview('smoothing',[3]))
        cls.session.apply(cls.session.preview('merging',[1,2],winner=1))
        cls.proposal=cls.session.preview('smoothing',[1])
        cls.states=(*cls.session.history,cls.proposal.state)
        cls.snapshot=TransitionSnapshot.capture(cls.proposal.state,cls.session.history)

    def test_saved_sections_and_shared_endpoint_deduplication(self):
        options=ViewSettings(extent='history',scope='all')
        outlines=visible_outlines(self.snapshot,options)
        self.assertEqual({o.level for o in outlines},{5,15,25,35})
        for state in self.states:
            actual=PlaneSlicer.from_face_records((str(f.owner),f.points) for f in state.faces)
            for owner,geometry in actual.sections(SlicePlane.horizontal(state.z),include_degenerate=True).items():
                boundary=geometry.boundary if geometry.geom_type in ('Polygon','MultiPolygon') else geometry
                matching=[o for o in outlines if o.level==state.z and o.owner==int(owner) and o.geometry.equals(boundary)]
                self.assertEqual(len(matching),1)
        interval=visible_outlines(self.snapshot,replace(options,extent='interval'))
        self.assertEqual({o.level for o in interval},{25,35})
        self.assertEqual(visible_outlines(self.snapshot,replace(options,outlines=False)),())

    def test_hidden_features_ancestry_and_earlier_history(self):
        options=ViewSettings(extent='history')
        eligible=visible_owners(self.snapshot,options)
        self.assertIn(2,eligible)  # Earlier merge loser remains a contributing owner.
        self.assertNotIn(3,eligible)
        outlines=visible_outlines(self.snapshot,options)
        self.assertEqual({o.owner for o in outlines},eligible)
        hidden=visible_outlines(self.snapshot,replace(options,hidden=(2,)))
        self.assertNotIn(2,{o.owner for o in hidden})
        earlier=TransitionSnapshot.capture(self.states[1],self.states[:1])
        self.assertEqual({o.level for o in visible_outlines(earlier,replace(options,scope='all'))},{5,15})
        aggregate=session([box(0,0,5,5),box(8,0,13,5)])
        aggregate.apply(aggregate.preview('aggregation',[1,2]))
        owner=max(aggregate.state.features)
        next_step=aggregate.preview('smoothing',[owner])
        snapshot=TransitionSnapshot.capture(next_step.state,aggregate.history)
        self.assertEqual(set(snapshot.affected),{1,2,owner})
        low=[o for o in visible_outlines(snapshot,options) if o.level==5]
        self.assertEqual({o.owner for o in low},{1,2})

    def test_cached_capture_and_rendering_are_read_only(self):
        core=_core();history=tuple(self.session.history);cache={}
        original=PlaneSlicer.from_face_records
        with patch.object(PlaneSlicer,'from_face_records',wraps=original) as sections:
            for i,state in enumerate(self.states[1:],1):
                TransitionSnapshot.capture(state,self.states[:i],outline_cache=cache)
            self.assertEqual(sections.call_count,4)  # One extraction per unique saved endpoint.
        with patch.multiple(dhe,**{name:unittest.mock.Mock(side_effect=AssertionError('Visualization mutated DHE'))
                                  for name in ('MEVVFS','MEV','MVE','MEF','Sew','SewComplex','JoinByFace')}):
            snapshot=TransitionSnapshot.capture(self.states[-1],self.states[:-1])
            options=ViewSettings(extent='history',scope='all',cut_caps=False,show_axes=False)
            for linked in (False,True):
                figure=render_transition(snapshot,options,with_section=linked)
                levels={float(line.get_data_3d()[2][0]) for line in figure.axes[0].lines}
                self.assertEqual(levels,{5,15,25,35});figure.clear()
            figure=render_transition(snapshot,replace(options,outlines=False))
            self.assertFalse(figure.axes[0].lines);figure.clear()
        self.assertEqual(_core(),core);self.assertEqual(tuple(self.session.history),history)

    def test_numbered_history_grid_and_export_metadata(self):
        options=ViewSettings(extent='history',scope='all',slice_z_start=5,slice_z_end=35)
        settings=PublicationSettings({'layout':{'width_in':5,'height_in':4},'export':{'dpi':100}})
        for count in (6,9):
            levels=resolve_slice_levels(self.snapshot,options,count)
            figure=render_slices(self.snapshot,count,'3d',options,settings)
            self.assertEqual([a.get_title() for a in figure.axes],[f'({i+1}) Z = {z:g}' for i,z in enumerate(levels)])
            for axis in figure.axes:
                drawn={float(line.get_data_3d()[2][0]) for line in axis.lines if line.get_linewidth()==1.8}
                self.assertEqual(drawn,{5,15,25,35})
            figure.clear()
            individual=render_slices(self.snapshot,count,'2d',options,settings,levels=[35])
            self.assertEqual(individual.axes[0].get_title(),f'({count}) Z = 35');individual.clear()
        with tempfile.TemporaryDirectory(dir='artifacts') as output:
            paths,record=export_transition(self.snapshot,output,options=options,settings=settings,count=9,kind='3d',content='slices')
            self.assertEqual(record['outline_levels'],[5,15,25,35])
            svg=next(p for p in paths if p.suffix=='.svg')
            text=''.join(ET.parse(svg).getroot().itertext())
            self.assertIn('(1) Z = 5',text);self.assertIn('(9) Z = 35',text)
            metadata=json.loads(next(Path(output).glob('*.json')).read_text())
            self.assertEqual(metadata['outline_levels'],record['outline_levels'])
            _,disabled=export_transition(self.snapshot,Path(output)/'disabled',options=replace(options,outlines=False),settings=settings)
            self.assertEqual(disabled['outline_levels'],[])


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
            self.assertFalse(window.slice_xy_numbers.get())
            self.assertFalse(window.show_dual_vertices.get());self.assertFalse(window.show_dual_halfedges.get())
            window.show_dual_vertices.set(True);window.show_dual_halfedges.set(True)
            window.redraw();root.update()
            self.assertTrue(window.options().show_dual_vertices);self.assertTrue(window.options().show_dual_halfedges)
            self.assertTrue(any((a.get_gid() or '').startswith('dual-vertex') for a in window.figure.axes[0].collections))
            window.variables['slice_z_start'].set('7');window.variables['slice_z_end'].set('22')
            self.assertEqual(window.options().slice_z_start,7)
            window.presentation.set('2D slice grid');window.redraw();root.update()
            self.assertIn('Z = 7',window.figure.axes[0].get_title())
            figure=window.figure;current=window.value.get()
            window.variables['slice_z_start'].set('2');window.redraw();root.update()
            self.assertIs(window.figure,figure)
            self.assertIn('Total history',window.status.get())
            self.assertEqual(window.value.get(),current)
            window.extent.set('Total history');window._extent_changed();root.update()
            self.assertIn('Z = 2',window.figure.axes[0].get_title())
            current=window.value.get();window.reset_slice_range();root.update()
            self.assertEqual(window.value.get(),current)
            self.assertEqual(window.options().slice_z_start,15)
            window.presentation.set('3D + live section')
            window.extent.set('Latest interval Zb–Zt');window._extent_changed();root.update()
            window.fill_cells.set(False);window.redraw();root.update()
            self.assertFalse(window.options().fill_cells)
            window.variables['slice_wspace'].set('0');window.variables['slice_hspace'].set('0')
            self.assertEqual(window.options().slice_hspace,0)
            window.step.set('.5');window.move(1);root.update()
            self.assertEqual(window.value.get(),15.5);self.assertTrue(window.show_plane.get())
            window.move(-1);self.assertEqual(window.value.get(),15)
            window.operation_menu.current(0);window._change_operation();root.update()
            self.assertEqual(window.snapshot.operation['name'],'smoothing')
            self.assertEqual(window.options().slice_z_start,5)
            self.assertEqual(window.options().slice_z_end,15)
            window.operation_menu.current(1);window._change_operation();root.update()
            for orientation in ('Vertical','Inclined','Horizontal'):
                window.orientation.set(orientation);window._orientation_changed();root.update()
            window.extent.set('Total history');window._extent_changed();root.update()
            window.scope.set('All features');window._scope_changed();root.update()
            for presentation in ('2D slice grid','3D slice grid','3D figure','3D + live section'):
                window.presentation.set(presentation);window.redraw();root.update()
                if 'slice grid' in presentation:
                    self.assertTrue(window.figure.axes[0].get_title().startswith('(1)'))
                if presentation!='2D slice grid':
                    levels={float(line.get_data_3d()[2][0]) for line in window.figure.axes[0].lines if line.get_linewidth()==1.8}
                    self.assertEqual(levels,{5,15,25})
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
