"""Continuous Smooth unions, hole-preserving volumes and complete-file mapping."""
import json
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import box,Polygon,MultiPolygon
from shapely.ops import unary_union

from generalization_model import _core
from mapping_builders import build_mapping,MappingBuildError
from mapping_data import prepare_layer,union_overlaps,reproject_layer,suggest_groups,automatic_review
from mapping_scene import MappingScene
from plane_slicing import SlicePlane
from test_mapping import layers,group,build


def donut():return Polygon(box(0,0,6,6).exterior.coords,[box(2,2,4,4).exterior.coords])


class ContinuousMappingTests(unittest.TestCase):
    def test_separated_sources_change_continuously_without_a_skeleton(self):
        ss=[box(0,0,2,2),box(4,0,6,2)];target=box(0,0,6,2)
        s,t=layers(ss,[target]);saved=[f.geometry.wkb for l in (s,t) for f in l.features.values()];core=_core()
        gs=[group(['s1','s2'],['t1'])]
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=AssertionError('Skeleton must not run')):
            r=build_mapping(s,t,gs,dict(mode='smooth'))
        scene=MappingScene(r,1);scene.validate_endpoints(s,t,gs)
        self.assertEqual(_core(),core);self.assertEqual(saved,[f.geometry.wkb for l in (s,t) for f in l.features.values()])
        self.assertEqual(len(r.model.cells),3);self.assertEqual(r.report['construction_attempts'],1);self.assertEqual(r.report['retry_history'],[])
        row=r.report['routing'][0];self.assertEqual(row['builder'],'smooth');self.assertNotIn('fallback',row)
        self.assertEqual(row['construction']['stages'],['source_union_to_supplied_target'])
        last=unary_union(ss)
        for z in (7.5,10.,12.5):
            sections=scene.slicer.sections(SlicePlane.horizontal(z));actual=unary_union(list(sections.values()))
            self.assertGreater(actual.area,8.);self.assertLess(actual.area,12.)
            self.assertLess(last.difference(actual).area,1e-8);last=actual
        self.assertEqual({m['feature_id'] for m in r.metadata.values()},{'s1','s2','t1'})

    def test_touching_multipart_and_concave_target(self):
        cases=[([box(0,0,2,2),box(2,0,4,2)],box(0,0,4,2)),
               ([box(0,0,2,2),box(2,2,4,4)],box(0,0,4,4)),
               ([MultiPolygon([box(0,0,2,2),box(4,0,6,2)])],box(0,0,6,2)),
               ([box(0,0,2,2),box(4,0,6,2)],Polygon([(0,0),(6,0),(6,3),(4,3),(4,2),(0,2)]))]
        for sources,target in cases:
            with self.subTest(sources=len(sources),target=target.wkt):
                r,_=build(sources,[target],mode='smooth')
                self.assertEqual(r.report['endpoint_validation'],'passed')
                self.assertEqual(r.report['routing'][0]['construction']['method'],'continuous_boundary_distance')

    def test_source_target_and_persistent_holes_have_exact_endpoints(self):
        for source,target in ((donut(),box(0,0,6,6)),(box(0,0,6,6),donut()),(donut(),donut()),
                              (donut(),translate(donut(),xoff=.5,yoff=.25))):
            with self.subTest(source=source.wkt,target=target.wkt):
                r,scene=build([source],[target],mode='smooth')
                self.assertEqual(r.report['logical_features'],dict(source=1,target=1))
                self.assertEqual(r.report['endpoint_validation'],'passed')
                row=r.report['routing'][0];self.assertEqual(row['builder'],'smooth')
                self.assertEqual(row['construction']['target_holes'],len(target.interiors))
                if target.interiors:
                    self.assertGreater(row['construction']['upper_cells'],1)
                    self.assertEqual(row['construction']['hole_decomposition'],'conforming_triangular_transition_columns')
                for z,expected in ((5.,source),(15.,target)):
                    actual=unary_union(list(scene.slicer.sections(SlicePlane.horizontal(z),horizontal_side='below').values()))
                    self.assertLess(actual.symmetric_difference(expected).area,1e-8)
                for z in (7.5,10.,12.5):
                    sections=list(scene.slicer.sections(SlicePlane.horizontal(z)).values())
                    self.assertTrue(all(g.is_valid for g in sections))
                    self.assertLess(sum(g.area for g in sections)-unary_union(sections).area,1e-8)
                self.assertEqual(set(m['feature_id'] for m in r.metadata.values() if m['layer']=='target'),{'t1'})
                json.dumps(r.report,default=str,allow_nan=False)

    def test_neighbouring_groups_share_target_boundary_without_crossing(self):
        sources=[box(0,0,2,2),box(3,0,4,2),box(6,0,7,2),box(8,0,10,2)]
        targets=[box(0,0,5,2),box(5,0,10,2)]
        r,scene=build(sources,targets,mode='smooth',groups=[group(['s1','s2'],['t1']),group(['s3','s4'],['t2'],id='g2')])
        self.assertTrue(all(row['builder']=='smooth' for row in r.report['routing']))
        for z in (6.,8.,10.,12.,14.,15.):
            sections=list(scene.slicer.sections(SlicePlane.horizontal(z)).values())
            self.assertLess(sum(g.area for g in sections)-unary_union(sections).area,1e-8)

    def test_anchor_failure_and_cancel_preserve_core(self):
        s,t=layers([box(0,0,2,2),box(4,0,6,2)],[box(0,0,6,2)]);gs=[group(['s1','s2'],['t1'])];core=_core()
        gs[0]['anchor']=1
        with self.assertRaisesRegex(MappingBuildError,'clear the anchor'):build_mapping(s,t,gs,dict(mode='smooth'))
        self.assertEqual(_core(),core);gs[0]['anchor']=None;stop=Event()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            build_mapping(s,t,gs,dict(mode='smooth'),cancelled=stop.is_set,
                          progress=lambda text:stop.set() if text.startswith('Conforming common interfaces') else None)
        self.assertEqual(_core(),core)

    def test_one_to_one_anchor_and_hybrid_construction_are_preserved(self):
        r,_=build([box(0,0,4,2)],[box(1,0,5,2)],mode='smooth',groups=[{**group(['s1'],['t1']),'anchor':0}])
        self.assertEqual(r.report['routing'][0]['construction']['anchor'],0)
        r,_=build([box(0,0,2,2),box(4,0,6,2)],[box(-1,-1,7,3)],mode='hybrid')
        self.assertEqual(r.report['routing'][0]['construction']['stages'],['skeleton_to_hull','hull_to_supplied_target'])


class ReportedContinuousMappingTests(unittest.TestCase):
    @unittest.skipUnless(all((Path('C:/University/PhD/Data/Kleczkow Updated')/p).is_file()
                             for p in ('osm_dataSimplified3.shp','Regions.shp')),'Local reported datasets unavailable')
    def test_complete_supplied_files_are_smooth_without_exclusions_or_classic(self):
        root=Path('C:/University/PhD/Data/Kleczkow Updated')
        s=union_overlaps(prepare_layer(gpd.read_file(root/'osm_dataSimplified3.shp'),'source','source'))
        t=union_overlaps(reproject_layer(prepare_layer(gpd.read_file(root/'Regions.shp'),'target','target'),s.crs))
        gs,_=suggest_groups(s,t,overlap_fraction=.25,search_distance=10.);gs=automatic_review(gs,s,t)
        self.assertEqual((len(s.features),len(t.features),len(gs)),(563,34,34));self.assertTrue(all(g['decision']=='map' for g in gs))
        saved=[f.geometry.wkb for layer in (s,t) for f in layer.features.values()];core=_core()
        with patch('aggregation_geometry.build_aggregation_preview',side_effect=AssertionError('Skeleton must not run')):
            r=build_mapping(s,t,gs,dict(mode='smooth',source_z=5.,target_z=15.,geometry_tolerance=.01))
        scene=MappingScene(r,1);scene.validate_endpoints(s,t,gs)
        self.assertEqual(_core(),core);self.assertEqual(saved,[f.geometry.wkb for layer in (s,t) for f in layer.features.values()])
        self.assertEqual(len(r.model.cells),617);self.assertTrue(all(row['builder']=='smooth' and 'fallback' not in row for row in r.report['routing']))
        self.assertEqual(r.report['logical_features'],dict(source=563,target=34));self.assertEqual(r.report['retry_history'],[])
        self.assertEqual(r.report['endpoint_validation'],'passed')
        for z in (7.5,10.,12.5):
            sections=list(scene.slicer.sections(SlicePlane.horizontal(z)).values())
            self.assertTrue(all(g.is_valid for g in sections))
            self.assertLess(sum(g.area for g in sections)-unary_union(sections).area,1e-5)


if __name__=='__main__':unittest.main()
