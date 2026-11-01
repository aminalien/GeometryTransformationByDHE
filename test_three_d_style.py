"""Shared 3D appearance, metadata-only level guides and export regressions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
import numpy as np
from shapely.geometry import box

import DHECore as dhe
from generalization_model import _core
from generalization_session import render_overview
from generalization_visualization import TransitionSnapshot,ViewSettings,render_transition,render_slices,export_transition
from methodology_publication import PublicationSettings
from plane_slicing import PlaneSlicer,SlicePlane
from plot_dhe_interactive import interactive_plot_from_lists
from test_generalization import session
from three_d_style import style_3d,levels_from_record,guide_available


class SharedStyleTests(unittest.TestCase):
    def test_white_numeric_axes_and_independent_guide_preserve_geometry(self):
        fig=Figure();FigureCanvasAgg(fig);ax=fig.add_subplot(111,projection='3d')
        fig.set_facecolor('black');ax.grid(True)
        for axis in (ax.xaxis,ax.yaxis,ax.zaxis):axis.pane.fill=False
        ax.set(xlim=(0,2),ylim=(0,3),zlim=(-2,12));ax.view_init(elev=27,azim=31)
        line,=ax.plot([0,1],[0,2],[0,10],color='red')
        before=tuple(ax.get_xlim()),tuple(ax.get_ylim()),tuple(ax.get_zlim()),ax.elev,ax.azim
        levels=dict(z_b=0,z_s=5,z_m=9,z_t=10)
        labels=style_3d(ax,levels)
        self.assertFalse(labels);self.assertTrue(ax._axis3don);self.assertFalse(ax._draw_grid)
        self.assertEqual(fig.get_facecolor(),to_rgba('white'))
        self.assertEqual(ax.get_facecolor(),to_rgba('white'))
        for axis in (ax.xaxis,ax.yaxis,ax.zaxis):
            self.assertEqual(axis.pane.get_facecolor(),to_rgba('white'));self.assertTrue(axis.pane.fill)
        self.assertEqual([ax.get_xlabel(),ax.get_ylabel(),ax.get_zlabel()],['X','Y','Z'])
        self.assertEqual(len(ax.get_xticks()),0);self.assertEqual(len(ax.get_yticks()),0)
        self.assertEqual([t.get_text() for t in ax.get_zticklabels()],['0','5','9','10'])
        labels=style_3d(ax,levels,show_axes=False,show_z_transition=True)
        self.assertFalse(ax._axis3don);self.assertEqual([t.get_text() for t in labels],['0','5','9','10'])
        self.assertFalse(ax.get_zticklabels())
        self.assertTrue(all(t.get_visible() for t in labels));count=len(ax.texts)
        style_3d(ax,levels,show_axes=False,show_z_transition=True)
        self.assertEqual(len(ax.texts),count)
        self.assertEqual((tuple(ax.get_xlim()),tuple(ax.get_ylim()),tuple(ax.get_zlim()),ax.elev,ax.azim),before)
        self.assertEqual(line.get_color(),'red');fig.canvas.draw()
        style_3d(ax,levels);self.assertTrue(ax._axis3don);self.assertFalse(ax._dhe_z_guide)
        self.assertEqual([t.get_text() for t in ax.get_zticklabels()],['0','5','9','10'])
        fig.clear()

    def test_missing_transition_metadata_does_not_invent_a_guide(self):
        fig=Figure();FigureCanvasAgg(fig);ax=fig.add_subplot(111,projection='3d');ax.set_zlim(0,5)
        self.assertFalse(guide_available(dict(z_b=0,z_s=5)))
        self.assertFalse(style_3d(ax,dict(z_b=0,z_s=5),show_z_transition=True));fig.clear()

    def test_close_levels_are_separated_on_small_panels(self):
        fig=Figure(figsize=(3,2.5));FigureCanvasAgg(fig);ax=fig.add_subplot(111,projection='3d');ax.set_zlim(0,35)
        labels=style_3d(ax,dict(z_b=0,z_s=25,z_m=33,z_t=35),show_z_transition=True,tick_size=10)
        self.assertGreater(ax.zaxis.get_major_ticks()[3].get_pad(),ax.zaxis.get_major_ticks()[2].get_pad())
        by_value={a._dhe_z_value:a for a in labels}
        self.assertGreater(by_value[35].get_position()[1],by_value[33].get_position()[1])
        self.assertTrue(all(a.xy[0]<.1 for a in labels));fig.clear()

    def test_generalization_metadata_aggregation_and_export_flags(self):
        s=session([box(0,0,4,4),box(8,0,12,4)])
        proposal=s.preview('aggregation',[1,2]);saved=_core()
        snapshot=TransitionSnapshot.capture(proposal.state,s.history)
        self.assertEqual(snapshot.operation['z_m'],13)
        options=ViewSettings(show_axes=False,show_z_transition=True)
        fig=render_transition(snapshot,options);ax=fig.axes[0]
        self.assertFalse(ax._axis3don);self.assertEqual(ax._dhe_levels['z_m'],13)
        self.assertTrue(ax._dhe_z_guide);fig.clear()
        for count in (6,9):
            fig=render_slices(snapshot,count,'3d',replace(options,show_axes=True,slice_xy_numbers=True))
            self.assertEqual(len(fig.axes),count)
            for ax in fig.axes:
                self.assertFalse(ax._draw_grid);self.assertFalse(len(ax.get_xticks()));self.assertFalse(len(ax.get_yticks()))
                self.assertIn(13,ax.get_zticks())
            fig.clear()
        with tempfile.TemporaryDirectory(dir='artifacts') as folder:
            files,record=export_transition(snapshot,folder,options=options,
                settings=PublicationSettings({'layout':{'width_in':5,'height_in':4},'export':{'dpi':100}}))
            self.assertEqual(len(files),3)
            self.assertFalse(record['view']['show_axes']);self.assertTrue(record['view']['show_z_transition'])
        self.assertEqual(_core(),saved)
        s.apply(proposal);fig=render_overview(s,show_axes=False,show_z_transition=True)
        self.assertFalse(fig.axes[1]._axis3don);self.assertTrue(fig.axes[1]._dhe_z_guide);fig.clear()

    def test_inspector_toggles_and_navigation_keep_style(self):
        s=session();s.apply(s.preview('merging',[1,2],winner=1));saved=_core()
        viewer=interactive_plot_from_lists(s.model.active_edges(),dhe.Getdhe()[1],show=False,
            levels=levels_from_record(s.state.operation))
        try:
            viewer.checkbox.set_active(3);viewer.checkbox.set_active(4)
            self.assertFalse(viewer.ax._axis3don);self.assertTrue(viewer.ax._dhe_z_guide)
            viewer.on_key(SimpleNamespace(key='f'))
            self.assertFalse(viewer.ax._axis3don);self.assertFalse(viewer.ax._draw_grid)
            self.assertEqual(_core(),saved)
        finally:plt.close(viewer.fig)

    def test_methodology_numeric_levels_and_guide_without_axes(self):
        from methodology_figure_generator import Repository,render,ViewOptions
        repo=Repository();fig=render(repo.get('agg'),3,'lifted_facet_plan',ViewOptions(show_axes=False,show_z_transition=True))
        try:
            ax=fig.axes[0];self.assertFalse(ax._axis3don);self.assertFalse(ax._draw_grid)
            self.assertEqual({t.get_text() for t in ax._dhe_z_guide if hasattr(t,'_dhe_z_value')},{'0','1','1.8','2'})
            self.assertFalse(any(a.get_visible() for key,a in fig._publication_artists.items() if key.endswith(('/xaxis_label','/yaxis_label'))))
            fig.canvas.draw()
        finally:fig.clear()


@unittest.skipUnless(os.environ.get('DHE_GUI_TESTS')=='1','Native Tk controls require DHE_GUI_TESTS=1')
class NativeAppearanceTests(unittest.TestCase):
    def test_workspace_and_transition_viewer_toggles(self):
        import tkinter as tk
        from generalization_session import GeneralizationWindow
        root=tk.Tk();root.withdraw();window=GeneralizationWindow(root=root,autoload=False)
        try:
            s=session();s.apply(s.preview('merging',[1,2],winner=1));window._loaded(s);root.update()
            window.show_axes.set(False);window.show_z_transition.set(True);window.draw();root.update()
            self.assertFalse(window.ax._axis3don);self.assertTrue(window.ax._dhe_z_guide)
            viewer=window.visualize_transition();root.update()
            viewer.show_axes.set(False);viewer.show_z_transition.set(True);viewer.redraw();root.update()
            self.assertFalse(viewer.figure.axes[0]._axis3don);self.assertTrue(viewer.figure.axes[0]._dhe_z_guide)
            viewer.show_axes.set(True);viewer.redraw();root.update();self.assertTrue(viewer.figure.axes[0]._axis3don)
        finally:window.close()


class PaperAppearanceTests(unittest.TestCase):
    def test_geometry_preview_stage_navigation_and_export(self):
        import main as app
        if not Path(app.input_shp).is_file():self.skipTest('Local paper Shapefile unavailable')
        from paper_session import PaperSession
        from test_paper_viewer import arguments
        paper=PaperSession(arguments(),transformation='aggregation',step='gaps')
        try:
            paper.viewer.set_3d_options(show_axes=False,show_z_transition=True)
            self.assertFalse(paper.viewer.ax._axis3don);self.assertTrue(paper.viewer.ax._dhe_z_guide)
            self.assertTrue(paper.switch_stage('input'))
            self.assertFalse(paper.viewer.ax._axis3don);self.assertTrue(paper.viewer.ax._dhe_z_guide)
            with tempfile.TemporaryDirectory(dir='artifacts') as folder:
                files=paper.export_figure(directory=folder)
                self.assertTrue(files)
                data=json.loads(next(p for p in files if p.suffix=='.json').read_text())
                self.assertFalse(data['show_axes']);self.assertTrue(data['show_z_transition']);self.assertEqual(data['z_levels']['z_m'],9)
        finally:plt.close(paper.figure)


if __name__=='__main__':unittest.main()
