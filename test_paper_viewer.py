"""Scientific stage accuracy, UI transactions, mouse input, and figure export."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent
import numpy as np
from shapely import union_all
from shapely.geometry import Polygon

import DHECore as dhe
import main as app
from paper_stages import STAGES, TRANSFORMATIONS, build_paper_stage
from paper_session import PaperSession, camera_state
from paper_export import export_paper_figure


def arguments():
    return SimpleNamespace(input_shp=app.input_shp, height=5., z_down=0., target_z=10., merge_z=9.,
                           source_id=3000, target_id=3006, line_shp=app.LineSHP, line_id=3001)


def core():
    return dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId


def click_axes(ax):
    canvas = ax.figure.canvas
    xy = ax.transAxes.transform((0.5, 0.5))
    for name in ("button_press_event", "button_release_event"):
        canvas.callbacks.process(name, MouseEvent(name, canvas, *xy, button=1))


def choose(dropdown, value):
    click_axes(dropdown.button.ax)
    click_axes(dropdown.menu[dropdown.options.index(value)].ax)


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class PaperStageTests(unittest.TestCase):
    def test_all_24_stages_have_correct_identity_geometry_and_live_state(self):
        self.assertEqual(sum(map(len, STAGES.values())), 24)
        for family, specs in STAGES.items():
            bounds = None
            for spec in specs:
                with self.subTest(family=family, step=spec.id):
                    stage = build_paper_stage(arguments(), family, spec.id)
                    self.assertEqual(stage.spec, spec)
                    self.assertEqual(stage.model is not None, spec.dhe)
                    self.assertEqual(list(stage.sources), [str(i) for i in
                                     (app.AGGREGATION_IDS if family == "aggregation" else app.POLYGON_IDS)])
                    if bounds is not None:
                        self.assertEqual(stage.bounds, bounds)
                    bounds = stage.bounds
                    if stage.model:
                        app.validate_model(stage.model)
                        self.assertFalse(set(stage.model.active_edges()) & dhe.Getdhe()[1])
                        self.assertEqual(len(stage.model.cells), 1 if family == "aggregation" and stage.final else 6 if family == "aggregation" else 5)
                    if spec.id == "triangulation" and family != "polygon":
                        self.assertTrue(all(p[2] == 5 for f in stage.facets for p in f.points))
                    if spec.id == "inclined":
                        self.assertTrue(stage.facets)
                        self.assertTrue(any(max(p[2] for p in f.points) > min(p[2] for p in f.points) for f in stage.facets))
                    if spec.id == "partitions":
                        combined = union_all([g for g, color, label in stage.regions])
                        self.assertLess(combined.symmetric_difference(stage.target).area, 1e-8)
                    if spec.id == "input" and family == "aggregation":
                        self.assertFalse(stage.lines)  # Hull first appears in the next scientific stage.
                    if spec.id == "extensions":
                        self.assertEqual(len(stage.regions), 2)
                        combined = union_all([g for g, color, label in stage.regions])
                        self.assertLess(combined.symmetric_difference(stage.sources["3000"]).area, 1e-8)

    def test_invalid_stage_and_heights_are_rejected(self):
        args = arguments()
        with self.assertRaisesRegex(ValueError, "Unknown step"):
            build_paper_stage(args, "point", "overlay")
        args.target_z = 5
        with self.assertRaisesRegex(ValueError, "finite"):
            build_paper_stage(args, "point", "input")


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class PaperSessionTests(unittest.TestCase):
    def setUp(self):
        with redirect_stdout(io.StringIO()):
            self.session = PaperSession(arguments())
        self.session.figure.canvas.callbacks.exception_handler = lambda error: (_ for _ in ()).throw(error)

    def tearDown(self):
        plt.close(self.session.figure)

    def test_all_stages_render_in_one_window_without_callback_growth(self):
        session = self.session
        fig = session.figure
        counts = {}
        with patch.object(fig.canvas, "draw_idle"), redirect_stdout(io.StringIO()):
            for cycle in range(2):
                for family, specs in STAGES.items():
                    self.assertTrue(session.switch_transformation(family), session.last_error)
                    for spec in specs:
                        self.assertTrue(session.switch_stage(spec.id), session.last_error)
                        self.assertIs(session.figure, fig)
                        count = sum(len(c) for c in fig.canvas.callbacks.callbacks.values())
                        key = family, spec.id
                        if cycle:
                            self.assertEqual(count, counts[key], key)
                        else:
                            counts[key] = count
                            fig.canvas.draw()
                        self.assertEqual(session.export_section_button.active, spec.id == "final")
                        self.assertEqual(bool(session.viewer.plane_controls), spec.id == "final")
                        self.assertIsNone(fig.canvas.mouse_grabber)

    def test_remembered_stage_camera_and_final_slice_settings(self):
        session = self.session
        with patch.object(session.figure.canvas, "draw_idle"), redirect_stdout(io.StringIO()):
            session.viewer.ax.view_init(elev=21, azim=42, roll=3)
            camera = camera_state(session.viewer.ax)
            controls = session.viewer.plane_controls
            controls.step_box.set_val("0.5")
            controls.set_position(8)
            self.assertTrue(session.switch_stage("skeleton"))
            self.assertIsNone(session.viewer.plane_controls)
            np.testing.assert_allclose(session.viewer.ax.get_xlim(), camera["xlim"])
            self.assertEqual(session.viewer.ax.elev, 21)
            self.assertTrue(session.switch_transformation("point"))
            self.assertEqual(session.viewer.ax.azim, 42)
            self.assertTrue(session.switch_transformation("aggregation"))
            self.assertEqual(session.stage.spec.id, "skeleton")
            self.assertTrue(session.switch_stage("final"))
            self.assertEqual(session.viewer.plane_controls.section_step, 0.5)
            self.assertEqual(session.viewer.plane_controls.position_slider.val, 8)
            self.assertTrue(session.viewer.plane_controls.show_slicing_3d)

    def test_builder_and_partial_renderer_failures_restore_old_state(self):
        session = self.session
        old_stage, old_core = session.stage, core()
        with patch.object(session.figure.canvas, "draw_idle"), redirect_stdout(io.StringIO()):
            def fail(family, step):
                dhe.reset_globals()
                raise ValueError("bad target")
            with patch.object(session, "builder", side_effect=fail):
                self.assertFalse(session.switch_transformation("point"))
            self.assertIs(session.stage, old_stage)
            self.assertIs(dhe.dhe, old_core[0])
            self.assertEqual(core()[2:], old_core[2:])
            from paper_plot import mount_paper_view
            calls = []
            def render_then_fail(stage, figure):
                viewer = mount_paper_view(stage, figure)
                calls.append(stage.spec.id)
                if len(calls) == 1:
                    raise RuntimeError("render failed")
                return viewer
            with patch("paper_session.mount_paper_view", side_effect=render_then_fail):
                self.assertFalse(session.switch_stage("inclined"))
            self.assertIs(session.stage, old_stage)
            self.assertIs(dhe.dhe, old_core[0])
            self.assertEqual(session.step_dropdown.value, "7. Final single-cell DHE")
            self.assertEqual(session.transformation_dropdown.value, "Aggregation")
            self.assertFalse(session.busy)
            self.assertIsNone(session.figure.canvas.mouse_grabber)
            self.assertTrue(session.switch_stage("input"))

    def test_real_mouse_dropdowns_navigation_and_slicing_after_switches(self):
        session = self.session
        with patch.object(session.figure.canvas, "draw_idle"), redirect_stdout(io.StringIO()):
            for family, label in TRANSFORMATIONS.items():
                choose(session.transformation_dropdown, label)
                self.assertEqual(session.stage.transformation, family)
                self.assertEqual(len(session.step_dropdown.options), len(STAGES[family]))
                # Switch away from and back to the final model through real clicks.
                choose(session.step_dropdown, session.step_dropdown.options[1])
                self.assertEqual(session.stage.spec.id, STAGES[family][1].id)
                choose(session.step_dropdown, session.step_dropdown.options[-1])
                viewer = session.viewer
                self.assertTrue(viewer.plane_controls)
                viewer.on_key(SimpleNamespace(key="x"))
                self.assertIsNotNone(viewer.selected_face_artist)
                self.assertAlmostEqual(viewer.selected_face_artist.get_alpha(), 0.5)
                viewer.on_key(SimpleNamespace(key="l"))
                self.assertIsNone(viewer.selected_face_artist)
                viewer.on_key(SimpleNamespace(key="f"))
                self.assertIsNotNone(viewer.selected_face_artist)
                viewer.plane_controls.step_box.set_val("0.1")
                viewer.on_key(SimpleNamespace(key="up"))
                self.assertTrue(viewer.plane_controls.show_slicing_3d)
                self.assertIsNone(session.figure.canvas.mouse_grabber)
            session.transformation_dropdown.toggle()
            click_axes(session.step_dropdown.button.ax)
            self.assertFalse(session.transformation_dropdown.is_open)
            self.assertTrue(session.step_dropdown.is_open)

    def test_clean_exports_all_families_preserve_state_and_have_metadata(self):
        session = self.session
        with tempfile.TemporaryDirectory() as directory, patch.object(session.figure.canvas, "draw_idle"), redirect_stdout(io.StringIO()):
            for family in STAGES:
                self.assertTrue(session.switch_transformation(family))
                self.assertTrue(session.switch_stage("final"))
                viewer = session.viewer
                viewer.ax.view_init(elev=18, azim=36)
                viewer.on_key(SimpleNamespace(key="l"))
                before, camera = core(), camera_state(viewer.ax)
                paths = export_paper_figure(session, directory=directory)
                self.assertEqual(paths[0].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                svg = paths[1].read_text(encoding="utf-8")
                self.assertIn("<svg", svg)
                self.assertNotIn("<image", svg)
                for widget_text in ("Implementation step", "Export figure", "Edge ID", "Lower", "Raise"):
                    self.assertNotIn(widget_text, svg)
                metadata = json.loads(paths[2].read_text(encoding="utf-8"))
                self.assertEqual(metadata["transformation"], family)
                self.assertEqual(metadata["stage_id"], "final")
                self.assertFalse(metadata["current_face_fill"])
                self.assertEqual(metadata["camera"], camera)
                self.assertIs(dhe.dhe, before[0])
                self.assertEqual(core()[2:], before[2:])
                self.assertFalse(viewer.current_face_fill_enabled)
                section_paths = export_paper_figure(session, directory=directory, section=True)
                self.assertTrue(json.loads(section_paths[2].read_text())["section_only"])
            self.assertTrue(session.switch_stage("inclined"))
            paths = export_paper_figure(session, directory=directory)
            self.assertEqual(json.loads(paths[2].read_text())["kind"], "Geometry preview")
            with self.assertRaisesRegex(ValueError, "final"):
                export_paper_figure(session, directory=directory, section=True)
            self.assertTrue(session.switch_stage("skeleton"))
            panel = session.viewer.overlay_panel
            region, color, label = session.stage.regions[0]
            point = region.representative_point()
            panel.on_click(SimpleNamespace(inaxes=panel.ax, button=1, xdata=point.x, ydata=point.y))
            self.assertIsNotNone(panel.region_selection)
            paths = export_paper_figure(session, directory=directory)
            self.assertEqual(json.loads(paths[2].read_text())["region_selection"]["label"], label)
            self.assertEqual(plt.get_fignums(), [session.figure.number])

    def test_cli_headless_stage_and_legacy_builders(self):
        with redirect_stdout(io.StringIO()):
            stage = app.main(["--no-show", "--transformation", "polyline", "--step", "extensions"])
            self.assertEqual(stage.kind, "Geometry preview")
            self.assertEqual(len(stage.regions), 2)
            model = app.main(["--no-show", "--scene", "aggregation"])
            self.assertEqual(len(model.cells), 1)


if __name__ == "__main__":
    unittest.main()
