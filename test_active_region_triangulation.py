"""Check constrained active-region triangulation in the DHE model."""

import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import shapely
from shapely.affinity import translate
from shapely.geometry import GeometryCollection, Polygon
from shapely.ops import unary_union

import DHECore as dhe
import main as app
from active_region_triangulation import (
    VertexRole, classify_region, triangulate_active_regions, validate_triangulation,
)
from plot_dhe_interactive import REGION_COLORS, interactive_plot_from_lists
from top_face_overlay import RegionKind, overlay_top_faces, validate_overlay


def snapshot(model):
    return tuple((e, e.S, e.NF, e.NV, e.D, e.V) for e in model.active_edges())


class ActiveTriangulationTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()
        exterior = dhe.make_vertex(6, 6, 5, ch="VINF")
        polygons = [
            Polygon([(0, 0), (2, 0), (2, 4), (0, 4)]),
            Polygon([(2, 0), (4, 0), (4, 4), (2, 4)]),
        ]
        cells = [app.extrude_polygon(p, str(i + 1), 10, exterior, app.CELL_COLORS[i])
                 for i, p in enumerate(polygons)]
        self.model = app.ExtrusionModel(cells, exterior, (0, 0), app.sew_shared_walls(cells))
        self.target = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
        self.overlay = overlay_top_faces(self.model, self.target, source_id=1, target_id=9, z=10)

    def test_classification_matches_set_operations(self):
        result = triangulate_active_regions(self.model)
        self.assertEqual(Counter(r.kind for r in result.regions), {
            RegionKind.ACTIVE_SOURCE_ONLY: 1, RegionKind.ACTIVE_TARGET_ONLY: 1,
            RegionKind.COMMON_NONACTIVE: 1, RegionKind.BACKGROUND: 1,
        })
        source, target = self.overlay.source_geometry, self.overlay.target_geometry
        expected = {
            RegionKind.ACTIVE_SOURCE_ONLY: source.difference(target),
            RegionKind.ACTIVE_TARGET_ONLY: target.difference(source),
            RegionKind.COMMON_NONACTIVE: source.intersection(target),
        }
        for kind, geometry in expected.items():
            classified = unary_union([r.geometry for r in result.regions if r.kind == kind])
            self.assertLess(classified.symmetric_difference(geometry).area, 1e-9)
        active = unary_union([f.geometry for f in result.triangles])
        self.assertLess(active.symmetric_difference(source.symmetric_difference(target)).area, 1e-9)
        self.assertEqual(len(result.triangles), 8)
        self.assertEqual(len(result.inserted_diagonals), 6)

    def test_boundary_tags_and_vertex_positions_are_preserved(self):
        before = {e.V: (e.V.x, e.V.y, e.V.z) for e in self.model.active_edges()}
        vertex_counter = dhe.Vertex.cntId
        result = triangulate_active_regions(self.model)
        self.assertEqual(result.original_vertex_positions, before)
        self.assertEqual({e.V: (e.V.x, e.V.y, e.V.z) for e in self.model.active_edges()}, before)
        self.assertEqual(dhe.Vertex.cntId, vertex_counter)
        self.assertEqual(set(result.vertex_roles.values()), set(VertexRole))
        both = {v for v, role in result.vertex_roles.items() if role is VertexRole.BOTH}
        self.assertEqual(both, set(self.overlay.crossing_vertices))
        for region in result.regions:
            if region.kind.active:
                self.assertNotIn(VertexRole.NEITHER, [result.vertex_roles[v] for v in region.boundary_vertices])
        validate_triangulation(self.model)

    def test_common_and_background_faces_stay_unsplit(self):
        untouched = {
            f.label: (f, tuple(app.face_edges(f.seed)))
            for f in self.overlay.fragments
            if not classify_region(f.geometry, self.overlay.source_geometry, self.target).active
        }
        result = triangulate_active_regions(self.model)
        for region in result.regions:
            if not region.kind.active:
                old_fragment, old_cycle = untouched[region.region_id]
                self.assertEqual(len(region.fragments), 1)
                self.assertIs(region.fragments[0], old_fragment)
                self.assertEqual(tuple(app.face_edges(old_fragment.seed)), old_cycle)
        validate_overlay(self.model)
        app.validate_model(self.model)

    def test_bad_triangulation_or_missing_support_fails_before_mutation(self):
        before = snapshot(self.model)
        original_fragments = list(self.overlay.fragments)
        for replacement, message in (
            (None, "Shapely"),
            (lambda geometry: GeometryCollection(), "preserve the region vertices"),
        ):
            with self.subTest(message=message):
                with patch.object(shapely, "constrained_delaunay_triangles", replacement):
                    with self.assertRaisesRegex(ValueError, message):
                        triangulate_active_regions(self.model)
                self.assertEqual(snapshot(self.model), before)
                self.assertIsNone(self.overlay.triangulation)
                self.assertEqual(self.overlay.fragments, original_fragments)
                self.assertTrue(all(f.region_kind is None and f.parent_region_id is None
                                    for f in self.overlay.fragments))

    def test_overlay_is_required_and_repeat_calls_are_rejected(self):
        self.model.overlay = None
        with self.assertRaisesRegex(ValueError, "overlay"):
            triangulate_active_regions(self.model)
        self.model.overlay = self.overlay
        triangulate_active_regions(self.model)
        before = snapshot(self.model)
        with self.assertRaisesRegex(ValueError, "already"):
            triangulate_active_regions(self.model)
        self.assertEqual(snapshot(self.model), before)

    def test_triangle_selection_is_linked_and_reports_parent(self):
        mesh = triangulate_active_regions(self.model)
        viewer = interactive_plot_from_lists(
            self.model.active_edges(), dhe.Getdhe()[1], show=False,
            highlight_cells=True, overlay=self.overlay,
        )
        try:
            panel = viewer.overlay_panel
            self.assertTrue(panel.classified)
            self.assertEqual(len(panel.patches), len(self.overlay.fragments))
            triangle = next(f for f in mesh.triangles if f.region_kind is RegionKind.ACTIVE_TARGET_ONLY)
            point = triangle.geometry.representative_point()
            panel.on_click(SimpleNamespace(inaxes=panel.ax, button=1, xdata=point.x, ydata=point.y))
            self.assertIs(viewer.e, triangle.seed)
            self.assertIs(panel.selected_fragment, triangle)
            self.assertIn(triangle.parent_region_id, panel.status.get_text())
            self.assertIn("active T - S", panel.status.get_text())
            internal = next(e for e in app.face_edges(triangle.seed)
                            if e.S in panel.edge_to_fragment
                            and panel.edge_to_fragment[e.S].parent_region_id == triangle.parent_region_id)
            viewer._set_current_edge(internal)
            viewer._draw_highlight_line()
            opposite = panel.edge_to_fragment[internal.S]
            viewer.on_key(SimpleNamespace(key="s"))
            self.assertIs(panel.selected_fragment, opposite)
            self.assertEqual(opposite.parent_region_id, triangle.parent_region_id)
            self.assertEqual(tuple(panel.patches[opposite.label].get_facecolor()[:3]),
                             opposite.seed.D.V.color[:3])
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class ActualActiveTriangulationTests(unittest.TestCase):
    def test_actual_mesh_counts_and_cell_invariants(self):
        model = app.build_model(height=10)
        target = app.load_selected_polygons(app.input_shp, [3006]).geometry.iloc[0]
        target = translate(target, xoff=-model.origin[0], yoff=-model.origin[1])
        overlay = overlay_top_faces(model, target, source_id=3000, target_id=3006, z=10)
        before = app.validate_model(model)
        triangular_original = next(f for f in overlay.fragments if f.cell_id == "3004" and f.inside_target)
        original_triangle_cycle = tuple(app.face_edges(triangular_original.seed))
        result = triangulate_active_regions(model)
        self.assertEqual(len(result.triangles), 16)
        self.assertEqual(len(result.inserted_diagonals), 12)
        self.assertEqual(len(overlay.fragments), 21)
        self.assertEqual(Counter(r.kind for r in result.regions), {
            RegionKind.ACTIVE_SOURCE_ONLY: 2, RegionKind.ACTIVE_TARGET_ONLY: 2,
            RegionKind.COMMON_NONACTIVE: 1, RegionKind.BACKGROUND: 4,
        })
        self.assertEqual([len(r.fragments) for r in result.regions if r.kind.active], [2, 8, 5, 1])
        self.assertEqual(tuple(app.face_edges(triangular_original.seed)), original_triangle_cycle)
        after = app.validate_model(model)
        self.assertEqual([s["neighbors"] for s in before], [s["neighbors"] for s in after])
        self.assertEqual(
            [(s["id"], s["vertices"], s["edges"], s["faces"]) for s in after],
            [("3000", 25, 43, 20), ("3001", 24, 39, 17), ("3002", 14, 21, 9),
             ("3003", 18, 27, 11), ("3004", 19, 28, 11)],
        )
        validate_triangulation(model)
        validate_overlay(model)

    def test_cli_supports_triangulation_overlay_and_legacy_modes(self):
        model = app.main(["--target-kind", "polygon", "--no-show"])
        self.assertEqual(len(model.overlay.triangulation.triangles), 16)
        step1 = app.main(["--target-kind", "polygon", "--no-show", "--no-triangulation"])
        self.assertIsNone(step1.overlay.triangulation)
        self.assertEqual(len(step1.overlay.fragments), 9)
        legacy = app.main(["--target-kind", "polygon", "--no-show", "--ids", "2001", "2002", "--no-overlay"])
        self.assertIsNone(legacy.overlay)


if __name__ == "__main__":
    unittest.main()
