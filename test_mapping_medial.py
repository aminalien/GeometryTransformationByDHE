"""Skeleton-first aggregation, medial recovery and transactional publication."""
import json
import unittest
from threading import Event
from unittest.mock import patch

import numpy as np
from shapely.geometry import box, Polygon
from shapely.affinity import rotate, translate
from shapely.ops import unary_union

from generalization_model import _core, Face
from mapping_builders import build_mapping, MappingBuildError, realize, smooth_faces, _partition_plane
from medial_aggregation import MedialSite, _radius_error
from plane_slicing import SlicePlane
from shell_conformity import TransitionShellError,check_face_intersections
from test_mapping import build, group, layers


SOURCES = [box(0,0,2,2), box(4,0,6,2)]
TARGET = box(0,0,6,2)


class MedialMappingTests(unittest.TestCase):
    def test_skeleton_is_first_and_continuous_aggregation_is_prohibited(self):
        with patch('mapping_builders.continuous_polygon_faces',side_effect=AssertionError('Prohibited many-to-one builder')):
            result,scene = build(SOURCES,[TARGET],mode='smooth')
        construction = result.report['routing'][0]['construction']
        self.assertEqual(construction['method'],'straight_skeleton')
        self.assertEqual(construction['stages'],['skeleton_to_hull','hull_to_supplied_target'])
        self.assertEqual(construction['stage_z'],10.)
        self.assertEqual(result.report['endpoint_validation'],'passed')

    def test_skeleton_failure_recovers_with_medial_provenance_and_monotone_growth(self):
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=ValueError('Antiparallel source segments')), \
             patch('mapping_builders.continuous_polygon_faces',side_effect=AssertionError('Prohibited many-to-one builder')):
            result,scene = build(SOURCES,[TARGET],mode='smooth')
        construction = result.report['routing'][0]['construction']
        self.assertEqual(construction['method'],'medial_axis')
        self.assertEqual(construction['stages'],['medial_axis_to_hull','hull_to_supplied_target'])
        self.assertEqual(construction['attempts'][0]['message'],'Antiparallel source segments')
        self.assertEqual(construction['attempts'][-1],dict(method='medial_axis',status='passed'))
        self.assertLessEqual(construction['quantization_error'],.01/100)
        self.assertLessEqual(construction['curve_error_bound'],.01)
        self.assertLessEqual(construction['radius_interpolation_error_bound'],.01)
        self.assertTrue(construction['sites']);self.assertTrue(construction['medial_edges'])
        json.dumps(result.report,allow_nan=False)
        previous = unary_union(SOURCES)
        for z in (5.5,6.,7.,8.,9.,10.):
            section = unary_union(list(scene.slicer.sections(SlicePlane.horizontal(z)).values()))
            self.assertLess(previous.difference(section).area,1e-8)
            self.assertTrue(section.is_valid);previous=section
        self.assertLess(previous.symmetric_difference(TARGET).area,1e-8)

    def test_timing_failure_uses_independent_medial_radii(self):
        failure = TransitionShellError('Bad skeleton timing','skeleton_time_conflict',owners=['0','1'])
        with patch('aggregation_layer._plan_upper_shell',side_effect=failure):
            result,_=build(SOURCES,[TARGET],mode='smooth')
        construction=result.report['routing'][0]['construction']
        self.assertEqual(construction['method'],'medial_axis')
        self.assertEqual(construction['attempts'][0]['diagnostic']['classification'],'skeleton_time_conflict')

    def test_combined_validation_retries_only_implicated_group(self):
        gs=[group(['s1','s2'],['t1']),group(['s3'],['t2'],id='stable')]
        count=0
        def fail_first(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1:raise TransitionShellError('Bad skeleton shell','nonmanifold_edge_incidence',cell=3)
            return realize(*args,**kwargs)
        with patch('mapping_builders.realize',side_effect=fail_first), \
             patch('mapping_builders.smooth_faces',wraps=smooth_faces) as planner:
            result,_=build([*SOURCES,box(20,0,22,2)],[TARGET,box(20,0,22,2)],mode='smooth',groups=gs)
        self.assertEqual(count,2);self.assertEqual(planner.call_count,3)
        self.assertEqual([r['construction']['method'] for r in result.report['routing'] if r['group']=='g1'],['medial_axis'])
        self.assertEqual(result.report['retry_history'][0]['groups'],['g1'])
        self.assertEqual(result.report['retry_history'][0]['methods'],{'g1':'medial_axis'})

    def test_both_backends_fail_with_all_causes_and_without_classic(self):
        source,target=layers(SOURCES,[TARGET]);core=_core()
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=ValueError('Skeleton failed')), \
             patch('medial_aggregation.medial_aggregation_faces',side_effect=ValueError('Medial failed')):
            with self.assertRaises(MappingBuildError) as caught:
                build_mapping(source,target,[group(['s1','s2'],['t1'])],dict(mode='smooth'))
        failure=caught.exception.report['failures'][0]
        self.assertEqual([a['message'] for a in failure['attempts']],['Skeleton failed','Medial failed'])
        self.assertEqual(caught.exception.report['retry_history'],[])
        self.assertEqual(_core(),core);json.dumps(caught.exception.report,allow_nan=False)

    def test_coupled_failure_can_retry_a_skeleton_group_beside_a_medial_group(self):
        from aggregation_geometry import build_aggregation_preview
        calls=0
        def select_skeleton(sources,**kwargs):
            if min(p.bounds[0] for p in sources.values())<10:raise ValueError('First group needs medial recovery')
            return build_aggregation_preview(sources,**kwargs)
        def combined(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:raise TransitionShellError('Coupled conflict','self_intersection',owners=[3,6])
            return realize(*args,**kwargs)
        gs=[group(['s1','s2'],['t1']),group(['s3','s4'],['t2'],id='g2')]
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=select_skeleton), \
             patch('mapping_builders.realize',side_effect=combined):
            result,_=build([*SOURCES,translate(SOURCES[0],xoff=20),translate(SOURCES[1],xoff=20)],
                           [TARGET,translate(TARGET,xoff=20)],mode='smooth',groups=gs)
        self.assertEqual(calls,2)
        self.assertEqual([r['construction']['method'] for r in result.report['routing']],['medial_axis','medial_axis'])
        self.assertEqual(result.report['retry_history'][0]['methods'],{'g2':'medial_axis'})

    def test_resource_failure_and_cancellation_do_not_start_another_backend(self):
        source,target=layers(SOURCES,[TARGET]);gs=[group(['s1','s2'],['t1'])];core=_core()
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=MemoryError('Allocation failed')), \
             patch('medial_aggregation.medial_aggregation_faces') as medial:
            with self.assertRaises(MemoryError):build_mapping(source,target,gs,dict(mode='smooth'))
            medial.assert_not_called()
        stop=Event()
        def cancelled_skeleton(*args,**kwargs):
            stop.set();raise ValueError('Interrupted skeleton')
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=cancelled_skeleton), \
             patch('medial_aggregation.medial_aggregation_faces') as medial:
            with self.assertRaisesRegex(ValueError,'cancelled'):
                build_mapping(source,target,gs,dict(mode='smooth'),cancelled=stop.is_set)
            medial.assert_not_called()
        self.assertEqual(_core(),core)

    def test_medial_handles_touching_holes_and_nontrivial_curves(self):
        donut=Polygon(box(0,0,6,6).exterior.coords,[box(2,2,4,4).exterior.coords])
        cases=[([box(0,0,2,2),box(2,2,4,4)],box(0,0,4,4)),
               ([donut,box(8,0,10,6)],box(0,0,10,6)),
               ([box(0,0,2,2),box(4,1,6,3)],box(0,0,6,3))]
        for sources,target in cases:
            with self.subTest(sources=sources),patch('aggregation_geometry.build_aggregation_preview',side_effect=ValueError('Forced')):
                result,scene=build(sources,[target],mode='smooth')
                self.assertEqual(result.report['endpoint_validation'],'passed')
                self.assertEqual(result.report['routing'][0]['construction']['method'],'medial_axis')
                for z in (6.,8.,10.,12.,14.):
                    geometries=list(scene.slicer.sections(SlicePlane.horizontal(z)).values())
                    self.assertTrue(all(g.is_valid for g in geometries))
                    self.assertLess(sum(g.area for g in geometries)-unary_union(geometries).area,1e-8)

    def test_radius_error_bound_covers_independent_cone_samples(self):
        site=MedialSite(0,1,((0.,0.),),('source',))
        for triangle in (((0.,0.),(3.,0.),(0.,4.)),((1.,1.),(3.,1.),(2.,5.)),((-2.,-1.),(3.,-1.),(0.,3.))):
            h=[site.radius(p) for p in triangle];bound=_radius_error(triangle,h,site)
            for a in np.linspace(0,1,21):
                for b in np.linspace(0,1-a,21):
                    weights=np.array([a,b,1-a-b]);point=weights@np.asarray(triangle)
                    self.assertLessEqual(abs(weights@h-site.radius(point)),bound+1e-12)

    def test_quantized_sites_reconnect_to_exact_original_source_endpoints(self):
        sources=[rotate(box(0,0,2,2),13,origin=(0,0)),translate(rotate(box(0,0,2,2),-7,origin=(0,0)),xoff=5.3,yoff=.417)]
        target=unary_union(sources).envelope;original=[p.wkb for p in sources]
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=ValueError('Forced')):
            result,_=build(sources,[target],mode='smooth')
        construction=result.report['routing'][0]['construction']
        self.assertEqual(result.report['endpoint_validation'],'passed')
        self.assertGreater(construction['quantization_error'],0.)
        self.assertLessEqual(construction['radius_interpolation_error_bound'],.01)
        self.assertEqual(original,[p.wkb for p in sources])

    def test_intersection_acceleration_keeps_crossings_and_coplanar_folds_visible(self):
        base=Face(1,[(0,0,0),(2,0,0),(0,2,0)])
        crossing=Face(1,[(.5,.5,-1),(.5,.5,1),(1.5,.5,0)])
        far=[Face(1,[(10+3*i,0,0),(11+3*i,0,0),(10+3*i,1,0)]) for i in range(1001)]
        for exhaustive in (False,True):
            with self.subTest(exhaustive=exhaustive):
                def factory(shapes):
                    from types import SimpleNamespace
                    return SimpleNamespace(query=lambda region:np.arange(len(shapes)))
                if exhaustive:
                    with patch('shapely.STRtree',side_effect=factory),self.assertRaises(TransitionShellError) as caught:
                        check_face_intersections([base,crossing,*far])
                else:
                    with self.assertRaises(TransitionShellError) as caught:check_face_intersections([base,crossing,*far])
                self.assertEqual(caught.exception.report['classification'],'self_intersection')
                self.assertEqual(caught.exception.report['faces'],(0,1))
        check_face_intersections([base,Face(1,[(0,0,0),(2,0,0),(0,-2,0)])])
        check_face_intersections([base,Face(1,[(0,0,0),(2,0,0),(0,2,1)])])
        with self.assertRaises(TransitionShellError):
            check_face_intersections([base,Face(1,[(0,0,0),(2,0,0),(.5,.5,0)])])
        for points in ([(0,0,0),(1,.1,0),(.1,1,0)],[(0,0,0),(2,1,-1),(1,2,1)]):
            with self.assertRaises(TransitionShellError):check_face_intersections([base,Face(1,points)])
        check_face_intersections([base,Face(1,[(0,0,0),(-1,0,1),(-1,-1,1)])])

    def test_dense_triangle_fan_has_only_declared_contacts(self):
        from math import cos,sin,pi
        ring=[(cos(2*pi*i/80),sin(2*pi*i/80),6.+.1*cos(4*pi*i/80)) for i in range(80)]
        faces=[Face(1,[(0.,0.,5.),a,b]) for a,b in zip(ring,ring[1:]+ring[:1])]
        check_face_intersections(faces)

    def test_oblique_wall_overlay_restores_exact_input_vertices(self):
        a=(1463.7182421111115,284.377898744852,6.117591303780201)
        b=(1464.1209116507575,284.3395184863774,6.073615862466576)
        c=(1584.642016965663,272.8521058112383,8.)
        walls=[Face(1,[c,b,(*b[:2],10.),(*c[:2],10.)]),
               Face(1,[b,a,(*a[:2],10.),(*b[:2],10.)])]
        vertices={p for face in _partition_plane(walls) for p in face.points}
        self.assertTrue({a,b,c,(*a[:2],10.),(*b[:2],10.),(*c[:2],10.)}<=vertices)

    def test_display_preserves_existing_triangles_without_native_retriangulation(self):
        from web_generalization_scene import display_triangles
        triangle=[(1460.,280.,5.),(1461.,280.,5.1),(1460.5,280.0000000001,5.04)]
        with patch('web_generalization_scene.constrained_delaunay_triangles',side_effect=AssertionError('Already a triangle')):
            self.assertEqual(display_triangles(triangle),[triangle])
        with self.assertRaises(ValueError):display_triangles([(0,0,0),(1,0,0),(2,0,0)])


if __name__=='__main__':unittest.main()
