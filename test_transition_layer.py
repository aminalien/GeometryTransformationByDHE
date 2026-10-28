"""Shared DHE partitions, whole-model slices, and navigation/scroll regressions."""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent
from shapely.geometry import Polygon
from shapely.ops import unary_union

import DHECore as dhe
import main as app
from active_region_triangulation import VertexRole, triangulate_active_regions, validate_triangulation
from plot_dhe_interactive import HILIGHT_COLOR, interactive_plot_from_lists
from top_face_overlay import cell_volume, overlay_top_faces, validate_overlay
from transition_layer import build_transition_layer, slice_model, validate_transition_layer
from plane_slicing import PlaneSlicer, SlicePlane


def base_snapshot(model):
    return tuple((e, e.S, e.NF, e.NV, e.D, e.V)
                 for e in model.active_edges(include_transition=False))


def neighbour_pairs(interfaces):
    return {frozenset((a.D.V.logical_feature_id, b.D.V.logical_feature_id))
            for a, b in interfaces}


class TransitionLayerTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()
        exterior = dhe.make_vertex(6, 6, 5, ch="VINF", clr=(0.55, 0.2, 0.75, 1))
        # The source is surrounded by four constructed neighbours. Collinear
        # boundary nodes give both cells identical original shared-wall faces.
        rings = [
            [(1, 1), (3, 1), (3, 3), (1, 3)],
            [(0, 0), (1, 0), (1, 1), (1, 3), (1, 4), (0, 4)],
            [(1, 0), (3, 0), (3, 1), (1, 1)],
            [(3, 0), (4, 0), (4, 4), (3, 4), (3, 3), (3, 1)],
            [(1, 3), (3, 3), (3, 4), (1, 4)],
        ]
        cells = [app.extrude_polygon(Polygon(r), str(i + 1), 5, exterior, app.CELL_COLORS[i])
                 for i, r in enumerate(rings)]
        self.model = app.ExtrusionModel(cells, exterior, (0, 0), app.sew_shared_walls(cells))
        target = Polygon([(2, 1.5), (3.5, 1.5), (3.5, 2.5), (2, 2.5)])
        self.overlay = overlay_top_faces(self.model, target, source_id=1, target_id=9, z=5)
        triangulate_active_regions(self.model)

    def viewer(self, layer=None, overlay=True):
        return interactive_plot_from_lists(
            self.model.active_edges(), dhe.Getdhe()[1], show=False,
            highlight_cells=True, overlay=self.overlay if overlay else None, transition=layer)

    def test_merged_cells_caps_and_adjacency(self):
        original_edges = set(self.model.active_edges())
        positions = {e.V: (e.V.x, e.V.y, e.V.z) for e in original_edges}
        before_neighbours = neighbour_pairs(self.model.shared_walls)
        layer = build_transition_layer(self.model)
        self.assertIs(layer.cell, layer.source_cell)
        self.assertEqual(len(self.model.all_cells), 5)
        self.assertIs(self.model.cells, self.model.all_cells)
        self.assertEqual(len(layer.connecting_facets), 12)
        self.assertEqual(neighbour_pairs(layer.shared_interfaces), before_neighbours)
        self.assertFalse(set(self.model.active_edges()) & original_edges)
        self.assertTrue(original_edges <= dhe.Getdhe()[1])
        self.assertTrue(all((v.x, v.y, v.z) == p for v, p in positions.items()))
        for cell in self.model.cells:
            caps = [f for f in dhe.get_UniqueFace_in_cell(cell.seed)
                    if len({e.V.z for e in app.face_edges(f)}) == 1]
            self.assertEqual(len(caps), 2)
            self.assertEqual({f.V.z for f in caps}, {0, 10})
            self.assertEqual(cell.feature_id, str(cell.dual_vertex.i))
        for facet in layer.connecting_facets:
            self.assertIs(facet.seed.D.V, layer.cell.dual_vertex)
            self.assertIsNot(facet.seed.Adjacent().D.V, self.model.exterior)
            self.assertNotEqual(facet.seed.Adjacent().D.V.logical_feature_id, "1")
        validate_transition_layer(self.model)

    def test_whole_model_sections_partition_domain_at_caps_and_intermediate_levels(self):
        layer = build_transition_layer(self.model)
        domain = unary_union(list(self.overlay.original_roofs.values()))
        for z in (0, 0.01, 2.5, 4.999, 5, 5.001, 6.25, 7.5, 8.75, 9.999, 10):
            with self.subTest(z=z):
                sections = slice_model(self.model, z)
                self.assertEqual(set(sections), {"1", "2", "3", "4", "5"})
                combined = unary_union(list(sections.values()))
                self.assertLess(combined.symmetric_difference(domain).area, 1e-8)
                self.assertAlmostEqual(sum(p.area for p in sections.values()), domain.area)
                self.assertTrue(all(p.is_valid for p in sections.values()))
                if z <= 5:
                    for owner, polygon in sections.items():
                        self.assertTrue(polygon.equals(self.overlay.original_roofs[owner]))
        self.assertTrue(slice_model(self.model, 10)["1"].equals(layer.target_geometry))
        self.assertTrue(layer.horizontal_section(5).equals(layer.source_geometry))
        self.assertTrue(layer.horizontal_section(10).equals(layer.target_geometry))
        for z in (-1, 11, float("nan"), float("inf")):
            with self.subTest(z=z), self.assertRaisesRegex(ValueError, "height"):
                slice_model(self.model, z)
        for z in (4, 11):
            with self.assertRaisesRegex(ValueError, "height"):
                layer.horizontal_section(z)

    def test_crossing_copies_and_source_cap_roles(self):
        layer = build_transition_layer(self.model)
        for crossing in self.overlay.crossing_vertices:
            copies = [v for v in layer.vertex_stages if (v.x, v.y) == (crossing.x, crossing.y)]
            self.assertEqual(len(copies), 2)
            self.assertEqual({v.z for v in copies}, {5, 10})
            self.assertIn(crossing, copies)
            self.assertTrue(all(layer.vertex_roles[v] is VertexRole.BOTH for v in copies))
        for edge in dhe.Cell(layer.cell.seed):
            if layer.vertex_roles[edge.V] is VertexRole.SOURCE:
                self.assertLess(edge.V.z, 10)
        for facet in layer.connecting_facets:
            self.assertEqual(len(app.face_edges(facet.seed)), 3)
            self.assertEqual({p[2] for p in facet.xyz}, {5, 10})

    def test_invalid_heights_and_bad_strip_fail_before_mutation(self):
        before = base_snapshot(self.model)
        counts = (dhe.Vertex.cntId, dhe.DualHalfEdge.cntId)
        for z in (0, 5, float("inf"), float("nan")):
            with self.subTest(z=z), self.assertRaisesRegex(ValueError, "elevation"):
                build_transition_layer(self.model, target_z=z)
            self.assertEqual(base_snapshot(self.model), before)
            self.assertEqual((dhe.Vertex.cntId, dhe.DualHalfEdge.cntId), counts)
            self.assertIsNone(self.model.transition)
        with patch("transition_layer._strip", return_value=[]):
            with self.assertRaisesRegex(ValueError, "gaps|closed manifold"):
                build_transition_layer(self.model)
        self.assertEqual(base_snapshot(self.model), before)
        self.assertEqual((dhe.Vertex.cntId, dhe.DualHalfEdge.cntId), counts)

    def test_repeat_layer_is_rejected_without_changes(self):
        build_transition_layer(self.model)
        before = base_snapshot(self.model)
        with self.assertRaisesRegex(ValueError, "already"):
            build_transition_layer(self.model)
        self.assertEqual(base_snapshot(self.model), before)

    def test_scroll_slider_keys_and_camera_are_synchronized(self):
        layer = build_transition_layer(self.model)
        viewer = self.viewer(layer)
        try:
            panel = viewer.plane_controls
            self.assertEqual((panel.section_slider.valmin, panel.section_slider.valmax), (0, 10))
            for z in (0, 5, 7.5, 10):
                panel.section_slider.set_val(z)
                expected = slice_model(self.model, z)
                self.assertTrue(panel.current_section.equals(expected["1"]))
                self.assertEqual(set(panel.current_sections), set(expected))
            panel.section_slider.set_val(7.5)
            viewer.on_key(SimpleNamespace(key="up"))
            self.assertEqual(panel.section_slider.val, 7.6)
            viewer.on_key(SimpleNamespace(key="down"))
            self.assertEqual(panel.section_slider.val, 7.5)
            viewer.fig.canvas.draw()
            bounds = (viewer.ax.get_xlim(), viewer.ax.get_ylim(), viewer.ax.get_zlim(),
                      viewer.ax.elev, viewer.ax.azim, panel.ax.get_xlim(), panel.ax.get_ylim())
            for axis in (viewer.ax, panel.ax, panel.section_slider.ax):
                x, y = axis.transAxes.transform((0.5, 0.5))
                event = MouseEvent("scroll_event", viewer.fig.canvas, x, y,
                                   button="up", step=1, key="control")
                viewer.fig.canvas.callbacks.process("scroll_event", event)
            self.assertEqual(panel.section_slider.val, 7.8)
            self.assertEqual(bounds, (
                viewer.ax.get_xlim(), viewer.ax.get_ylim(), viewer.ax.get_zlim(),
                viewer.ax.elev, viewer.ax.azim, panel.ax.get_xlim(), panel.ax.get_ylim()))
            panel.move_section(1000)
            self.assertEqual(panel.section_slider.val, 10)
            panel.move_section(-1000)
            self.assertEqual(panel.section_slider.val, 0)
        finally:
            plt.close(viewer.fig)

    def test_only_current_edge_is_highlighted_in_3d_and_projection_keeps_both(self):
        layer = build_transition_layer(self.model)
        viewer = self.viewer(layer)
        try:
            panel = viewer.overlay_panel
            facet = next(f for f in layer.connecting_facets if f.geometry.area > 1e-6)
            point = facet.geometry.representative_point()
            panel.on_click(SimpleNamespace(
                inaxes=panel.ax, button=1, xdata=point.x, ydata=point.y))
            self.assertIs(panel.selected_fragment, facet)
            self.assertEqual(viewer.current_halfedge_line.get_color(), HILIGHT_COLOR)
            self.assertFalse(viewer.face_halfedge_artists)
            self.assertFalse(panel.selection_3d_lines)
            self.assertIsNone(viewer.hi_line)
            self.assertIsNone(viewer.vert_highlight)
            self.assertIs(viewer.face_artist, viewer.selected_face_artist)
            self.assertIsNotNone(viewer.face_artist)
            self.assertEqual(len(panel.halfedge_2d_artists), 3)
            old = [viewer.current_halfedge_line, *panel.halfedge_2d_artists]
            viewer.on_key(SimpleNamespace(key="f"))
            self.assertTrue(all(artist.axes is None for artist in old))
            vertical = next(e for f in layer.connecting_facets for e in app.face_edges(f.seed)
                            if (e.V.x, e.V.y) == (e.S.V.x, e.S.V.y))
            viewer._set_current_edge(vertical)
            viewer._draw_highlight_line()
            self.assertTrue(panel.current_is_vertical)
            self.assertEqual(panel.current_halfedge_2d_artist.get_marker(), "o")
            self.assertIn("vertical", panel.status.get_text())
        finally:
            plt.close(viewer.fig)

    def test_shared_material_follows_feature_and_exterior_is_purple(self):
        layer = build_transition_layer(self.model)
        viewer = self.viewer(layer)
        try:
            base_colors = {c.feature_id: c.dual_vertex.color for c in self.model.cells}
            for cell in layer.cells:
                self.assertEqual(cell.dual_vertex.color, base_colors[cell.logical_feature_id])
            face = layer.connecting_facets[0].seed
            key = tuple(sorted((e.V.x, e.V.y, e.V.z) for e in app.face_edges(face)))
            record = viewer.scene_faces[key]
            for current in (face, face.Adjacent()):
                viewer._set_current_edge(current)
                viewer._draw_highlight_line()
                self.assertEqual(record["owner"], current.D.V.logical_feature_id)
                self.assertIsNone(record["artist"])
                self.assertEqual(tuple(viewer.selected_face_artist.get_facecolor()[0][:3]),
                                 current.D.V.color[:3])
            self.assertTrue(all(r["artist"] is None for r in viewer.scene_faces.values()
                                if r["hidden"]))
            outer = next(e for e in self.model.active_edges()
                         if e.Adjacent().D.V is self.model.exterior)
            viewer._set_current_edge(outer.Adjacent())
            viewer._draw_highlight_line()
            visible = [r for r in viewer.scene_faces.values() if r["artist"] is not None]
            self.assertTrue(visible)
            self.assertTrue(all(r["exterior"] and r["owner"] == "VINF" for r in visible))
            self.assertTrue(all(tuple(r["artist"].get_facecolor()[0][:3]) ==
                                self.model.exterior.color[:3] for r in visible))
            viewer.on_key(SimpleNamespace(key="x"))
            self.assertFalse(viewer.cell_artists)
            viewer.on_key(SimpleNamespace(key="c"))
            self.assertTrue(viewer.cell_artists)
            viewer._set_current_edge(face)
            viewer.on_key(SimpleNamespace(key="c"))
            self.assertEqual(record["owner"], "1")
        finally:
            plt.close(viewer.fig)

    def test_red_edge_only_without_projection_panel(self):
        viewer = self.viewer(overlay=False)
        try:
            self.assertFalse(viewer.face_halfedge_artists)
            self.assertEqual(viewer.current_halfedge_line.get_color(), HILIGHT_COLOR)
            viewer.on_key(SimpleNamespace(key="s"))
            self.assertFalse(viewer.face_halfedge_artists)
            self.assertIsNotNone(viewer.face_artist)
        finally:
            plt.close(viewer.fig)

    def test_step_size_applies_to_all_controls_and_orientations(self):
        layer = build_transition_layer(self.model)
        viewer = self.viewer(layer)
        try:
            controls = viewer.plane_controls
            # Exercise the full state and geometry updates without repeatedly
            # rasterizing the figure during this control-matrix regression.
            with patch.object(viewer.fig.canvas, "draw_idle") as draw:
                for orientation in ("Horizontal", "Vertical", "Inclined"):
                    controls.orientation_dropdown.select(orientation)
                    for step in (0.01, 0.1, 0.5, 1):
                        with self.subTest(orientation=orientation, step=step):
                            controls.step_box.set_val(str(step))
                            self.assertEqual(controls.section_step, step)
                            start = 5 if orientation == "Horizontal" else 0
                            controls.set_position(start)
                            controls.raise_button._observers.process("clicked", None)
                            self.assertAlmostEqual(controls.position_slider.val, start+step)
                            controls.lower_button._observers.process("clicked", None)
                            self.assertAlmostEqual(controls.position_slider.val, start)
                            viewer.on_key(SimpleNamespace(key="up"))
                            self.assertAlmostEqual(controls.position_slider.val, start+step)
                            viewer.on_key(SimpleNamespace(key="down"))
                            self.assertAlmostEqual(controls.position_slider.val, start)
                            controls.on_scroll(SimpleNamespace(inaxes=controls.position_slider.ax, step=1))
                            self.assertAlmostEqual(controls.position_slider.val, start+step)
                            controls.position_slider.set_val(start+1.35*step)
                            self.assertAlmostEqual(controls.position_slider.val, start+step)
                            self.assertIsNotNone(controls.plane_artist)
                            self.assertTrue(all(p.is_valid for p in controls.current_sections.values()))
                self.assertTrue(draw.called)
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)

    def test_dropdown_angles_invalid_steps_and_position_memory(self):
        layer = build_transition_layer(self.model)
        viewer = self.viewer(layer)
        try:
            controls = viewer.plane_controls
            camera = (viewer.ax.get_xlim(), viewer.ax.get_ylim(), viewer.ax.get_zlim(),
                      viewer.ax.elev, viewer.ax.azim)
            with patch.object(viewer.fig.canvas, "draw_idle"):
                controls.set_position(6.2)
                dropdown = controls.orientation_dropdown
                dropdown.toggle()
                self.assertTrue(all(b.ax.get_visible() for b in dropdown.menu))
                dropdown.menu[1]._observers.process("clicked", None)
                self.assertEqual(controls.orientation, "Vertical")
                self.assertFalse(any(b.ax.get_visible() for b in dropdown.menu))
                self.assertFalse(controls.tilt_slider.active)
                self.assertTrue(controls.azimuth_slider.active)
                controls.azimuth_slider.set_val(90)
                self.assertEqual(controls.current_plane.normal, (1, 0, 0))
                controls.set_position(0.3)
                dropdown.select("Inclined")
                controls.tilt_slider.set_val(30)
                self.assertAlmostEqual(controls.current_plane.normal[2], 3**0.5/2)
                self.assertEqual(controls.ax.get_xlabel(), "Plane u (m)")
                dropdown.select("Horizontal")
                self.assertAlmostEqual(controls.position_slider.val, 6.2)
                self.assertFalse(controls.azimuth_slider.active)
                for invalid in ("0", "-1", "nan", "inf", "oops"):
                    controls.step_box.set_val(invalid)
                    self.assertEqual(controls.section_step, 0.1)
                    self.assertEqual(controls.step_box.text, "0.1")
                    self.assertTrue(controls.step_error.get_text())
                controls.step_box.set_val("0.01")
                self.assertEqual(controls.step_error.get_text(), "")
                current = controls.position_slider.val
                controls.step_box.capturekeystrokes = True
                viewer.on_key(SimpleNamespace(key="up"))
                self.assertEqual(controls.position_slider.val, current)
                controls.step_box.capturekeystrokes = False
                dropdown.select("Vertical")
                self.assertAlmostEqual(controls.position_slider.val, 0.3)
                controls.move_section(10000)
                self.assertEqual(controls.position_slider.val, controls.position_slider.valmax)
                controls.move_section(-10000)
                self.assertEqual(controls.position_slider.val, controls.position_slider.valmin)
            self.assertEqual(camera, (viewer.ax.get_xlim(), viewer.ax.get_ylim(), viewer.ax.get_zlim(),
                                      viewer.ax.elev, viewer.ax.azim))
        finally:
            plt.close(viewer.fig)


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class ActualTransitionTests(unittest.TestCase):
    def run_model(self, *args):
        with redirect_stdout(io.StringIO()):
            return app.main(["--scene", "transition", "--target-kind", "polygon", "--no-show", *args])

    def test_actual_shared_topology_volumes_and_default_levels(self):
        model = self.run_model()
        layer = model.transition
        self.assertEqual((layer.source_z, layer.target_z), (5, 10))
        self.assertEqual(min(e.V.z for e in model.active_edges()), 0)
        self.assertEqual(len(model.all_cells), 5)
        self.assertEqual(len(layer.connecting_facets), 24)
        self.assertEqual(len(layer.lower_facets), 5)
        self.assertEqual(len(layer.upper_facets), 5)
        self.assertEqual(len(layer.shared_interfaces), 44)
        self.assertEqual(neighbour_pairs(layer.shared_interfaces), layer.original_neighbours)
        summaries = app.validate_model(model)
        self.assertEqual([(s["vertices"], s["edges"], s["faces"]) for s in summaries],
                         [(32, 64, 34), (34, 62, 30), (23, 40, 19),
                          (29, 51, 24), (32, 57, 27)])
        self.assertAlmostEqual(cell_volume(layer.cell), 1938.807229904, places=6)
        source_vertices = {e.V for e in dhe.Cell(layer.cell.seed)}
        self.assertFalse(any(layer.vertex_roles[v] is VertexRole.SOURCE and v.z == 10
                             for v in source_vertices))
        for z in (0, 5, 5.01, 6.25, 7.5, 8.75, 9.99, 10):
            sections = slice_model(model, z)
            domain = unary_union(list(model.overlay.original_roofs.values()))
            self.assertLess(unary_union(list(sections.values())).symmetric_difference(domain).area, 1e-6)
            self.assertAlmostEqual(sum(p.area for p in sections.values()), domain.area, places=6)
        validate_transition_layer(model)

    def test_custom_levels_and_earlier_stage_flags(self):
        model = self.run_model("--z-down", "-2", "--height", "10", "--target-z", "30")
        self.assertEqual((model.transition.source_z, model.transition.target_z), (10, 30))
        self.assertEqual(min(e.V.z for e in model.active_edges()), -2)
        self.assertAlmostEqual(cell_volume(model.transition.cell),
                               12 * model.transition.source_geometry.area + 4 * 922.8869724517, places=6)
        self.assertEqual(set(slice_model(model, -2)), {"3000", "3001", "3002", "3003", "3004"})
        flat = self.run_model("--no-transition")
        self.assertIsNone(flat.transition)
        self.assertEqual(len(flat.overlay.triangulation.triangles), 16)
        overlay = self.run_model("--no-triangulation")
        self.assertIsNone(overlay.overlay.triangulation)
        legacy = self.run_model("--ids", "2001", "2002", "--height", "10", "--no-overlay")
        self.assertIsNone(legacy.overlay)

    def test_oblique_feature_partition_matches_whole_domain_prism(self):
        model = self.run_model()
        domain = unary_union(list(model.overlay.original_roofs.values()))
        reference = app.ExtrusionModel(
            [app.extrude_polygon(domain, "domain", 10, model.exterior)],
            model.exterior, model.origin, [])
        actual, expected = PlaneSlicer(model), PlaneSlicer(reference)
        for tilt, azimuth in ((90, 0), (90, 45), (90, 90), (30, 0), (45, 40), (70, 150)):
            for offset in (-1, 0, 1):
                with self.subTest(tilt=tilt, azimuth=azimuth, offset=offset):
                    plane = SlicePlane.from_angles(actual.center, tilt, azimuth, offset)
                    sections = actual.sections(plane)
                    combined = unary_union(list(sections.values()))
                    envelope = expected.sections(plane)["domain"]
                    self.assertLess(combined.symmetric_difference(envelope).area, 1e-6)
                    self.assertAlmostEqual(sum(p.area for p in sections.values()), combined.area, places=6)


if __name__ == "__main__":
    unittest.main()
