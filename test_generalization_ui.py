"""Opt-in native GUI regression: DHE_GUI_TESTS=1 (requires a desktop)."""
import os
import time
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box


@unittest.skipUnless(os.environ.get("DHE_GUI_TESTS")=="1","Set DHE_GUI_TESTS=1 for native Tk controls")
class GeneralizationControlTests(unittest.TestCase):
    def test_selection_preview_apply_history_and_all_plane_controls(self):
        import tkinter as tk
        from generalization_geometry import prepare_frame
        from generalization_model import GeneralizationSession
        from generalization_session import GeneralizationWindow
        root=tk.Tk();root.withdraw()
        window=GeneralizationWindow(root=root,autoload=False)
        errors=[]
        try:
            with patch("tkinter.messagebox.showerror",side_effect=lambda *a,**k:errors.append(a)):
                data=prepare_frame(gpd.GeoDataFrame(geometry=[box(0,0,10,10),box(20,0,30,10)],crs=2180))
                window._loaded(GeneralizationSession(data))
                window._select({1,2});root.update()
                self.assertEqual(window.selected,{1,2})
                window.operation.set("Smoothing");window.compute(False)
                deadline=time.monotonic()+30
                while window.busy and time.monotonic()<deadline:
                    root.update();time.sleep(.01)
                self.assertFalse(window.busy)
                self.assertIsNotNone(window.proposal)
                window.compute(True);root.update()
                self.assertEqual(window.session.state.z,15)
                self.assertFalse(window.show_plane.get())
                window.variables['step'].set('.5')
                window.move_plane(1);root.update()
                self.assertAlmostEqual(window.plane_value.get(),.5)
                self.assertTrue(window.show_plane.get())
                for orientation in ('Vertical','Inclined','Horizontal'):
                    window.orientation.set(orientation);window._plane_range();root.update()
                    self.assertIsNotNone(window._plane())
                window._face(1);window._edge(1);root.update()
                self.assertIn('Half-edge',window.edge_label.cget('text'))
                window.undo()
                deadline=time.monotonic()+30
                while window.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
                self.assertEqual(window.session.state.z,5)
                self.assertEqual(errors,[])
        finally:window.close()


if __name__=='__main__':unittest.main()
