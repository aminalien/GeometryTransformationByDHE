"""Current-face fill stays independent of background cell shading."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

import DHECore as dhe
from plot_dhe_interactive import interactive_plot_from_lists
from test_collapse_targets import make_model
from viewer_session import ViewerSession


class CurrentFaceFillTests(unittest.TestCase):
    def setUp(self):
        self.session = ViewerSession(make_model("point"), make_model)
        self.viewer = self.session.viewer
        self.fig = self.viewer.fig

    def tearDown(self):
        plt.close(self.fig)

    def assert_face_filled(self, viewer=None):
        viewer = viewer or self.viewer
        artist = viewer.face_artist
        self.assertIsNotNone(artist)
        self.assertIs(artist, viewer.selected_face_artist)
        self.assertIs(artist.axes, viewer.ax)
        self.assertEqual(artist.get_alpha(), 0.5)
        self.assertEqual(tuple(artist.get_facecolor()[0][:3]), viewer.e.D.V.color[:3])
        self.assertNotIn(artist, viewer.cell_artists)
        key = tuple(sorted((e.V.x,e.V.y,e.V.z) for e in dhe.face_edges(viewer.e)))
        self.assertIsNone(viewer.scene_faces[key]["artist"])
        self.assertEqual(sum(a is artist for a in viewer.ax.collections), 1)

    def key(self, value):
        self.viewer.on_key(SimpleNamespace(key=value))

    def test_current_face_is_filled_without_any_cell_shading_at_startup(self):
        model = self.session.model
        viewer = interactive_plot_from_lists(
            model.active_edges(), dhe.Getdhe()[1], show=False, highlight_cells=False)
        try:
            self.assertFalse(viewer.cell_artists)
            self.assertFalse(viewer.shaded_features)
            self.assert_face_filled(viewer)
        finally:
            plt.close(viewer.fig)

    def test_x_and_subsequent_navigation_always_retain_current_face(self):
        with patch.object(self.fig.canvas, "draw_idle"):
            original = self.viewer.face_artist
            self.key("x")
            self.assertIs(original, self.viewer.face_artist)
            for value in ("x", "f", "s", "a", "v", "o", "e", "k", "h"):
                with self.subTest(key=value):
                    self.key(value)
                    self.assertFalse(self.viewer.cell_artists)
                    self.assertFalse(self.viewer.shaded_features)
                    self.assert_face_filled()

    def test_l_clears_fill_until_a_new_selection_even_within_same_face(self):
        with patch.object(self.fig.canvas, "draw_idle"):
            old = self.viewer.face_artist
            face_key = self.viewer._selected_face_key
            self.key("l")
            self.assertIsNone(old.axes)
            self.assertIsNone(self.viewer.face_artist)
            self.assertIsNone(self.viewer.selected_face_artist)
            self.viewer._update_face_fill()
            self.viewer._draw_highlight_line()
            for value in ("x", "c", "h"):
                self.key(value)
                self.assertIsNone(self.viewer.face_artist)
                self.assertTrue(all(r["artist"] is None for key,r in self.viewer.scene_faces.items()
                                    if key == face_key))
            self.key("f")
            self.assert_face_filled()
            self.assertEqual(self.viewer._selected_face_key, face_key)
            self.key("l")
            self.viewer._set_current_edge(self.viewer.e)
            self.assert_face_filled()

    def test_projection_edge_id_and_3d_click_restore_fill_after_l(self):
        self.fig.canvas.draw()
        with patch.object(self.fig.canvas, "draw_idle"):
            self.key("x")
            panel = self.viewer.overlay_panel
            facet = next(f for f in self.session.model.transition.connecting_facets if f.geometry.area > 1e-6)
            point = facet.geometry.representative_point()
            self.key("l")
            panel.on_click(SimpleNamespace(inaxes=panel.ax,button=1,xdata=point.x,ydata=point.y))
            self.assert_face_filled()
            edge = next(e for e in self.viewer.loop_edges if e.D.V is not self.viewer.e.D.V)
            self.key("l")
            self.viewer.text_box.set_val(edge.info)
            self.assertIs(self.viewer.e, edge)
            self.assert_face_filled()
            self.key("l")
            a,b = facet.seed.V,facet.seed.S.V
            px,py,_ = proj3d.proj_transform(
                (a.x+b.x)/2,(a.y+b.y)/2,(a.z+b.z)/2,self.viewer.ax.get_proj())
            xy = self.viewer.ax.transData.transform((px,py))
            for name in ("button_press_event","button_release_event"):
                event = MouseEvent(name,self.fig.canvas,*xy,button=1)
                self.fig.canvas.callbacks.process(name,event)
            self.assert_face_filled()
            self.assertFalse(self.viewer.cell_artists)

    def test_artist_cleanup_and_single_background_layer(self):
        with patch.object(self.fig.canvas, "draw_idle"):
            old = self.viewer.face_artist
            old_key = self.viewer._selected_face_key
            count = len(self.viewer.ax.collections)
            self.key("s")
            self.assertIsNone(old.axes)
            self.assert_face_filled()
            background = self.viewer.scene_faces[old_key]["artist"]
            self.assertIsNotNone(background)
            self.assertEqual(background.get_alpha(), 0.42)
            self.assertEqual(len(self.viewer.ax.collections), count)
            selected = self.viewer.face_artist
            for _ in range(10):
                self.viewer._update_face_fill()
                self.viewer._draw_highlight_line()
            self.assertIs(self.viewer.face_artist, selected)
            self.assertEqual(len(self.viewer.ax.collections), count)
            self.key("x")
            self.assertEqual([a for a in self.viewer.ax.collections if isinstance(a, Poly3DCollection)],
                             [selected])

    def test_all_targets_and_exterior_keep_face_fill_after_clearing_cells(self):
        with patch.object(self.fig.canvas, "draw_idle"):
            for kind in ("polyline", "polygon", "point"):
                with self.subTest(target=kind):
                    self.assertTrue(self.session.switch_target(kind), self.session.last_error)
                    viewer = self.session.viewer
                    viewer.on_key(SimpleNamespace(key="x"))
                    self.assert_face_filled(viewer)
                    outer = next(e for e in self.session.model.active_edges()
                                 if e.Adjacent().D.V is self.session.model.exterior)
                    viewer._set_current_edge(outer.Adjacent())
                    viewer._draw_highlight_line()
                    self.assert_face_filled(viewer)
                    self.assertEqual(tuple(viewer.face_artist.get_facecolor()[0][:3]),
                                     self.session.model.exterior.color[:3])
                    self.assertFalse(viewer.cell_artists)


if __name__ == "__main__":
    unittest.main()
