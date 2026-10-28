"""Aggregation geometry, source ownership and preview integration regressions."""

import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely import union_all
from shapely.geometry import Polygon, box

import DHECore as dhe
import main as app
from aggregation_geometry import build_aggregation_preview, validate_aggregation
from plot_aggregation_preview import AggregationPreviewPlot


class AggregationGeometryTests(unittest.TestCase):
    def check_partition(self, preview):
        metrics = validate_aggregation(preview)
        for name in ("overlap_area", "missing_area", "excess_area"):
            self.assertLessEqual(metrics[name], preview.area_tolerance)
        self.assertLessEqual(preview.frame_stability_error, preview.area_tolerance)
        for key, source in preview.sources.items():
            self.assertLess(source.difference(preview.partitions[key]).area, preview.area_tolerance)
        for target in preview.target_lines:
            self.assertIn(target.kind, ("hull", "meeting"))
            self.assertTrue(set(target.source_ids) <= set(preview.sources))

    def test_separated_sources_grow_to_shared_target_and_hull(self):
        preview = build_aggregation_preview({"left": box(0, 0, 2, 2), "right": box(6, 0, 8, 2)})
        self.check_partition(preview)
        self.assertAlmostEqual(preview.partitions["left"].area, 8)
        self.assertAlmostEqual(preview.partitions["right"].area, 8)
        meeting = union_all([t.geometry for t in preview.target_lines if t.kind == "meeting"])
        self.assertAlmostEqual(meeting.length, 2)
        self.assertAlmostEqual(meeting.bounds[0], 4)
        self.assertTrue(any(t.kind == "hull" for t in preview.target_lines))
        self.assertTrue(preview.skeleton_arcs)
        self.assertTrue(all(min(arc.times) >= -1e-6 for arc in preview.skeleton_arcs))

    def test_shared_boundaries_do_not_generate_growth_fronts(self):
        sources = {"a": box(0, 0, 2, 2), "b": box(2, 0, 4, 2), "c": box(7, 1, 9, 3)}
        preview = build_aggregation_preview(sources)
        self.check_partition(preview)
        shared = sources["a"].boundary.intersection(sources["b"].boundary)
        for face in preview.growth_faces:
            self.assertLess(face.source_edge.intersection(shared).length, 1e-6)

    def test_holes_and_multiple_gap_regions(self):
        # The left pair encloses a bounded gap; an isolated interior source is
        # also a hole of the larger exterior transition region.
        sources = {
            "lower": Polygon([(0, 0), (6, 0), (6, 2), (4, 2), (4, 1), (1, 1), (1, 3), (0, 3)]),
            "upper": Polygon([(0, 3), (1, 3), (1, 4), (4, 4), (4, 2), (6, 2), (6, 5), (0, 5)]),
            "isolated": box(9, 2, 10, 3),
            "right": box(13, 0, 15, 5),
        }
        preview = build_aggregation_preview(sources)
        self.check_partition(preview)
        self.assertGreaterEqual(len(preview.gap_regions), 2)
        self.assertTrue(any(region.interiors for region in preview.gap_regions))

    def test_input_order_does_not_change_ownership(self):
        sources = {"a": box(0, 0, 2, 3), "b": box(6, 1, 8, 4), "c": box(3, 6, 5, 8)}
        forward = build_aggregation_preview(sources)
        reverse = build_aggregation_preview(dict(reversed(list(sources.items()))))
        for key in sources:
            self.assertLess(forward.partitions[key].symmetric_difference(reverse.partitions[key]).area,
                            forward.area_tolerance)

    def test_already_convex_union_needs_no_skeleton(self):
        preview = build_aggregation_preview({"a": box(0, 0, 2, 2), "b": box(2, 0, 4, 2)})
        self.check_partition(preview)
        self.assertFalse(preview.gap_regions)
        self.assertFalse(preview.skeleton_arcs)
        self.assertTrue(all(target.is_empty for target in preview.targets.values()))

    def test_invalid_sources_and_backend_failure_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            build_aggregation_preview({})
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_aggregation_preview({"a": box(0, 0, 2, 2), "b": box(1, 0, 3, 2)})
        with patch("py_straight_skeleton.compute_skeleton", side_effect=RuntimeError("event failure")):
            with self.assertRaisesRegex(ValueError, "frame-independent.*event failure"):
                build_aggregation_preview({"a": box(0, 0, 1, 1), "b": box(4, 0, 5, 1)})


@unittest.skipUnless(Path(app.input_shp).is_file(), "User's source shapefile is unavailable")
class ActualAggregationTests(unittest.TestCase):
    check_partition = AggregationGeometryTests.check_partition

    def test_actual_sources_and_preview_do_not_modify_dhe(self):
        app.build_model(ids=[2001, 2002])
        original_edges, original_killed = dhe.Getdhe()
        before = (tuple(original_edges), set(original_killed))
        with redirect_stdout(io.StringIO()):
            preview = app.main(["--no-show"])
        self.assertEqual(list(preview.sources), [str(i) for i in app.AGGREGATION_IDS])
        self.assertEqual(len(list(preview.union.geoms)), 4)
        self.assertEqual(len(preview.gap_regions), 2)
        self.assertAlmostEqual(preview.union.area, 639.2298201, places=5)
        self.assertAlmostEqual(preview.hull.area, 1206.6821338, places=5)
        self.check_partition(preview)
        self.assertEqual(tuple(dhe.Getdhe()[0]), before[0])
        self.assertEqual(dhe.Getdhe()[1], before[1])
        self.assertTrue(all(preview.targets[key].length > 0 for key in preview.sources))
        viewer = AggregationPreviewPlot(preview)
        try:
            self.assertEqual(len(set(viewer.colors.values())), 6)
            viewer.checkbox.set_active(0)
            self.assertTrue(all(a.get_visible() for a in viewer.merged_artists))
            self.assertFalse(any(a.get_visible() for a in viewer.partition_artists))
            viewer.checkbox.set_active(0)
            self.assertTrue(all(a.get_visible() for a in viewer.partition_artists))
            viewer.fig.canvas.draw()
        finally:
            plt.close(viewer.fig)

    def test_cli_preview_export_and_custom_selection(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            png = Path(directory) / "aggregation.png"
            preview = app.main(["--ids", "8", "17", "--no-show", "--preview", str(png)])
            self.assertEqual(set(preview.sources), {"8", "17"})
            self.assertGreater(png.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
