"""Internal/exterior sewing, live dual incidence and unchanged geometry."""
from collections import defaultdict
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box

import DHECore as dhe
from main import ExtrudedCell, ExtrusionModel, validate_model
from generalization_geometry import prepare_frame
from generalization_model import GeneralizationSession
from plane_slicing import PlaneSlicer, SlicePlane
from top_face_overlay import cell_volume


def face(vertices, inside, exterior):
    first = dhe.MEVVFS(vertices[0], vertices[1], inside, exterior)
    cursor = first
    for vertex in vertices[2:]:
        cursor = dhe.MEV(vertex, cursor.S)
    return dhe.MEF(first, cursor.S).S


def record_snapshot():
    pending, records = list(dhe.dhe), set()
    while pending:
        edge = pending.pop()
        if edge is None or edge in records:
            continue
        records.add(edge)
        pending.extend((edge.S, edge.D, edge.NF, edge.NV))
    return {e: (e.V, e.S, e.D, e.NF, e.NV) for e in records}


def two_faces(separate_owner=False, separate_exterior=False):
    inside = dhe.make_vertex(0, 0, 0, ch="inside")
    exterior = dhe.make_vertex(0, 0, 0, ch="VINF")
    p, q, r, s = [dhe.make_vertex(*xyz) for xyz in ((0,0,0), (1,0,0), (0,1,0), (0,0,1))]
    first = face([p, q, r], inside, exterior)
    second = face([q, p, s], dhe.make_vertex(0, 0, ch="other") if separate_owner else inside,
                  dhe.make_vertex(0, 0, ch="other exterior") if separate_exterior else exterior)
    a = next(e for e in dhe.face_edges(first) if e.V is p and e.S.V is q)
    b = next(e for e in dhe.face_edges(second) if e.V is q and e.S.V is p)
    return a, b


def tetrahedron(sew=dhe.SewComplex):
    inside, exterior = dhe.make_vertex(0, 0, ch="tetra"), dhe.make_vertex(0, 0, ch="VINF")
    p, q, r, s = [dhe.make_vertex(*xyz) for xyz in ((0,0,0), (1,0,0), (0,1,0), (0,0,1))]
    pairs, seeds = defaultdict(list), []
    for vertices in ([p,r,q], [p,q,s], [q,r,s], [r,p,s]):
        seed = face(vertices, inside, exterior)
        seeds.append(seed)
        for edge in dhe.face_edges(seed):
            pairs[frozenset((edge.V, edge.S.V))].append(edge)
    for a, b in pairs.values():
        sew(a, b.S)
    return ExtrusionModel([ExtrudedCell("tetra", seeds[0], inside)], exterior, (0,0), [])


class ComplexSewingTests(unittest.TestCase):
    def setUp(self):
        dhe.reset_globals()

    def test_both_sides_are_sewn_and_repeat_is_a_noop(self):
        a, b = two_faces()
        x, y = a.Adjacent(), b.Adjacent()
        before = [(e.V.x, e.V.y, e.V.z) for e in (a,b,x,y)]
        with patch.object(dhe, "Sew", wraps=dhe.Sew) as sew:
            dhe.SewComplex(a, b.S)
            self.assertEqual(sew.call_count, 2)
        for first, second in ((a,b), (x,y)):
            self.assertIs(first.S, second)
            self.assertIs(second.S, first)
            self.assertIs(first.Adjacent().Adjacent(), first)
        self.assertEqual(before, [(e.V.x, e.V.y, e.V.z) for e in (a,b,x,y)])
        completed = record_snapshot()
        with patch.object(dhe, "Sew", side_effect=AssertionError("Repeated seam was sewn again")):
            dhe.SewComplex(a, b.S)
        self.assertEqual(completed, record_snapshot())

    def test_repair_interior_only_seam(self):
        a, b = two_faces()
        x, y = a.Adjacent(), b.Adjacent()
        dhe.Sew(a, b.S)
        self.assertIs(a.S, b)
        self.assertIsNot(x.S, y)
        with patch.object(dhe, "Sew", wraps=dhe.Sew) as sew:
            dhe.SewComplex(a, b.S)
            self.assertEqual(sew.call_count, 1)
        self.assertIs(a.S, b)
        self.assertIs(x.S, y)
        self.assertIs(y.S, x)

    def test_invalid_pairings_fail_before_mutation(self):
        for kind in ("endpoints", "interior owner", "exterior owner", "same use", "incomplete"):
            with self.subTest(kind=kind):
                dhe.reset_globals()
                a, b = two_faces(kind == "interior owner", kind == "exterior owner")
                argument = b.NF.S if kind == "endpoints" else a.S if kind == "same use" else b.S
                before = record_snapshot()
                with self.assertRaises(ValueError):
                    dhe.SewComplex(None if kind == "incomplete" else a, argument)
                self.assertEqual(before, record_snapshot())

    def test_validator_rejects_old_interior_only_construction(self):
        model = tetrahedron(dhe.Sew)
        # The old checks see a perfectly closed bounded tetrahedron.
        edges = dhe.Cell(model.cells[0].seed)
        self.assertEqual(len({e.V for e in edges}), 4)
        self.assertTrue(all(e.S.S is e and e.D.D is e for e in edges))
        self.assertEqual(len(dhe.get_UniqueFace_in_cell(model.cells[0].seed)), 4)
        with self.assertRaisesRegex(ValueError, "unsewn|non-live"):
            validate_model(model)

    def test_complete_tetrahedron_and_corrupt_exterior_or_dual_records(self):
        model = tetrahedron()
        self.assertEqual(validate_model(model)[0]["vertices"], 4)
        self.assertAlmostEqual(cell_volume(model.cells[0]), 1/6)
        inside = set(model.active_edges())
        exterior = {e.Adjacent() for e in inside}
        self.assertEqual(set(dhe.Cell(next(iter(exterior)))), exterior)
        x = next(iter(exterior))
        for target, attribute, value in ((x, "S", x), (x.D, "NF", None)):
            original = getattr(target, attribute)
            try:
                setattr(target, attribute, value)
                with self.assertRaises(ValueError):
                    validate_model(model)
            finally:
                setattr(target, attribute, original)
        dhe.killdhe.add(x.D)
        with self.assertRaisesRegex(ValueError, "retired"):
            validate_model(model)
        dhe.killdhe.remove(x.D)
        validate_model(model)

    def test_disconnected_exterior_and_four_cell_junction(self):
        for polygons in ([box(0,0,4,4),box(8,0,12,4)],
                         [box(0,0,4,4),box(4,0,8,4),box(0,4,4,8),box(4,4,8,8)]):
            with self.subTest(count=len(polygons)):
                session = GeneralizationSession(prepare_frame(gpd.GeoDataFrame(geometry=polygons, crs=2180)))
                self.assertEqual(len(validate_model(session.model)), len(polygons))
                self.assertEqual(len({c.dual_vertex for c in session.model.cells}), len(polygons))

    def test_all_operations_preserve_geometry_and_counts_against_old_sewing(self):
        def measurements(operation, legacy):
            polygons = [box(0,0,10,5),box(10 if operation == "merging" else 14,0,20 if operation == "merging" else 24,5),box(30,0,40,5)]
            with ExitStack() as context:
                if legacy:
                    context.enter_context(patch.object(dhe, "SewComplex", side_effect=dhe.Sew))
                    context.enter_context(patch.object(dhe, "validate_complex_topology", return_value=None))
                session = GeneralizationSession(prepare_frame(gpd.GeoDataFrame(geometry=polygons, crs=2180)))
                session.apply(session.preview(operation, [1,2] if operation in ("merging","aggregation") else [1],
                                              winner=1 if operation == "merging" else None))
                summary = validate_model(session.model)
                faces = sorted((f.owner, f.part, tuple(sorted(f.points))) for f in session.state.faces)
                sections = [PlaneSlicer(session.model).sections(SlicePlane.horizontal(z),include_degenerate=True) for z in (2.5,5,10,15)]
                volumes = {c.feature_id:cell_volume(c) for c in session.model.cells}
                return summary, faces, sections, volumes
        for operation in ("simplification","smoothing","point","polyline","merging","aggregation"):
            with self.subTest(operation=operation):
                old, new = measurements(operation, True), measurements(operation, False)
                self.assertEqual(old[0], new[0])
                self.assertEqual(old[1], new[1])
                self.assertEqual(old[3], new[3])
                for before, after in zip(old[2],new[2]):
                    self.assertEqual(set(before), set(after))
                    for key in before:
                        self.assertLess(before[key].hausdorff_distance(after[key]), 1e-8)


if __name__ == "__main__":
    unittest.main()
