"""Topology and viewer regression checks; run with python -m unittest -v."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.geometry import MultiPolygon, Polygon

import DHECore as dhe
import main as app
from plot_dhe_interactive import interactive_plot_from_lists


class ExtrusionTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()
        self.exterior = dhe.make_vertex(5, 5, 5, ch="VINF")

    def model_for(self, polygons):
        cells = [
            app.extrude_polygon(polygon, str(i + 1), 10, self.exterior,
                                app.CELL_COLORS[i % 2])
            for i, polygon in enumerate(polygons)
        ]
        return app.ExtrusionModel(cells, self.exterior, (0, 0),
                                  app.sew_shared_walls(cells))

    def test_square_is_closed_prism(self):
        model = self.model_for([Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])])
        summary = app.validate_model(model)[0]
        self.assertEqual((summary["vertices"], summary["edges"], summary["faces"]), (8, 12, 6))
        self.assertEqual(summary["neighbors"], [])
        for face in dhe.get_UniqueFace_in_cell(model.cells[0].seed):
            self.assertTrue(dhe.is_Face_planar(face))

    def test_concave_clockwise_polygon(self):
        polygon = Polygon([(0, 0), (0, 3), (1, 3), (1, 1), (3, 1), (3, 0)])
        model = self.model_for([polygon])
        summary = app.validate_model(model)[0]
        self.assertEqual((summary["vertices"], summary["edges"], summary["faces"]), (12, 18, 8))

    def test_shared_wall_sews_without_merging_cells(self):
        model = self.model_for([
            Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
            Polygon([(2, 0), (4, 0), (4, 2), (2, 2)]),
        ])
        self.assertEqual(len(model.shared_walls), 1)
        self.assertEqual([s["neighbors"] for s in app.validate_model(model)], [["2"], ["1"]])
        self.assertEqual(len({edge.V for edge in model.active_edges()}), 12)
        self.assertFalse(set(model.active_edges()) & dhe.Getdhe()[1])
        self.assertIsNot(model.cells[0].dual_vertex, model.cells[1].dual_vertex)

    def test_disjoint_cells_stay_disconnected(self):
        model = self.model_for([
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
        ])
        self.assertEqual(model.shared_walls, [])
        self.assertEqual([s["neighbors"] for s in app.validate_model(model)], [[], []])

    def test_invalid_height_and_unsupported_geometry(self):
        square = Polygon([(0, 0), (3, 0), (3, 3), (0, 3)])
        for height in (0, -1, float("nan"), float("inf")):
            with self.subTest(height=height), self.assertRaisesRegex(ValueError, "height"):
                app.extrude_polygon(square, "x", height, self.exterior)
        hole = Polygon(square.exterior.coords, [[(1, 1), (2, 1), (2, 2), (1, 2)]])
        for geom, message in ((hole, "holes"), (MultiPolygon([square]), "Polygon geometry"),
                              (Polygon([(0, 0), (2, 2), (0, 2), (2, 0)]), "invalid")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                app.extrude_polygon(geom, "x", 10, self.exterior)

    def test_broken_boundary_is_rejected(self):
        model = self.model_for([Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])])
        model.cells[0].seed.NF = None
        with self.assertRaisesRegex(ValueError, "open face"):
            app.face_edges(model.cells[0].seed)

    def test_viewer_navigation_rendering_and_dual_controls(self):
        model = self.model_for([
            Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
            Polygon([(2, 0), (4, 0), (4, 2), (2, 2)]),
        ])
        viewer = interactive_plot_from_lists(
            model.active_edges(), dhe.Getdhe()[1], show=False, highlight_cells=True,
        )
        try:
            self.assertEqual(len(viewer.cell_artists), 11)  # one shared face artist
            start = viewer.e
            viewer.on_key(SimpleNamespace(key="f"))
            self.assertIs(viewer.e, start.NF)
            viewer.on_key(SimpleNamespace(key="s"))
            self.assertIs(viewer.e, start.NF.S)
            shared = model.shared_walls[0][0]
            viewer._set_current_edge(shared)
            viewer.on_key(SimpleNamespace(key="a"))
            self.assertIs(viewer.e.D.V, model.cells[1].dual_vertex)
            viewer.checkbox.set_active(0)
            self.assertTrue(viewer.dual_vertex_artists)
            viewer.checkbox.set_active(1)
            self.assertEqual(len(viewer.dual_edge_artists), 1)
            viewer.checkbox.set_active(0)
            viewer.checkbox.set_active(1)
            self.assertFalse(viewer.dual_vertex_artists)
            self.assertFalse(viewer.dual_edge_artists)
            viewer.on_key(SimpleNamespace(key="x"))
            self.assertFalse(viewer.cell_artists)
            viewer.on_key(SimpleNamespace(key="c"))
            self.assertEqual(len(viewer.cell_artists), 6)
            viewer.on_key(SimpleNamespace(key="k"))
            self.assertIsNotNone(viewer.e)
            viewer.ax.view_init(elev=25, azim=45)
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)


class ShapefileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "footprints.shp"
        gpd.GeoDataFrame({
            "osm_id": ["2001", "2002"],
            "geometry": [
                Polygon([(500000, 5600000), (500002, 5600000), (500002, 5600002), (500000, 5600002)]),
                Polygon([(500002, 5600000), (500004, 5600000), (500004, 5600002), (500002, 5600002)]),
            ],
        }, crs=app.PROJECTED_CRS).to_file(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_shapefile_loading_ids_and_projection(self):
        frame = app.load_selected_polygons(self.path, [2002, 2001])
        self.assertEqual(list(frame.osm_id), ["2002", "2001"])
        self.assertEqual(frame.crs.to_epsg(), 32633)
        model = app.build_model(self.path, ids=[2001, 2002])
        self.assertEqual(model.origin, (500000, 5600000))
        self.assertEqual(len(model.shared_walls), 1)
        app.validate_model(model)

    def test_missing_ids_field_and_file(self):
        for ids, field, message in (([9999], "osm_id", "not found"),
                                    ([2001], "missing", "ID field")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                app.load_selected_polygons(self.path, ids, field)
        with self.assertRaises(FileNotFoundError):
            app.load_selected_polygons(self.path.with_name("missing.shp"))

    @unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
    def test_actual_footprints_and_repeatable_reset(self):
        for _ in range(2):
            model = app.build_model(ids=[2001, 2002])
            summaries = app.validate_model(model)
            self.assertEqual(
                [(s["id"], s["vertices"], s["edges"], s["faces"]) for s in summaries],
                [("2001", 46, 69, 25), ("2002", 16, 24, 10)],
            )
            self.assertEqual(len(model.shared_walls), 3)
            self.assertEqual([s["neighbors"] for s in summaries], [["2002"], ["2001"]])


if __name__ == "__main__":
    unittest.main()
