"""Exact collapsed targets, selected-face materials and target-session controls."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backend_bases import KeyEvent
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union

import main as app
import DHECore as dhe
from active_region_triangulation import triangulate_active_regions
from top_face_overlay import overlay_top_faces, face_polygon, roof_faces, cell_volume
from transition_layer import build_transition_layer, validate_transition_layer
from transition_targets import TargetDefinition, LINE_SHP
from plane_slicing import PlaneSlicer, SlicePlane
from viewer_session import ViewerSession


def make_base():
    dhe.reset_globals()
    exterior = dhe.make_vertex(6,6,5,ch="VINF",clr=(0.55,0.2,0.75,1))
    rings = [
        [(1,1),(3,1),(3,3),(1,3)],
        [(0,0),(1,0),(1,1),(1,3),(1,4),(0,4)],
        [(1,0),(3,0),(3,1),(1,1)],
        [(3,0),(4,0),(4,4),(3,4),(3,3),(3,1)],
        [(1,3),(3,3),(3,4),(1,4)],
    ]
    cells = [app.extrude_polygon(Polygon(r),str(i+1),5,exterior,app.CELL_COLORS[i])
             for i,r in enumerate(rings)]
    return app.ExtrusionModel(cells,exterior,(0,0),app.sew_shared_walls(cells))


def make_model(kind="point"):
    model = make_base()
    if kind == "polygon":
        geometry = Polygon([(2,1.5),(3.5,1.5),(3.5,2.5),(2,2.5)])
        overlay_top_faces(model,geometry,source_id=1,target_id=9,z=5)
        triangulate_active_regions(model)
        build_transition_layer(model,target=TargetDefinition(kind,"9",geometry))
    else:
        geometry = Point(2,2) if kind == "point" else LineString([(1.6,2.4),(1.5,1.8),(2.4,1.7)])
        build_transition_layer(model,target=TargetDefinition(kind,"target",geometry),
                               source_id="1",source_z=5)
    return model


class CollapsedTargetTests(unittest.TestCase):
    def test_point_and_line_are_closed_shared_cells_without_zero_area_caps(self):
        for kind in ("point","polyline"):
            with self.subTest(kind=kind):
                model = make_model(kind)
                layer = model.transition
                self.assertEqual(len(model.cells),5)
                self.assertEqual(layer.target_kind,kind)
                self.assertFalse([f for f in layer.upper_facets if f.cell_id=="1"])
                for cell in model.cells:
                    horizontal = [f for f in dhe.get_UniqueFace_in_cell(cell.seed)
                                  if len({e.V.z for e in dhe.face_edges(f)})==1]
                    self.assertEqual(len(horizontal),1 if cell.feature_id=="1" else 2)
                validate_transition_layer(model)
                slicer = PlaneSlicer(model)
                terminal = slicer.sections(SlicePlane.horizontal(10),include_degenerate=True)
                self.assertTrue(terminal["1"].equals(layer.target_geometry))
                self.assertNotIn("1",slicer.sections(SlicePlane.horizontal(10)))
                domain = unary_union(list(model.overlay.original_roofs.values()))
                for z in (0,5,5.01,6.25,7.5,8.75,9.99,10):
                    sections = slicer.sections(SlicePlane.horizontal(z),include_degenerate=True)
                    self.assertLess(unary_union(list(sections.values())).symmetric_difference(domain).area,1e-7)
                    self.assertAlmostEqual(sum(p.area for p in sections.values()),domain.area)
                for tilt,azimuth in ((90,0),(90,45),(45,40),(70,150)):
                    sections = slicer.sections(SlicePlane.from_angles(slicer.center,tilt,azimuth))
                    self.assertAlmostEqual(sum(p.area for p in sections.values()),
                                           unary_union(list(sections.values())).area)

    def test_point_sections_follow_analytic_quadratic_shrinkage(self):
        model = make_model("point")
        layer = model.transition
        for z in (5,6,7.5,9,9.99):
            self.assertAlmostEqual(layer.horizontal_section(z).area,
                                   layer.source_geometry.area*((10-z)/5)**2,places=8)
        self.assertAlmostEqual(cell_volume(layer.cell),
                               layer.source_geometry.area*(5+5/3),places=8)
        self.assertEqual(len(layer.connecting_facets),4)

    def test_unsupported_targets_fail_without_mutating_topology(self):
        model = make_base()
        before = tuple((e,e.NF,e.S,e.D,e.V) for e in model.active_edges())
        counts = (dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        bad = [
            TargetDefinition("point","bad",Point(5,5)),
            TargetDefinition("polyline","bad",LineString([(0,0),(4,4)])),
            TargetDefinition("polyline","bad",LineString([(1.2,1.2),(2.8,2.8),(1.2,2.8),(2.8,1.2)])),
            TargetDefinition("polyline","bad",LineString([(1.2,1.2),(2.8,1.2),(2,2.8),(1.2,1.2)])),
        ]
        for target in bad:
            with self.subTest(target=target.geometry.wkt),self.assertRaises(ValueError):
                build_transition_layer(model,target=target,source_id="1")
            self.assertEqual(before,tuple((e,e.NF,e.S,e.D,e.V) for e in model.active_edges()))
            self.assertEqual(counts,(dhe.Vertex.cntId,dhe.DualHalfEdge.cntId))
            self.assertIsNone(model.transition)

    def test_selected_face_uses_cell_colour_and_fifty_percent_opacity(self):
        session = ViewerSession(make_model(),make_model)
        viewer = session.viewer
        try:
            with patch.object(viewer.fig.canvas,"draw_idle"):
                viewer._draw_highlight_line()
                selected = viewer.selected_face_artist
                self.assertIsNotNone(selected)
                self.assertEqual(selected.get_alpha(),0.5)
                self.assertEqual(tuple(selected.get_facecolor()[0][:3]),viewer.e.D.V.color[:3])
                count = len(viewer.cell_artists)
                viewer.e = viewer.e.S
                viewer._draw_highlight_line()
                self.assertIsNot(selected,viewer.selected_face_artist)
                self.assertIsNone(selected.axes)
                self.assertEqual(viewer.selected_face_artist.get_alpha(),0.5)
                self.assertEqual(len(viewer.cell_artists),count)
                self.assertFalse(viewer.face_halfedge_artists)
                self.assertEqual(viewer.current_halfedge_line.get_color(),"red")
        finally:
            plt.close(viewer.fig)

    def test_hidden_startup_live_2d_auto_enable_and_manual_hide(self):
        session = ViewerSession(make_model(),make_model)
        viewer = session.viewer
        controls = viewer.plane_controls
        try:
            self.assertFalse(controls.show_slicing_3d)
            self.assertIsNone(controls.plane_artist)
            self.assertFalse(controls.section_artists)
            self.assertTrue(controls.current_section.area)
            with patch.object(viewer.fig.canvas,"draw_idle"):
                actions = [
                    lambda: controls.position_slider.set_val(8),
                    lambda: viewer.on_key(SimpleNamespace(key="up")),
                    lambda: controls.on_scroll(SimpleNamespace(inaxes=controls.ax,step=1)),
                    lambda: controls.raise_button._observers.process("clicked",None),
                    lambda: controls.orientation_dropdown.select("Inclined"),
                    lambda: controls.tilt_slider.set_val(30),
                ]
                for action in actions:
                    action()
                    self.assertTrue(controls.show_slicing_3d)
                    self.assertTrue(controls.visibility_checkbox.get_status()[0])
                    self.assertIsNotNone(controls.plane_artist)
                    controls.visibility_checkbox.set_active(0)
                    self.assertFalse(controls.show_slicing_3d)
                    self.assertIsNone(controls.plane_artist)
                    self.assertFalse(controls.section_artists)
                controls.step_box.set_val("0.5")
                self.assertFalse(controls.show_slicing_3d)
                controls.set_orientation("Horizontal")
                controls.set_position(10)
                self.assertEqual(controls.current_section.geom_type,"Point")
                controls.set_3d_visible(False)
                self.assertEqual(controls.current_section.geom_type,"Point")
        finally:
            plt.close(viewer.fig)

    def test_switches_preserve_camera_settings_and_do_not_duplicate_callbacks(self):
        session = ViewerSession(make_model(),make_model)
        fig = session.viewer.fig
        try:
            with patch.object(fig.canvas,"draw_idle"):
                controls = session.viewer.plane_controls
                controls.set_orientation("Inclined")
                controls.azimuth_slider.set_val(30)
                controls.tilt_slider.set_val(40)
                controls.step_box.set_val("0.5")
                controls.set_position(0.5)
                controls.set_3d_visible(False)
                session.viewer.ax.view_init(elev=25,azim=32)
                camera = (session.viewer.ax.get_xlim(),session.viewer.ax.get_ylim(),
                          session.viewer.ax.get_zlim(),session.viewer.ax.elev,session.viewer.ax.azim)
                axes_count = len(fig.axes)
                for kind in ("polyline","polygon","point"):
                    self.assertTrue(session.switch_target(kind),session.last_error)
                    self.assertIs(session.viewer.fig,fig)
                    self.assertEqual(session.model.transition.target_kind,kind)
                    self.assertEqual(len(fig.axes),axes_count)
                    controls = session.viewer.plane_controls
                    self.assertEqual(controls.orientation,"Inclined")
                    self.assertEqual(controls.section_step,0.5)
                    self.assertEqual(controls.position_slider.val,0.5)
                    self.assertFalse(controls.show_slicing_3d)
                    self.assertEqual(controls.azimuth_slider.val,30)
                    self.assertEqual(controls.tilt_slider.val,40)
                    self.assertEqual(camera,(session.viewer.ax.get_xlim(),session.viewer.ax.get_ylim(),
                                            session.viewer.ax.get_zlim(),session.viewer.ax.elev,session.viewer.ax.azim))
                event = KeyEvent("key_press_event",fig.canvas,key="up")
                fig.canvas.callbacks.process("key_press_event",event)
                self.assertEqual(controls.position_slider.val,1.0)
                self.assertTrue(controls.show_slicing_3d)
        finally:
            plt.close(fig)

    def test_failed_switch_restores_dhe_globals_and_original_view(self):
        session = ViewerSession(make_model(),make_model)
        viewer = session.viewer
        old_state = (dhe.dhe,dhe.killdhe,dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        def fail(kind):
            make_base()
            raise ValueError("unsupported test target")
        try:
            with patch.object(viewer.fig.canvas,"draw_idle"):
                session.builder = fail
                self.assertFalse(session.switch_target("polyline"))
                self.assertIs(session.viewer,viewer)
                self.assertIs(dhe.dhe,old_state[0])
                self.assertIs(dhe.killdhe,old_state[1])
                self.assertEqual((dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),old_state[2:])
                self.assertIn("unsupported test target",session.error_text.get_text())
                app.validate_model(session.model)
                session.builder = make_model
                self.assertTrue(session.switch_target("polyline"))
        finally:
            plt.close(viewer.fig)


@unittest.skipUnless(Path(app.input_shp).is_file() and Path(LINE_SHP).is_file(),"User shapefiles unavailable")
class ActualCollapseTests(unittest.TestCase):
    def test_actual_point_and_polyline_topology_and_terminal_targets(self):
        for kind,count,shared,volume in (("point",8,28,1354.5603432698),
                                         ("polyline",16,36,1508.7586452378)):
            with self.subTest(kind=kind):
                with redirect_stdout(io.StringIO()):
                    model = app.main(["--scene","transition","--no-show","--target-kind",kind])
                layer = model.transition
                self.assertEqual(len(model.cells),5)
                self.assertEqual(len(layer.connecting_facets),count)
                self.assertEqual(len(layer.shared_interfaces),shared)
                self.assertAlmostEqual(cell_volume(layer.cell),volume,places=6)
                section = PlaneSlicer(model).sections(SlicePlane.horizontal(10),include_degenerate=True)
                self.assertTrue(section["3000"].equals(layer.target_geometry))
                if kind=="polyline":
                    self.assertEqual(len(layer.target_geometry.coords),4)
                    self.assertEqual(layer.target_definition.identifier,"3001")
                validate_transition_layer(model)


if __name__ == "__main__":
    unittest.main()
