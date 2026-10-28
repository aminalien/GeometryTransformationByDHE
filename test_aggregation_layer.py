"""Closed aggregate topology, wavefront sections and interactive regressions."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from shapely.geometry import Polygon, box

import DHECore as dhe
from DHECore import face_edges
import main as app
from aggregation_geometry import build_aggregation_preview
from aggregation_layer import build_aggregation_model, validate_aggregation_model, AGGREGATE_COLOR, EXTERIOR_COLOR
from plane_slicing import PlaneSlicer, SlicePlane, _face_frame
from plot_dhe_interactive import interactive_plot_from_lists
from top_face_overlay import cell_volume


def make_model(sources=None, **kwargs):
    return build_aggregation_model(build_aggregation_preview(sources or {
        "left": box(0, 0, 2, 2), "right": box(6, 0, 8, 2)}), **kwargs)


def make_viewer(model):
    return interactive_plot_from_lists(model.active_edges(), dhe.Getdhe()[1], show=False,
                                       highlight_cells=True, aggregation=model.aggregation)


class AggregationLayerTests(unittest.TestCase):
    def test_one_closed_cell_and_analytical_sections(self):
        model = make_model()
        layer = model.aggregation
        validate_aggregation_model(model)
        self.assertEqual(len(model.cells), 1)
        self.assertEqual(model.cells[0].source_ids, ("left", "right"))
        self.assertAlmostEqual(cell_volume(layer.cell), 104, places=8)
        self.assertEqual(sum(f.part == "upper_cap" for f in layer.facets), 1)
        self.assertEqual(sum(f.part == "bottom_cap" for f in layer.facets), 2)
        slicer = PlaneSlicer(model)
        previous = None
        for z in (0, 2.5, 5, 5.5, 6, 7, 8, 8.99, 9, 9.5, 10):
            with self.subTest(z=z):
                section = slicer.sections(SlicePlane.horizontal(z))["aggregate"]
                expected = layer.horizontal_section(z)
                self.assertLess(section.symmetric_difference(expected).area, 1e-8)
                if previous is not None:
                    self.assertLess(previous.difference(section).area, 1e-8)
                previous = section
        self.assertAlmostEqual(layer.horizontal_section(7).area, 12, places=8)
        self.assertTrue(layer.horizontal_section(9).equals(layer.preview.hull))

    def test_custom_heights_and_already_convex_union(self):
        model = make_model({"a": box(0, 0, 2, 2), "b": box(2, 0, 4, 2)},
                           bottom_z=-2, source_z=3, merge_z=7, top_z=8)
        layer = model.aggregation
        self.assertEqual(layer.connecting_facets, [])
        self.assertAlmostEqual(cell_volume(layer.cell), 80)
        self.assertTrue(layer.horizontal_section(5).equals(layer.preview.hull))
        with self.assertRaisesRegex(ValueError, "height"):
            layer.horizontal_section(9)

    def test_shared_source_edges_are_removed_and_all_faces_are_planar(self):
        model = make_model({"a": box(0, 0, 2, 2), "b": box(2, 0, 4, 2), "c": box(7, 1, 9, 3)})
        layer = model.aggregation
        for facet in layer.facets:
            _face_frame(facet.xyz)
            if facet.part == "base_wall":
                xs = [p[0] for p in facet.xyz]
                self.assertFalse(all(abs(x-2) < 1e-8 for x in xs))
            if facet.part == "connecting":
                heights = [p[2] for p in facet.xyz]
                self.assertGreater(max(heights)-min(heights), 1e-8)
                self.assertEqual(len(heights), 3)

    def test_concave_sources_and_bounded_gap_holes(self):
        sources = {
            "lower": Polygon([(0, 0), (6, 0), (6, 2), (4, 2), (4, 1), (1, 1), (1, 3), (0, 3)]),
            "upper": Polygon([(0, 3), (1, 3), (1, 4), (4, 4), (4, 2), (6, 2), (6, 5), (0, 5)]),
            "isolated": box(9, 2, 10, 3), "right": box(13, 0, 15, 5)}
        model = make_model(sources)
        slicer = PlaneSlicer(model)
        for z in (0, 5, 5.5, 6.5, 8, 9, 10):
            section = slicer.sections(SlicePlane.horizontal(z))["aggregate"]
            self.assertLess(section.symmetric_difference(model.aggregation.horizontal_section(z)).area, 1e-7)
        self.assertTrue(any(p.interiors for p in model.aggregation.preview.gap_regions))

    def test_oblique_and_vertical_sections_match_analytic_cap_band(self):
        model = make_model()
        slicer = PlaneSlicer(model)
        # Entire inclined section lies above growth completion in the solid band.
        plane = SlicePlane.from_angles((4, 1, 9.5), tilt=5, azimuth=90)
        section = slicer.sections(plane)["aggregate"]
        self.assertAlmostEqual(section.area, 16/np.cos(np.radians(5)), places=7)
        # At X=4 only the cap band, Z=9..10, joins the two sources.
        plane = SlicePlane.from_angles((4, 1, 5), tilt=90, azimuth=90)
        section = slicer.sections(plane)["aggregate"]
        self.assertAlmostEqual(section.area, 2, places=7)

    def test_preflight_and_allocation_failure_restore_previous_dhe(self):
        old = make_model()
        state = dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId
        preview = old.aggregation.preview
        for kwargs in ({"merge_z": 10}, {"bottom_z": 5}, {"top_z": float("nan")}):
            with self.assertRaisesRegex(ValueError, "finite"):
                build_aggregation_model(preview, **kwargs)
            self.assertEqual((dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId), state)
        with patch("aggregation_layer.validate_aggregation_model", side_effect=RuntimeError("validation failure")):
            with self.assertRaisesRegex(RuntimeError, "validation failure"):
                build_aggregation_model(preview)
        self.assertIs(dhe.dhe, state[0])
        self.assertIs(dhe.killdhe, state[1])
        self.assertEqual((dhe.Vertex.cntId, dhe.DualHalfEdge.cntId), state[2:])
        validate_aggregation_model(old)

    def test_source_face_colors_selection_and_exterior_navigation(self):
        model = make_model()
        layer = model.aggregation
        viewer = make_viewer(model)
        try:
            with patch.object(viewer.fig.canvas, "draw_idle"):
                for source in layer.preview.sources:
                    facet = next(f for f in layer.connecting_facets if f.source_id == source)
                    viewer._set_current_edge(facet.seed)
                    viewer.on_key(SimpleNamespace(key="x"))
                    self.assertIsNotNone(viewer.selected_face_artist)
                    self.assertEqual(viewer.selected_face_artist.get_alpha(), 0.5)
                    np.testing.assert_allclose(viewer.selected_face_artist.get_facecolor()[0][:3],
                                               colors.to_rgba(layer.source_colors[source])[:3])
                    self.assertFalse(viewer.cell_artists)
                    viewer.on_key(SimpleNamespace(key="l"))
                    self.assertIsNone(viewer.selected_face_artist)
                    viewer.on_key(SimpleNamespace(key="f"))
                    self.assertIsNotNone(viewer.selected_face_artist)
                    viewer.on_key(SimpleNamespace(key="a"))
                    self.assertIs(viewer.e.D.V, model.exterior)
                    np.testing.assert_allclose(viewer.selected_face_artist.get_facecolor()[0][:3], EXTERIOR_COLOR[:3])
                    viewer.on_key(SimpleNamespace(key="a"))
                    self.assertIs(viewer.e.D.V, layer.cell.dual_vertex)
                top = next(f for f in layer.facets if f.part == "upper_cap")
                viewer._set_current_edge(top.seed)
                np.testing.assert_allclose(viewer.selected_face_artist.get_facecolor()[0][:3], AGGREGATE_COLOR[:3])
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)

    def test_projection_picking_and_all_slicing_controls(self):
        model = make_model()
        viewer = make_viewer(model)
        try:
            panel, controls = viewer.overlay_panel, viewer.plane_controls
            self.assertFalse(controls.show_slicing_3d)
            self.assertIsNone(controls.plane_artist)
            self.assertEqual(set(controls.current_sections), {"aggregate"})
            with patch.object(viewer.fig.canvas, "draw_idle"):
                facet = model.aggregation.connecting_facets[0]
                p = facet.geometry.representative_point()
                panel.on_click(SimpleNamespace(inaxes=panel.ax, button=1, xdata=p.x, ydata=p.y))
                self.assertEqual(panel.selected_fragment.part, "connecting")
                self.assertTrue(panel.highlight_artists)
                for orientation in ("Horizontal", "Vertical", "Inclined"):
                    controls.orientation_dropdown.select(orientation)
                    if orientation != "Horizontal":
                        controls.azimuth_slider.set_val(90)
                    if orientation == "Inclined":
                        controls.tilt_slider.set_val(30)
                    for step in (0.01, 0.1, 0.5, 1):
                        controls.step_box.set_val(str(step))
                        start = 6 if orientation == "Horizontal" else 0
                        controls.set_position(start)
                        controls.raise_button._observers.process("clicked", None)
                        self.assertAlmostEqual(controls.position_slider.val, start+step)
                        viewer.on_key(SimpleNamespace(key="down"))
                        self.assertAlmostEqual(controls.position_slider.val, start)
                        controls.on_scroll(SimpleNamespace(inaxes=controls.position_slider.ax, step=1))
                        self.assertAlmostEqual(controls.position_slider.val, start+step)
                        controls.lower_button._observers.process("clicked", None)
                        self.assertAlmostEqual(controls.position_slider.val, start)
                        self.assertTrue(all(g.is_valid for g in controls.current_sections.values()))
                self.assertTrue(controls.show_slicing_3d)
                controls.visibility_checkbox.set_active(0)
                self.assertIsNone(controls.plane_artist)
                self.assertTrue(controls.current_sections)
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class ActualAggregationLayerTests(unittest.TestCase):
    def test_actual_single_cell_caps_volume_and_nested_sections(self):
        with redirect_stdout(io.StringIO()):
            model = app.main(["--no-show"])
        layer = model.aggregation
        self.assertEqual(app.validate_model(model)[0]["id"], "aggregate")
        self.assertEqual(len(layer.connecting_facets), 68)
        self.assertEqual(len(layer.facets), 121)
        self.assertEqual(sum(f.part == "bottom_cap" for f in layer.facets), 6)
        self.assertAlmostEqual(cell_volume(layer.cell), 8520.712540787, places=5)
        self.assertEqual((layer.bottom_z, layer.source_z, layer.merge_z, layer.target_z), (0, 5, 9, 10))
        for source in layer.preview.sources.values():
            vertices = {(e.V.x, e.V.y, e.V.z) for e in model.active_edges()}
            for x, y in list(source.exterior.coords)[:-1]:
                self.assertIn((x, y, 0.0), vertices)
        slicer = PlaneSlicer(model)
        for z in (0, 2.5, 5, 5.01, 5.5, 6, 7, 8, 8.9, 9, 9.5, 10):
            section = slicer.sections(SlicePlane.horizontal(z))["aggregate"]
            self.assertLess(section.symmetric_difference(layer.horizontal_section(z)).area, 1e-7)
        for facet in layer.facets:
            _face_frame(facet.xyz)
            for edge in face_edges(facet.seed):
                self.assertIs(edge.Adjacent().D.V, model.exterior)

    def test_cli_export_and_custom_growth_heights(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            png = Path(directory) / "aggregate.png"
            model = app.main(["--merge-z", "10", "--target-z", "11", "--no-show", "--preview", str(png)])
            self.assertGreater(png.stat().st_size, 1000)
            self.assertEqual(model.aggregation.merge_z, 10)
            self.assertEqual(model.aggregation.target_z, 11)


if __name__ == "__main__":
    unittest.main()
