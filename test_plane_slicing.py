"""Analytic and topological checks for arbitrary plane sections of DHE cells."""

from collections import defaultdict
from math import sqrt
import unittest

import numpy as np
from shapely.geometry import Polygon, LineString

import DHECore as dhe
import main as app
from plane_slicing import PlaneSlicer, SlicePlane, _regions_from_lines, slice_model_plane


class PlaneSlicingTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()
        self.exterior = dhe.make_vertex(8, 8, 8, ch="VINF")

    def prism(self, polygon, height=4):
        cell = app.extrude_polygon(polygon, "shape", height, self.exterior)
        return app.ExtrusionModel([cell], self.exterior, (0, 0), [])

    def test_analytic_horizontal_vertical_and_inclined_areas(self):
        model = self.prism(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))
        slicer = PlaneSlicer(model)
        planes = [
            (SlicePlane.horizontal(2), 4),
            (SlicePlane.from_angles(slicer.center, 90, 90), 8),
            (SlicePlane.from_angles(slicer.center, 90, 0), 8),
            (SlicePlane.from_angles(slicer.center, 45, 90), 4*sqrt(2)),
        ]
        for plane, expected in planes:
            with self.subTest(normal=plane.normal):
                result = slicer.sections(plane)["shape"]
                self.assertAlmostEqual(result.area, expected, places=8)
                xy = np.asarray(result.exterior.coords)
                xyz = plane.lift(xy)
                np.testing.assert_allclose(plane.signed_distance(xyz), 0, atol=1e-10)
                np.testing.assert_allclose(plane.project(xyz), xy, atol=1e-10)
        self.assertEqual(slice_model_plane(model, planes[0][0])["shape"].area, 4)

    def test_concave_cap_multiple_intersection_segments(self):
        # A vertical cut through the U's arms must be disconnected. Joining
        # only the first/last face intersection would incorrectly fill the gap.
        polygon = Polygon([(0, 0), (4, 0), (4, 4), (3, 4),
                           (3, 1), (1, 1), (1, 4), (0, 4)])
        model = self.prism(polygon, height=2)
        slicer = PlaneSlicer(model)
        result = slicer.sections(SlicePlane.from_angles(slicer.center, 90, 0))["shape"]
        self.assertEqual(result.geom_type, "MultiPolygon")
        self.assertEqual(len(result.geoms), 2)
        self.assertAlmostEqual(result.area, 4)
        self.assertTrue(slicer.sections(SlicePlane.horizontal(0))["shape"].equals(polygon))
        self.assertTrue(slicer.sections(SlicePlane.horizontal(2))["shape"].equals(polygon))

    def test_coplanar_boundary_tangent_edge_and_empty_sections(self):
        model = self.prism(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))
        slicer = PlaneSlicer(model)
        wall = SlicePlane.from_angles((0, 0, 2), 90, 90)
        self.assertAlmostEqual(slicer.sections(wall)["shape"].area, 8)
        tangent = SlicePlane.from_angles((0, 0, 2), 90, 45)
        self.assertEqual(slicer.sections(tangent), {})
        self.assertEqual(slicer.sections(SlicePlane.from_angles((-0.01, 0, 2), 90, 90)), {})
        self.assertEqual(slicer.sections(SlicePlane.horizontal(4.1)), {})
        outline = slicer.plane_outline(SlicePlane.horizontal(2))
        self.assertEqual(len(outline), 4)
        np.testing.assert_allclose(outline[:, 2], 2)

    def test_even_odd_ring_nesting_preserves_hole_and_island(self):
        rings = [
            [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
            [(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)],
            [(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)],
        ]
        result = _regions_from_lines([LineString(r) for r in rings])
        self.assertEqual(result.geom_type, "MultiPolygon")
        self.assertAlmostEqual(result.area, 68)
        self.assertEqual(sum(len(p.interiors) for p in result.geoms), 1)

    def test_hole_in_actual_genus_zero_dhe_cell_section(self):
        # An open-topped box, constructed from exposed unit voxel faces, is a
        # genus-zero solid whose upper horizontal section contains a hole.
        occupied = {(i, j, k) for i in range(4) for j in range(4) for k in range(3)
                    if k == 0 or i in (0, 3) or j in (0, 3)}
        templates = [
            ((-1, 0, 0), [(0,0,0),(0,0,1),(0,1,1),(0,1,0)]),
            ((1, 0, 0), [(1,0,0),(1,1,0),(1,1,1),(1,0,1)]),
            ((0, -1, 0), [(0,0,0),(1,0,0),(1,0,1),(0,0,1)]),
            ((0, 1, 0), [(0,1,0),(0,1,1),(1,1,1),(1,1,0)]),
            ((0, 0, -1), [(0,0,0),(0,1,0),(1,1,0),(1,0,0)]),
            ((0, 0, 1), [(0,0,1),(1,0,1),(1,1,1),(0,1,1)]),
        ]
        dual = dhe.make_vertex(0.5, 0.5, 0.5, ch="box")
        cache, pairs, seeds = {}, defaultdict(list), []
        for voxel in sorted(occupied):
            for direction, corners in templates:
                if tuple(voxel[i]+direction[i] for i in range(3)) in occupied:
                    continue
                vertices = []
                for point in corners:
                    xyz = tuple(voxel[i]+point[i] for i in range(3))
                    if xyz not in cache:
                        cache[xyz] = dhe.make_vertex(*xyz)
                    vertices.append(cache[xyz])
                first = dhe.MEVVFS(vertices[0], vertices[1], dual, self.exterior)
                cursor = first
                for vertex in vertices[2:]:
                    cursor = dhe.MEV(vertex, cursor.S)
                seed = dhe.MEF(first, cursor.S).S
                seeds.append(seed)
                for edge in dhe.face_edges(seed):
                    pairs[frozenset((edge.V, edge.S.V))].append(edge)
        for first, second in pairs.values():
            dhe.Sew(first, second.S)
        model = app.ExtrusionModel([app.ExtrudedCell("box", seeds[0], dual)],
                                   self.exterior, (0, 0), [])
        app.validate_model(model)
        slicer = PlaneSlicer(model)
        self.assertAlmostEqual(slicer.sections(SlicePlane.horizontal(0.5))["box"].area, 16)
        for z in (1.5, 2.5, 3):
            result = slicer.sections(SlicePlane.horizontal(z))["box"]
            self.assertAlmostEqual(result.area, 12)
            self.assertEqual(len(result.interiors), 1)

    def test_plane_basis_validation_and_angle_endpoints(self):
        for arguments in ((float("nan"), 0, 0), (91, 0, 0), (30, 0, float("inf"))):
            with self.assertRaises(ValueError):
                SlicePlane.from_angles((0, 0, 0), *arguments)
        with self.assertRaisesRegex(ValueError, "orthonormal"):
            SlicePlane((0,0,0), (0,0,1), (2,0,0), (0,1,0))
        self.assertEqual(SlicePlane.from_angles((0,0,0), 0).normal, (0,0,1))
        self.assertEqual(SlicePlane.from_angles((0,0,0), 90, 90).normal, (1,0,0))

    def test_plane_on_shared_face_assigns_boundary_area_once(self):
        polygons = [Polygon([(0,0),(2,0),(2,2),(0,2)]),
                    Polygon([(2,0),(4,0),(4,2),(2,2)])]
        cells = [app.extrude_polygon(p,str(i+1),4,self.exterior) for i,p in enumerate(polygons)]
        model = app.ExtrusionModel(cells,self.exterior,(0,0),app.sew_shared_walls(cells))
        plane = SlicePlane.from_angles((2,1,2),90,90)
        sections = PlaneSlicer(model).sections(plane)
        self.assertEqual(set(sections),{"1"})
        self.assertAlmostEqual(sections["1"].area,8)


if __name__ == "__main__":
    unittest.main()
