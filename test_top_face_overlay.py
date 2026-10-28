"""Regression checks for DHE roof subdivision and the linked overlay viewer."""

import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.affinity import translate
from shapely.geometry import Polygon

import DHECore as dhe
import main as app
from plot_dhe_interactive import interactive_plot_from_lists
from top_face_overlay import (
    cell_volume, face_polygon, overlay_top_faces, roof_faces, validate_overlay,
)


def snapshot(model):
    return tuple(
        (edge, edge.S, edge.NF, edge.NV, edge.D, edge.V)
        for edge in model.active_edges()
    )


class RoofOverlayTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()
        exterior = dhe.make_vertex(6, 6, 5, ch="VINF")
        self.polygons = [
            Polygon([(0, 0), (2, 0), (2, 4), (0, 4)]),
            Polygon([(2, 0), (4, 0), (4, 4), (2, 4)]),
        ]
        cells = [
            app.extrude_polygon(polygon, str(i + 1), 10, exterior, app.CELL_COLORS[i])
            for i, polygon in enumerate(self.polygons)
        ]
        self.model = app.ExtrusionModel(cells, exterior, (0, 0), app.sew_shared_walls(cells))
        self.target = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])

    def apply_overlay(self):
        return overlay_top_faces(self.model, self.target, source_id=1, target_id=9, z=10)

    def test_shared_edge_is_split_twice_and_cells_remain_closed(self):
        base_before = [
            [(v.x, v.y, v.z) for v in (edge.V for edge in app.face_edges(cell.seed))]
            for cell in self.model.cells
        ]
        duals = [cell.dual_vertex for cell in self.model.cells]
        result = self.apply_overlay()
        self.assertEqual(len(result.crossing_vertices), 2)
        self.assertEqual(Counter(f.cell_id for f in result.fragments), {"1": 2, "2": 2})
        self.assertEqual(
            [(s["vertices"], s["edges"], s["faces"]) for s in app.validate_model(self.model)],
            [(12, 17, 7), (12, 17, 7)],
        )
        for index, cell in enumerate(self.model.cells):
            self.assertIs(cell.dual_vertex, duals[index])
            self.assertAlmostEqual(cell_volume(cell), 80)
            base_after = [(e.V.x, e.V.y, e.V.z) for e in app.face_edges(cell.seed)]
            self.assertEqual(base_after, base_before[index])
            roof_vertices = {e.V for f in roof_faces(cell, 10) for e in app.face_edges(f)}
            self.assertTrue(set(result.crossing_vertices).issubset(roof_vertices))
        # Both copies of the incident shared wall use the same inserted vertices.
        for face in self.model.shared_walls[0]:
            wall_vertices = {edge.V for edge in app.face_edges(face)}
            self.assertTrue(set(result.crossing_vertices).issubset(wall_vertices))
        validate_overlay(self.model)

    def test_fragment_classification_area_and_target_coverage(self):
        result = self.apply_overlay()
        self.assertEqual(sum(f.inside_target for f in result.fragments), 2)
        self.assertAlmostEqual(sum(f.geometry.area for f in result.fragments if f.inside_target), 4)
        for cell_id in ("1", "2"):
            self.assertAlmostEqual(sum(f.geometry.area for f in result.fragments if f.cell_id == cell_id), 8)
        for fragment in result.fragments:
            self.assertLess(fragment.geometry.symmetric_difference(face_polygon(fragment.seed)).area, 1e-9)
        self.assertEqual({str(v.i) for v in [c.dual_vertex for c in self.model.cells]}, {"1", "2"})

    def test_unsupported_arrangements_fail_before_mutation(self):
        cases = [
            (Polygon([(0.4, 0.4), (1.4, 0.4), (1.4, 1.4), (0.4, 1.4)]), "interior target loop"),
            (Polygon([(1, 0), (3, 0), (3, 2), (1, 2)]), "Coincident"),
            (Polygon([(1, 1), (5, 1), (5, 3), (1, 3)]), "outside"),
        ]
        before = snapshot(self.model)
        counts = (dhe.Vertex.cntId, dhe.DualHalfEdge.cntId)
        for target, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                overlay_top_faces(self.model, target, source_id=1, target_id=9, z=10)
            self.assertEqual(snapshot(self.model), before)
            self.assertEqual((dhe.Vertex.cntId, dhe.DualHalfEdge.cntId), counts)
            self.assertIsNone(self.model.overlay)
        with self.assertRaisesRegex(ValueError, "Source cell"):
            overlay_top_faces(self.model, self.target, source_id=99, target_id=9, z=10)
        with self.assertRaisesRegex(ValueError, "separately"):
            overlay_top_faces(self.model, self.target, source_id=1, target_id=2, z=10)
        self.assertEqual(snapshot(self.model), before)

    def test_repeat_application_is_rejected(self):
        self.apply_overlay()
        before = snapshot(self.model)
        with self.assertRaisesRegex(ValueError, "already"):
            self.apply_overlay()
        self.assertEqual(snapshot(self.model), before)

    def test_crossing_at_existing_roof_vertex_reuses_that_vertex(self):
        corner = next(e.V for e in self.model.active_edges()
                      if (e.V.x, e.V.y, e.V.z) == (2, 4, 10))
        triangle = Polygon([(1, 1), (3, 1), (2, 4)])
        result = overlay_top_faces(self.model, triangle, source_id=1, target_id=9, z=10)
        self.assertIn(corner, result.crossing_vertices)
        self.assertEqual(
            [(s["vertices"], s["edges"], s["faces"]) for s in app.validate_model(self.model)],
            [(10, 15, 7), (10, 15, 7)],
        )
        validate_overlay(self.model)

    def test_plan_selection_and_3d_navigation_are_linked(self):
        result = self.apply_overlay()
        viewer = interactive_plot_from_lists(
            self.model.active_edges(), dhe.Getdhe()[1], show=False,
            highlight_cells=True, overlay=result,
        )
        try:
            panel = viewer.overlay_panel
            fragment = next(f for f in result.fragments if f.cell_id == "2" and f.inside_target)
            center = fragment.geometry.representative_point()
            panel.on_click(SimpleNamespace(inaxes=panel.ax, button=1, xdata=center.x, ydata=center.y))
            self.assertIs(viewer.e, fragment.seed)
            self.assertIs(panel.selected_fragment, fragment)
            self.assertEqual(len(panel.selection_3d_lines), 0)
            internal_edge = next(e for e in app.face_edges(fragment.seed) if e.S in panel.edge_to_fragment)
            viewer._set_current_edge(internal_edge)
            viewer._draw_highlight_line()
            self.assertIs(panel.selected_fragment, fragment)
            opposite = panel.edge_to_fragment[internal_edge.S]
            viewer.on_key(SimpleNamespace(key="s"))
            self.assertIs(viewer.e, internal_edge.S)
            self.assertIs(panel.selected_fragment, opposite)
            self.assertIn("outside target", panel.status.get_text())
            viewer.on_key(SimpleNamespace(key="f"))
            self.assertIs(panel.selected_fragment, opposite)
            viewer.ax.view_init(elev=35, azim=50)
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class ActualRoofOverlayTests(unittest.TestCase):
    def test_five_cells_overlay_matches_planned_topology_and_areas(self):
        model = app.build_model(height=10)
        before = app.validate_model(model)
        target = app.load_selected_polygons(app.input_shp, [3006]).geometry.iloc[0]
        target = translate(target, xoff=-model.origin[0], yoff=-model.origin[1])
        result = overlay_top_faces(model, target, source_id=3000, target_id=3006, z=10)
        validate_overlay(model)
        after = app.validate_model(model)
        self.assertEqual([s["neighbors"] for s in after], [s["neighbors"] for s in before])
        self.assertEqual(len(model.shared_walls), 12)
        self.assertEqual(len(result.crossing_vertices), 4)
        self.assertEqual(Counter(f.cell_id for f in result.fragments),
                         {"3000": 3, "3001": 2, "3002": 1, "3003": 1, "3004": 2})
        self.assertEqual(
            [(s["id"], s["vertices"], s["edges"], s["faces"]) for s in after],
            [("3000", 25, 35, 12), ("3001", 24, 35, 13), ("3002", 14, 21, 9),
             ("3003", 18, 27, 11), ("3004", 19, 28, 11)],
        )
        expected = {"3000": 117.5457868, "3001": 43.9192587, "3004": 16.0732110}
        for cell in model.cells:
            fragments = [f for f in result.fragments if f.cell_id == cell.feature_id]
            area = sum(f.geometry.area for f in fragments if f.inside_target)
            self.assertAlmostEqual(area, expected.get(cell.feature_id, 0), places=5)
            self.assertAlmostEqual(cell_volume(cell), result.original_roofs[cell.feature_id].area * 10, places=6)

    def test_cli_defaults_and_legacy_extrusion_mode(self):
        model = app.main(["--scene", "transition", "--target-kind", "polygon", "--no-show"])
        self.assertEqual(model.overlay.source_id, "3000")
        self.assertEqual(model.overlay.target_id, "3006")
        legacy = app.main(["--scene", "transition", "--target-kind", "polygon", "--ids", "2001", "2002", "--no-overlay", "--no-show"])
        self.assertIsNone(legacy.overlay)
        self.assertEqual(len(legacy.shared_walls), 3)


if __name__ == "__main__":
    unittest.main()
