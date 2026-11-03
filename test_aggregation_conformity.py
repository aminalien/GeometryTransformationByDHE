"""Regress shared skeleton time provenance and zero-width arrangement artifacts."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from shapely.geometry import LineString,Point,Polygon,box
from shapely.ops import unary_union

from aggregation_geometry import build_aggregation_preview
from aggregation_layer import _plan_shell,build_aggregation_model
from generalization_geometry import DEFAULT_DATA,load_shapefile
from generalization_model import GeneralizationSession,_core,_layer_faces
from plane_slicing import PlaneSlicer,SlicePlane
from shell_conformity import TransitionShellError
from top_face_overlay import cell_volume


class TimingAndColumnTests(unittest.TestCase):
    def test_nonzero_source_event_time_is_rejected(self):
        preview=build_aggregation_preview({'left':box(0,0,2,2),'right':box(6,0,8,2)})
        # An explicitly malformed arc gives a known source-boundary point a
        # positive event time. The planner must reject it, rather than pinning
        # that value silently to the source level.
        preview.skeleton_arcs.append(replace(preview.skeleton_arcs[0],
            geometry=LineString([(2,1),(4,1)]),times=(.1,2.)))
        with self.assertRaises(TransitionShellError) as caught:_plan_shell(preview,0,5,9,10)
        self.assertEqual(caught.exception.report['stage'],'source_vertex_time')

    def test_conflicting_arc_provenance_is_rejected(self):
        preview=build_aggregation_preview({'left':box(0,0,2,2),'right':box(6,0,8,2)})
        for arc in preview.skeleton_arcs:
            endpoints=[arc.geometry.coords[0],arc.geometry.coords[-1]]
            at=next((i for i,p in enumerate(endpoints) if preview.union.distance(Point(p))>1e-4),None)
            if at is not None:break
        times=list(arc.times);times[at]+=.1
        preview.skeleton_arcs.append(replace(arc,times=tuple(times)))
        saved=_core()
        with self.assertRaises(TransitionShellError) as caught:_plan_shell(preview,0,5,9,10)
        self.assertEqual(caught.exception.report['classification'],'skeleton_time_conflict')
        self.assertEqual(caught.exception.report['stage'],'growth_vertex_time')
        self.assertEqual(_core(),saved)

    def test_collapsed_ring_is_omitted_but_real_narrow_region_survives(self):
        domain={1:box(-1,-1,4,4)}
        collapsed=Polygon([(0,0),(1,1),(2,2+1e-15)])
        narrow=Polygon([(0,0),(1,1),(2,2+1e-8)])
        counts=[]
        for geometry in (collapsed,narrow):
            report={}
            with patch('generalization_model.polygonize',return_value=[geometry]):
                faces=_layer_faces(domain,domain,[],5,15,diagnostics=report)
            counts.append(len([f for f in faces if f.part!='top']))
            if geometry is collapsed:
                self.assertEqual(len(report['collapsed_arrangement_rings']),1)
            else:self.assertFalse(report)
        self.assertEqual(counts[0],0);self.assertGreater(counts[1],0)


@unittest.skipUnless(Path(DEFAULT_DATA).is_file(),'Local default Shapefile unavailable')
class ReportedAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=load_shapefile()

    def test_six_feature_angular_approximation_and_strict_rejection(self):
        selection=(17,5,7,10,27,30)
        for source_z in (5.,15.,25.):
            s=GeneralizationSession(self.data,initial_height=source_z)
            old=s.state;core=_core()
            source=unary_union([old.features[k].geometry for k in selection]);hull=source.convex_hull
            for dz in (1.,10.,20.):
                with self.subTest(zs=source_z,dz=dz):
                    p=s.preview('aggregation',selection,dz=dz)
                    self.assertIs(s.state,old);self.assertEqual(_core(),core)
                    timing=p.state.operation.diagnostics['skeleton_timing']
                    self.assertEqual(timing['backend_time_epsilon'],1e-5)
                    self.assertGreater(len(timing['explained_vertices']),0)
                    for vertex in timing['explained_vertices']:
                        residual=max(abs(t-vertex['time']) for t in vertex['edge_times'])
                        self.assertGreater(residual,vertex['backend_time_bound'])
                        self.assertLessEqual(residual,vertex['effective_time_bound'])
                        self.assertTrue(vertex['approximation_sources'])
                    for seed in timing['approximation_sources'].values():
                        from math import cos,radians
                        self.assertLessEqual(seed['angle_degrees'],seed['aligned_degrees'])
                        self.assertAlmostEqual(seed['delta'],seed['normal_travel']*(1-cos(radians(seed['angle_degrees']))),places=12)
                    slicer=PlaneSlicer(p.model);owner=str(p.state.operation.winner);last=source
                    for fraction in (0.,.25,.5,.75,.8,1.):
                        sections=slicer.sections(SlicePlane.horizontal(source_z+fraction*dz))
                        section=unary_union([g for k,g in sections.items() if int(k) in set(selection)|{int(owner)}])
                        self.assertLess(last.difference(section).area,1e-6)
                        self.assertLess(source.difference(section).area,1e-6)
                        self.assertLess(section.difference(hull).area,1e-6)
                        if fraction>=.8:self.assertLess(section.symmetric_difference(hull).area,1e-6)
                        last=section
                    self.assertTrue(all(cell_volume(c)>0 for c in p.model.cells))
        preview=build_aggregation_preview({str(k):old.features[k].geometry for k in selection},tolerance=1e-7)
        saved=tuple((a.geometry.wkb,a.times,a.provenance,a.fractions) for a in preview.skeleton_arcs)
        # No provenance allowance means the original strict rejection remains.
        with self.assertRaises(TransitionShellError):_plan_shell(replace(preview,timing_graphs=()),0,15,23,25)
        _plan_shell(preview,0,15,23,25)
        model=build_aggregation_model(preview,source_z=15,merge_z=23,top_z=25)
        slicer=PlaneSlicer(model)
        for z in (15,17,19,21,23,25):
            actual=unary_union(list(slicer.sections(SlicePlane.horizontal(z)).values()))
            self.assertLess(actual.symmetric_difference(model.aggregation.event_time_section(z)).area,1e-7)
        point=preview.timing_diagnostics['explained_vertices'][0]['point']
        from aggregation_layer import _arrival
        def corrupted(face,p):
            value=_arrival(face,p)
            return value+.01 if Point(point).distance(Point(p))<1e-9 else value
        with patch('aggregation_layer._arrival',side_effect=corrupted):
            with self.assertRaises(TransitionShellError) as caught:_plan_shell(preview,0,15,23,25)
        self.assertEqual(caught.exception.report['classification'],'skeleton_time_conflict')
        self.assertEqual(saved,tuple((a.geometry.wkb,a.times,a.provenance,a.fractions) for a in preview.skeleton_arcs))

    def test_reported_selections_levels_and_increments(self):
        for selection in ((17,27,30),(1,26,29,32)):
            for source_z in (5.,25.):
                s=GeneralizationSession(self.data,initial_height=source_z)
                old=s.state;saved=_core()
                source=unary_union([old.features[k].geometry for k in selection]);hull=source.convex_hull
                for increment in (1.,10.,20.):
                    with self.subTest(selection=selection,zs=source_z,dz=increment):
                        proposal=s.preview('aggregation',selection,dz=increment)
                        self.assertIs(s.state,old);self.assertEqual(_core(),saved)
                        owner=proposal.state.operation.winner;slicer=PlaneSlicer(proposal.model)
                        last=source
                        for fraction in (0.,.1,.25,.5,.75,.8,.9,1.):
                            z=source_z+fraction*increment
                            sections=slicer.sections(SlicePlane.horizontal(z))
                            geometry=unary_union([g for k,g in sections.items() if int(k) in set(selection)|{owner}])
                            self.assertLess(last.difference(geometry).area,1e-6)
                            self.assertLess(source.difference(geometry).area,1e-6)
                            self.assertLess(geometry.difference(hull).area,1e-6)
                            if fraction>=.8:self.assertLess(geometry.symmetric_difference(hull).area,1e-6)
                            last=geometry
                        for k,g in slicer.sections(SlicePlane.horizontal(2.5)).items():
                            expected=source if int(k)==owner else old.features[int(k)].geometry
                            self.assertLess(g.symmetric_difference(expected).area,1e-6)
                        self.assertTrue(all(cell_volume(cell)>0 for cell in proposal.model.cells))
                        timing=proposal.state.operation.diagnostics['skeleton_timing']
                        self.assertLessEqual(timing['maximum_edge_time_difference'],timing['backend_distance_epsilon']+1e-10)
                        if selection==(1,26,29,32):
                            self.assertTrue(proposal.state.operation.diagnostics['collapsed_arrangement_rings'])

    def test_edge_distance_reference_remains_independent_and_quantified(self):
        s=GeneralizationSession(self.data)
        preview=build_aggregation_preview({str(k):s.state.features[k].geometry for k in (17,27,30)},tolerance=1e-7)
        model=build_aggregation_model(preview,source_z=25,merge_z=33,top_z=35)
        slicer=PlaneSlicer(model)
        differences=[]
        for z in (25,27,29,31,33,35):
            reference=model.aggregation.horizontal_section(z)
            actual=unary_union(list(slicer.sections(SlicePlane.horizontal(z)).values()))
            self.assertLess(actual.symmetric_difference(model.aggregation.event_time_section(z)).area,1e-7)
            differences.append(actual.symmetric_difference(reference).area)
        self.assertGreater(max(differences),0.)
        # The reference uses source-edge planes; the constructed surface uses
        # rounded backend event times. Bound their spatial difference by the
        # backend's documented distance precision, without weakening shell checks.
        self.assertLess(max(differences),preview.hull.length*preview.timing_diagnostics['backend_distance_epsilon']*4)


if __name__=='__main__':unittest.main()
