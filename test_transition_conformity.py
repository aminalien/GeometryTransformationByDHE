"""Regression tests for junction provenance, all cases and compact histories."""
from pathlib import Path
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box
import DHECore as dhe
from generalization_geometry import DEFAULT_DATA,prepare_frame,load_shapefile
from generalization_model import GeneralizationSession,Face,Patch,_prism,_core,_recipient,realize
from plane_slicing import PlaneSlicer,SlicePlane
from shell_conformity import (CoordinateRegistry,TransitionShellError,check_oriented_shell,
                              check_face_intersections,check_interface_pairs)


def build(polygons):
    return GeneralizationSession(prepare_frame(gpd.GeoDataFrame(geometry=polygons,crs=2180)))


def counts(model,owner):
    cell=next(c for c in model.cells if c.feature_id==str(owner))
    edges=dhe.Cell(cell.seed)
    return (len({e.V for e in edges}),len({frozenset((e.V,e.S.V)) for e in edges}),len(dhe.get_UniqueFace_in_cell(cell.seed)))


def cycle(points):
    points=tuple(points)
    return min(points[i:]+points[:i] for i in range(len(points)))


class ShellDiagnosticTests(unittest.TestCase):
    def setUp(self):self.faces=_prism(1,box(0,0,1,1),0,1)

    def test_incidence_orientation_and_duplicate_diagnostics(self):
        cases=[(self.faces[:-1],'unmatched_edges'),
               ([Face(f.owner,list(reversed(f.points)) if i==0 else f.points,f.part) for i,f in enumerate(self.faces)],'inconsistent_orientation'),
               (self.faces+[self.faces[0]],'nonmanifold_edge_incidence')]
        for faces,classification in cases:
            with self.subTest(classification=classification):
                with self.assertRaises(TransitionShellError) as caught:check_oriented_shell(faces)
                self.assertEqual(caught.exception.report['classification'],classification)
                self.assertTrue(caught.exception.report['edges'])

    def test_disconnected_components_and_pinched_vertex(self):
        with self.assertRaises(TransitionShellError) as caught:
            check_oriented_shell(self.faces+_prism(1,box(2,0,3,1),0,1))
        self.assertEqual(caught.exception.report['classification'],'disconnected_shell')
        pinched=[Face(1,[(0,0,0) if p==(1,1,1) else p for p in f.points],f.part) for f in self.faces]
        with self.assertRaises(TransitionShellError) as caught:check_oriented_shell(pinched)
        self.assertEqual(caught.exception.report['classification'],'invalid_vertex_link')

    def test_combinatorially_closed_but_self_intersecting_shell(self):
        triangles=[]
        for face in self.faces:
            points=[(.5,.5,-.5) if p==(1,1,1) else p for p in face.points]
            triangles.extend(Face(1,[points[0],points[i],points[i+1]]) for i in range(1,len(points)-1))
        check_oriented_shell(triangles)
        with self.assertRaises(TransitionShellError) as caught:check_face_intersections(triangles)
        self.assertEqual(caught.exception.report['classification'],'self_intersection')
        check_face_intersections(self.faces)

    def test_interface_orientation_and_owner_are_not_guessed(self):
        a=self.faces[0]
        b=Face(2,a.points,a.part)
        with self.assertRaises(TransitionShellError):check_interface_pairs([a,b])
        check_interface_pairs([a,Face(2,list(reversed(a.points)),a.part)])

    def test_different_levels_and_small_real_gaps_remain_distinct(self):
        registry=CoordinateRegistry([(1,2,5),(1,2,6)])
        a=registry.canonical((1,2,5),'source')
        b=registry.canonical((1,2,5+1e-12),'target')
        self.assertNotEqual(a,b)
        self.assertNotEqual(registry.canonical((1,2,5)),registry.canonical((1+1e-9,2,5)))
        large_z=CoordinateRegistry([(0,0,1e12),(10,10,1e12)])
        self.assertNotEqual(large_z.canonical((1,1,1e12)),large_z.canonical((1+1e-6,1,1e12)))
        s=build([box(0,0,1,1),box(1+1e-8,0,2,1)])
        self.assertEqual(len(s.model.shared_walls),0)

    def test_endpoint_level_is_exact_without_barycentric_reconstruction(self):
        facet=Patch(1,[(0,0,.1),(1,0,.2),(0,1,.2)])
        self.assertEqual(facet.height((.25,.75)),.2)
        self.assertEqual(facet.height((0,0)),.1)
        self.assertIs(facet.height((.25,.75)),facet.height((.25,.75)))

    def test_tiny_uncovered_edge_does_not_acquire_an_unrelated_owner(self):
        source=box(100,100,110,110)
        facet=Patch(1,[(100,100,5),(100+1e-10,100,5),(105,105,15)])
        self.assertIsNone(_recipient(facet,{1:source,2:box(0,0,10,10)},[1],5))

    def test_horizontal_caps_are_allowed_but_not_mislabelled_as_inclined_facets(self):
        s=build([box(0,0,10,10)])
        faces=[Face(f.owner,f.points,'transition' if f.part=='top' else f.part) for f in s.state.faces]
        core=_core()
        with self.assertRaises(TransitionShellError) as caught:realize(faces,s.origin)
        self.assertEqual(caught.exception.report['classification'],'horizontal_transition_facet')
        self.assertIs(_core()[0],core[0])


class CompactHistoryTests(unittest.TestCase):
    def test_unchanged_isolated_prism_keeps_eight_vertices_and_six_faces(self):
        s=build([box(0,0,10,10),box(30,0,40,10)])
        for _ in range(3):
            previous=[(v,v.x,v.y,v.z) for e in s.model.active_edges() for v in [e.V]]
            proposal=s.preview('smoothing',[1])
            self.assertTrue(all((v.x,v.y,v.z)==(x,y,z) for v,x,y,z in previous))
            s.apply(proposal)
            self.assertEqual(counts(s.model,2),(8,12,6))
            self.assertEqual(max(e.V.z for e in dhe.Cell(next(c for c in s.model.cells if c.feature_id=='2').seed)),s.state.z)

    def test_shared_unchanged_walls_keep_ownership_and_compact_counts(self):
        s=build([box(0,0,10,10),box(10,0,20,10),box(30,0,40,10)])
        for operation in ('smoothing','point'):
            s.apply(s.preview(operation,[3]))
            self.assertEqual(counts(s.model,1),(8,12,6))
            self.assertEqual(counts(s.model,2),(8,12,6))
            self.assertEqual(len(s.model.shared_walls),1)

    def test_old_inclined_faces_and_sections_are_preserved(self):
        s=build([box(0,0,10,10),box(30,0,40,10)])
        s.apply(s.preview('smoothing',[1]))
        old={cycle(f.points) for f in s.state.faces if f.owner==1 and f.part=='transition'}
        sections={z:PlaneSlicer(s.model).sections(SlicePlane.horizontal(z))['1'] for z in (2.5,7.5,10,12.5)}
        s.apply(s.preview('smoothing',[2]))
        new={cycle(f.points) for f in s.state.faces if f.owner==1 and f.part=='transition'}
        self.assertEqual(old,new)
        for z,g in sections.items():
            actual=PlaneSlicer(s.model).sections(SlicePlane.horizontal(z))['1']
            self.assertLess(actual.symmetric_difference(g).area,1e-9)

    def test_no_default_triangulation_for_unchanged_natr(self):
        s=build([box(0,0,10,10),box(10,0,20,10)])
        with patch('generalization_model.constrained_delaunay_triangles',side_effect=AssertionError('NATR was triangulated')):
            s.apply(s.preview('simplification',[1],tolerance=.1))
        self.assertEqual(counts(s.model,1),(8,12,6))

    def test_zero_and_unresolvable_intervals_are_transactional(self):
        s=build([box(0,0,10,10)])
        original=s.state;core=_core()
        for dz in (0,-1,1e-12):
            with self.assertRaises(ValueError):s.preview('point',[1],dz=dz)
            self.assertIs(s.state,original);self.assertIs(_core()[0],core[0])
        s.apply(s.preview('point',[1],dz=.001))
        self.assertEqual(s.state.features[1].geometry.geom_type,'Point')
        large=GeneralizationSession(prepare_frame(gpd.GeoDataFrame(geometry=[box(0,0,10,10)],crs=2180)),initial_height=1e12)
        with self.assertRaises(TransitionShellError) as caught:large.preview('point',[1],dz=1e-6)
        self.assertEqual(caught.exception.report['classification'],'unresolvable_transition_interval')


class LegacyRollbackTests(unittest.TestCase):
    def test_allocation_and_postvalidation_restore_legacy_cells(self):
        from test_collapse_targets import make_base
        from shapely.geometry import Polygon,Point,LineString
        from transition_targets import TargetDefinition
        from transition_layer import build_transition_layer
        from top_face_overlay import overlay_top_faces
        from active_region_triangulation import triangulate_active_regions
        import main
        for kind,geometry in [('polygon',Polygon([(2,1.5),(3.5,1.5),(3.5,2.5),(2,2.5)])),
                              ('point',Point(2,2)),('polyline',LineString([(1.6,2.4),(1.5,1.8),(2.4,1.7)]))]:
            for fail in ('allocation','postvalidation'):
                with self.subTest(kind=kind,fail=fail):
                    model=make_base()
                    if kind=='polygon':
                        overlay_top_faces(model,geometry,source_id=1,target_id=9,z=5)
                        triangulate_active_regions(model)
                    ledger=list(dhe.dhe);killed=set(dhe.killdhe);counters=(dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
                    edges=[(e,e.S,e.D,e.NF,e.NV,e.V) for e in model.active_edges()]
                    cells=model.cells
                    target='DHECore.MEF' if fail=='allocation' else 'transition_layer.validate_transition_layer'
                    with patch(target,side_effect=ValueError('transaction probe')):
                        with self.assertRaisesRegex(ValueError,'transaction probe'):
                            build_transition_layer(model,target=TargetDefinition(kind,'9',geometry),source_id='1')
                    self.assertIs(model.cells,cells);self.assertIsNone(model.transition)
                    self.assertEqual(dhe.dhe,ledger);self.assertEqual(dhe.killdhe,killed)
                    self.assertEqual((dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),counters)
                    self.assertTrue(all((e.S,e.D,e.NF,e.NV,e.V)==tuple(values) for e,*values in edges))
                    main.validate_model(model)


@unittest.skipUnless(Path(DEFAULT_DATA).is_file(),'Local research dataset unavailable')
class Feature3ConformityTests(unittest.TestCase):
    def test_input_boundary_adjacency_is_preserved_before_translation(self):
        data=load_shapefile();s=GeneralizationSession(data)
        expected=set()
        for i,row in data.frame.iterrows():
            for j,other in data.frame.iloc[:i].iterrows():
                if row.geometry.boundary.intersection(other.geometry.boundary).length>0:
                    expected.add(tuple(sorted((int(row['_index']),int(other['_index'])))))
        actual={tuple(sorted((int(a.D.V.i),int(b.D.V.i)))) for a,b in s.model.shared_walls}
        self.assertEqual(actual,expected)

    def test_numerical_tangency_and_real_intersection_are_distinguished(self):
        s=GeneralizationSession(load_shapefile())
        s.preview('smoothing',[7])
        state=s.state;core=_core()
        with self.assertRaises(TransitionShellError) as caught:s.preview('polyline',[8])
        report=caught.exception.report
        self.assertEqual(report['classification'],'self_intersection')
        self.assertTrue(report['face_points'])
        self.assertEqual(report['case'],'polygon_to_polyline')
        self.assertIs(s.state,state);self.assertIs(_core()[0],core[0])

    def test_all_six_requested_tolerances(self):
        s=GeneralizationSession(load_shapefile())
        original=s.state
        for tolerance in (.01,.1,.5,1.,2.,5.):
            with self.subTest(tolerance=tolerance):
                proposal=s.preview('simplification',[3],tolerance=tolerance)
                self.assertEqual(proposal.state.z,15.)
                self.assertIs(s.state,original)
                cap=next(f for f in proposal.state.faces if f.owner==3 and f.part=='top')
                self.assertEqual({p[2] for p in cap.points},{15.})
                actual=PlaneSlicer(proposal.model).sections(SlicePlane.horizontal(15))['3']
                self.assertLess(actual.symmetric_difference(proposal.targets[3]).area,1e-6)


if __name__=='__main__':unittest.main()
