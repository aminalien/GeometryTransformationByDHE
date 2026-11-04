"""Input-survivor choices, persistent map zoom, and inspector appearance."""
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import box

import DHECore as dhe
from generalization_geometry import prepare_frame,resolve_overlaps,largest_first_survivors,overlap_pairs,load_shapefile,download_osm
from generalization_model import GeneralizationSession
from plot_dhe_interactive import interactive_plot_from_lists


def chain():
    return gpd.GeoDataFrame({'id':['A','B','C','D']},geometry=[box(0,0,4,4),box(3,0,7,4),box(6,0,10,4),box(0,4,4,5)],crs=2180)


class OverlapTests(unittest.TestCase):
    def test_chain_choices_preserve_indices_attributes_and_original_geometries(self):
        frame=chain();original=frame.copy();data=prepare_frame(frame,overlap_policy='review')
        self.assertEqual(data.conflicts,((1,2),(2,3)))
        self.assertEqual(largest_first_survivors(data),{1,3,4})
        with self.assertRaisesRegex(ValueError,'Resolve input overlaps'):GeneralizationSession(data)
        for selection,expected in (({1,3},{1,3,4}),({2},{2,4}),({1},{1,4})):
            resolved=resolve_overlaps(data,selection)
            self.assertEqual(set(resolved.frame['_index']),expected);self.assertFalse(overlap_pairs(resolved.frame))
            self.assertEqual(set(resolved.overlap_removed),set(range(1,5))-expected)
            for _,row in resolved.frame.iterrows():
                index=int(row['_index'])-1
                self.assertEqual(row['id'],original.iloc[index]['id']);self.assertTrue(row.geometry.equals_exact(original.geometry.iloc[index],0))
            self.assertEqual(len(GeneralizationSession(resolved).model.cells),len(expected))
        self.assertTrue(frame.equals(original));self.assertEqual(len(data.frame),4)
        for invalid in ({1,2},{},{9}):
            with self.assertRaises(ValueError):resolve_overlaps(data,invalid)

    def test_larger_nested_duplicate_and_touching_polygons(self):
        frame=gpd.GeoDataFrame(geometry=[box(1,1,2,2),box(0,0,3,3),box(0,0,3,3),box(3,0,5,3),box(5,3,6,4)],crs=2180)
        data=prepare_frame(frame)
        self.assertEqual(list(data.frame['_index']),[2,4,5])
        self.assertEqual(data.overlap_removed,(1,3));self.assertIn('retained feature(s) 2',data.rejected[0])
        with self.assertRaisesRegex(ValueError,'overlap'):prepare_frame(frame,overlap_policy='error')
        with self.assertRaises(ValueError):prepare_frame(frame,overlap_policy='unsupported')

    def test_shapefile_and_osm_share_review_and_headless_policies(self):
        with patch('generalization_geometry.Path.is_file',return_value=True),patch('generalization_geometry.gpd.read_file',return_value=chain()):
            self.assertTrue(load_shapefile('input.shp',overlap_policy='review').conflicts)
            self.assertEqual(set(load_shapefile('input.shp').frame['_index']),{1,3,4})
        response=unittest.mock.MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        with patch('generalization_geometry.urllib.request.urlopen',return_value=response),patch('generalization_geometry.parse_osm',return_value=(chain(),['parse note'])):
            self.assertTrue(download_osm(0,0,1,1,overlap_policy='review').conflicts)
            result=download_osm(0,0,1,1);self.assertEqual(result.overlap_removed,(2,));self.assertEqual(result.rejected[0],'parse note')


class InspectorAppearanceTests(unittest.TestCase):
    def setUp(self):
        data=prepare_frame(gpd.GeoDataFrame(geometry=[box(0,0,2,2),box(2,0,4,2)],crs=2180))
        self.session=GeneralizationSession(data)
        self.viewer=interactive_plot_from_lists(self.session.model.active_edges(),dhe.Getdhe()[1],show=False,highlight_cells=True)

    def tearDown(self):
        self.viewer._close_dual_style();plt.close(self.viewer.fig)

    def test_dual_defaults_updates_validation_and_topology_unchanged(self):
        v=self.viewer;ledger=tuple(dhe.dhe);camera=(v.ax.elev,v.ax.azim,v.ax.get_xlim(),v.ax.get_ylim(),v.ax.get_zlim())
        with patch.object(v.fig.canvas,'draw_idle'):
            v.checkbox.set_active(0);v.checkbox.set_active(1)
            self.assertTrue(v.dual_vertex_artists);self.assertTrue(v.dual_edge_artists)
            self.assertTrue(all(a.get_sizes()[0]==50 for a in v.dual_vertex_artists))
            self.assertTrue(all(a.get_linewidth()==2.5 for a in v.dual_edge_artists))
            v.set_dual_style(24,1.2)
            self.assertTrue(all(a.get_sizes()[0]==24 for a in v.dual_vertex_artists))
            self.assertTrue(all(a.get_linewidth()==1.2 for a in v.dual_edge_artists))
            for value in (0,-1,float('nan'),float('inf')):
                with self.assertRaises(ValueError):v.set_dual_style(value,1)
            self.assertEqual((v.dual_vertex_size,v.dual_edge_width),(24,1.2))
            v.checkbox.set_active(0);v.checkbox.set_active(0)
            self.assertTrue(all(a.get_sizes()[0]==24 for a in v.dual_vertex_artists))
        self.assertEqual(tuple(dhe.dhe),ledger)
        self.assertEqual((v.ax.elev,v.ax.azim,v.ax.get_xlim(),v.ax.get_ylim(),v.ax.get_zlim()),camera)

    def test_cell_checkbox_and_keyboard_keep_current_face_independent(self):
        v=self.viewer
        with patch.object(v.fig.canvas,'draw_idle'):
            self.assertTrue(v.checkbox.get_status()[5]);v.checkbox.set_active(5)
            self.assertFalse(v.cell_artists);self.assertIsNotNone(v.face_artist)
            self.assertEqual(v.face_artist.get_alpha(),.5)
            v.on_key(SimpleNamespace(key='f'));self.assertIsNotNone(v.face_artist)
            v.checkbox.set_active(5);self.assertTrue(v.cell_artists)
            v.on_key(SimpleNamespace(key='x'));self.assertFalse(v.checkbox.get_status()[5])
            self.assertFalse(v.cell_artists);self.assertIsNotNone(v.face_artist)
            v.on_key(SimpleNamespace(key='c'));self.assertFalse(v.checkbox.get_status()[5])
            v.on_key(SimpleNamespace(key='l'));self.assertIsNone(v.face_artist)
            v.set_fill_all_cells(True);self.assertTrue(v.checkbox.get_status()[5]);self.assertIsNone(v.face_artist)
            v.on_key(SimpleNamespace(key='f'));self.assertIsNotNone(v.face_artist)

    def test_editable_size_fields_apply_only_valid_values(self):
        v=self.viewer
        with patch('matplotlib.figure.Figure.show'),patch.object(v.fig.canvas,'draw_idle'):
            v.open_dual_style();v.dual_size_box.set_val('18');v.dual_width_box.set_val('0.8')
            self.assertEqual((v.dual_vertex_size,v.dual_edge_width),(18,.8))
            v.dual_size_box.set_val('nan')
            self.assertEqual(v.dual_vertex_size,18)
            self.assertIn('positive and finite',v.dual_style_message.get_text())
            v.dual_size_box.set_val('30');self.assertEqual(v.dual_vertex_size,30)
            v._close_dual_style();self.assertIsNone(v.dual_style_figure)


@unittest.skipUnless(os.environ.get('DHE_GUI_TESTS')=='1','Set DHE_GUI_TESTS=1 for native Tk controls')
class InputControlsGuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from generalization_session import GeneralizationWindow
        self.root=tk.Tk();self.root.withdraw();self.window=GeneralizationWindow(root=self.root,autoload=False)
        self.errors=[];self.error_patch=patch('tkinter.messagebox.showerror',side_effect=lambda *a,**k:self.errors.append(a));self.error_patch.start()
        frame=gpd.GeoDataFrame(geometry=[box(0,0,10,10),box(20,0,30,10)],crs=2180)
        self.window._loaded(GeneralizationSession(prepare_frame(frame)))

    def tearDown(self):
        self.window.close();self.error_patch.stop()

    def wait(self):
        deadline=time.monotonic()+30
        while self.window.busy and time.monotonic()<deadline:self.root.update();time.sleep(.01)
        self.root.update();self.assertFalse(self.window.busy);self.assertFalse(self.errors)

    def test_review_cancel_then_keep_middle_before_construction(self):
        w=self.window;old=w.session;history=tuple(old.history);core=tuple(dhe.dhe)
        pending=prepare_frame(chain(),overlap_policy='review')
        w._input_loaded(pending);review=w.overlap_review;self.root.update()
        self.assertIs(w.session,old);self.assertEqual(tuple(dhe.dhe),core)
        self.assertEqual(review.kept,{1,3,4});review.choose(2,True)
        self.assertEqual(review.kept,{2,4});review.cancel()
        self.assertIs(w.session,old);self.assertEqual(tuple(old.history),history)
        with patch.object(w,'_text_dialog'):
            w._input_loaded(pending);review=w.overlap_review
            review.table.selection_set(('1','3'));review.keep_only_selected();self.assertEqual(review.kept,{1,3,4})
            review.choose(2,True);review.confirm();self.wait()
        self.assertEqual(set(w.session.state.features),{2,4});self.assertEqual(w.session.data.overlap_removed,(1,3))

    def test_map_zoom_survives_selection_preview_apply_and_history(self):
        w=self.window;self.root.update();x0=w.map_ax.get_xlim();y0=w.map_ax.get_ylim()
        w._plot_scroll(SimpleNamespace(inaxes=w.map_ax,button='up',xdata=2.,ydata=2.))
        x1=w.map_ax.get_xlim();y1=w.map_ax.get_ylim()
        self.assertAlmostEqual(x1[1]-x1[0],(x0[1]-x0[0])/1.2)
        self.assertAlmostEqual(y1[1]-y1[0],(y0[1]-y0[0])/1.2)
        self.assertFalse(w.show_plane.get());w._select({1});self.root.update()
        np.testing.assert_allclose(w.map_ax.get_xlim(),x1);np.testing.assert_allclose(w.map_ax.get_ylim(),y1)
        w.operation.set('Smoothing');w.compute(False);self.wait()
        np.testing.assert_allclose(w.map_ax.get_xlim(),x1)
        w.compute(True);self.wait();np.testing.assert_allclose(w.map_ax.get_xlim(),x1)
        w.undo();self.wait();np.testing.assert_allclose(w.map_ax.get_xlim(),x1)
        w.zoom_map(-1,2.,2.);np.testing.assert_allclose(w.map_ax.get_xlim(),x0)
        w.fit_map();np.testing.assert_allclose(w.map_ax.get_xlim(),x0)
        toolbar=w.canvas.toolbar;toolbar.zoom();selected=set(w.selected)
        w._rectangle(SimpleNamespace(xdata=-100,ydata=-100),SimpleNamespace(xdata=100,ydata=100))
        self.assertEqual(w.selected,selected);toolbar.zoom()
        previous=w.plane_value.get();w._plot_scroll(SimpleNamespace(inaxes=w.section_ax,button='up'))
        self.assertGreater(w.plane_value.get(),previous)


if __name__=='__main__':unittest.main()
