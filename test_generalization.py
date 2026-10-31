"""Geometry, lifecycle, ownership and actual DHE regression tests."""
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
from shapely.geometry import Polygon, box, Point
from shapely.ops import unary_union

import DHECore as dhe
from generalization_geometry import (DEFAULT_DATA, prepare_frame, load_shapefile,
    visible_point, skeleton_ridge, polygon_target, parse_osm, download_osm)
from generalization_model import GeneralizationSession, _polygon_patches, _layer_faces, realize, Face
from generalization_session import parse_indices, render_overview
from plane_slicing import PlaneSlicer, SlicePlane


def session(polygons=None):
    polygons=polygons or [box(0,0,10,10),box(10,0,20,10),box(30,0,40,10),box(50,0,60,10)]
    frame=gpd.GeoDataFrame({"osm_id":[f"osm-{i}" for i in range(len(polygons))]},geometry=polygons,crs=2180)
    return GeneralizationSession(prepare_frame(frame))


class GeometryTests(unittest.TestCase):
    def test_visible_kernel_and_empty_kernel(self):
        polygon=Polygon([(0,0),(6,0),(6,2),(2,2),(2,6),(0,6)])
        point=visible_point(polygon)
        from shapely.geometry import LineString
        self.assertTrue(all(polygon.covers(LineString([p,point.coords[0]])) for p in polygon.exterior.coords[:-1]))
        with self.assertRaisesRegex(ValueError,"kernel"):
            visible_point(Polygon([(0,0),(6,0),(6,6),(4,6),(4,2),(2,2),(2,6),(0,6)]))

    def test_skeleton_target_is_valid_single_ridge(self):
        line,method=skeleton_ridge(box(0,0,10,5))
        self.assertEqual(line.geom_type,"LineString")
        self.assertTrue(box(0,0,10,5).contains(line))
        self.assertTrue(line.is_simple)
        self.assertIn(method,("straight-skeleton path","visibility-kernel principal-axis fallback"))

    def test_parameters_and_input_diagnostics(self):
        with self.assertRaises(ValueError):polygon_target(box(0,0,1,1),"simplification",tolerance=-1)
        frame=gpd.GeoDataFrame(geometry=[box(0,0,2,2),Point(3,3)],crs=2180)
        loaded=prepare_frame(frame)
        self.assertEqual(len(loaded.frame),1)
        self.assertIn("unsupported Point",loaded.rejected[0])
        with self.assertRaisesRegex(ValueError,"overlap"):
            prepare_frame(gpd.GeoDataFrame(geometry=[box(0,0,2,2),box(1,1,3,3)],crs=2180))

    def test_original_positions_survive_rejected_records(self):
        loaded=prepare_frame(gpd.GeoDataFrame(geometry=[Point(0,0),box(1,1,2,2)],crs=2180))
        self.assertEqual(list(loaded.frame['_index']),[2])

    def test_indices(self):
        self.assertEqual(parse_indices("1, 3-5; 9"),{1,3,4,5,9})
        with self.assertRaises(ValueError):parse_indices("0")

    def test_osm_complete_way_and_relation(self):
        elements=[dict(type="node",id=i,lon=x,lat=y) for i,(x,y) in enumerate([(0,0),(1,0),(1,1),(0,1)],1)]
        elements += [dict(type="way",id=10,nodes=[1,2,3,4,1],tags={"building":"yes"})]
        frame,errors=parse_osm({"elements":elements})
        self.assertEqual(len(frame),1);self.assertEqual(errors,[])
        elements += [dict(type="relation",id=20,tags={"building":"yes","type":"multipolygon"},members=[dict(type="way",ref=10,role="outer")])]
        frame,errors=parse_osm({"elements":elements})
        self.assertEqual(list(frame.osm_id),["relation/20"])

    def test_osm_transport_offline_and_failure_messages(self):
        import io,json,urllib.parse,urllib.error
        elements=[dict(type="node",id=i,lon=x,lat=y) for i,(x,y) in enumerate([(0,0),(.001,0),(.001,.001),(0,.001)],1)]
        elements.append(dict(type="way",id=10,nodes=[1,2,3,4,1],tags={"building":"yes"}))
        response=io.BytesIO(json.dumps({"elements":elements}).encode())
        with patch("urllib.request.urlopen",return_value=response) as request:
            loaded=download_osm(-.001,-.002,.002,.003)
        self.assertEqual(len(loaded.frame),1)
        query=urllib.parse.parse_qs(request.call_args.args[0].data.decode())["data"][0]
        self.assertIn("(-0.002,-0.001,0.003,0.002)",query)
        self.assertIn("out body;>;out skel",query)
        with patch("urllib.request.urlopen",side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(ValueError,"download failed"):download_osm(0,0,1,1)
        with self.assertRaisesRegex(ValueError,"Bounding box"):download_osm(1,0,0,1)


class StackTests(unittest.TestCase):
    def test_initial_dhe_shared_interface(self):
        s=session()
        from main import validate_model
        self.assertEqual(len(validate_model(s.model)),4)
        self.assertEqual(len(s.model.shared_walls),1)

    def test_point_and_line_actual_final_contacts(self):
        for operation in ("point","polyline"):
            with self.subTest(operation=operation):
                s=session();proposal=s.preview(operation,[3]);s.apply(proposal)
                result=PlaneSlicer(s.model).sections(SlicePlane.horizontal(15),include_degenerate=True)
                self.assertLess(result['3'].hausdorff_distance(s.state.features[3].geometry),1e-6)
                self.assertFalse(any(f.owner==3 and f.part=='top' for f in s.state.faces))

    def test_batch_same_height_and_neighbour_update(self):
        s=session();p=s.preview("point",[1,3]);s.apply(p)
        self.assertEqual(s.state.z,15)
        self.assertEqual(s.state.features[1].geometry.geom_type,"Point")
        self.assertGreater(s.state.features[2].geometry.area,100)
        self.assertEqual(s.state.features[4].geometry.area,100)

    def test_repeated_history_preserves_lower_sections(self):
        s=session()
        before=PlaneSlicer(s.model).sections(SlicePlane.horizontal(2.5))
        for operation,selection in [("smoothing",[3,4]),("simplification",[3]),("point",[4]),("smoothing",[3])]:
            s.apply(s.preview(operation,selection))
        self.assertEqual(s.state.z,45)
        after=PlaneSlicer(s.model).sections(SlicePlane.horizontal(2.5))
        for k,g in before.items():self.assertLess(g.symmetric_difference(after[k]).area,1e-6)
        self.assertFalse(any(len({p[2] for p in f.points})==1 and f.points[0][2] in (5,15,25,35) for f in s.state.faces))
        s.undo();self.assertEqual(s.state.z,35)
        s.apply(s.preview("simplification",[3],dz=2.5))
        self.assertEqual(s.state.z,37.5);self.assertEqual(len(s.history),5)

    def test_merge_winner_and_lineage(self):
        s=session();s.apply(s.preview("merging",[1,2],winner=2))
        self.assertNotIn(1,s.state.features)
        self.assertEqual(s.state.features[2].attributes['osm_id'],'osm-1')
        self.assertEqual(s.state.features[2].lineage,(1,2))
        self.assertAlmostEqual(s.state.features[2].geometry.area,200)
        # The losing cell remains in the historical solid, below the inclined
        # interface; it is retired only from the active top map.
        self.assertEqual(len(s.model.cells),4)
        s.apply(s.preview("smoothing",[2]));self.assertEqual(s.state.z,25)

    def test_aggregation_is_actual_hull_and_can_be_generalized(self):
        s=session([box(0,0,5,5),box(8,0,13,5),box(30,0,35,5)])
        expected=unary_union([s.state.features[k].geometry for k in (1,2)]).convex_hull
        s.apply(s.preview("aggregation",[1,2]))
        aggregate=max(s.state.features)
        self.assertLess(expected.symmetric_difference(s.state.features[aggregate].geometry).area,1e-6)
        self.assertEqual(s.state.features[aggregate].lineage,(1,2))
        s.apply(s.preview("smoothing",[aggregate]))
        self.assertEqual(s.state.z,25)

    def test_rejections_and_preview_do_not_mutate_session_or_core(self):
        s=session();original=s.state;core=dhe.dhe
        preview=s.preview("smoothing",[3])
        self.assertIs(s.state,original);self.assertIs(dhe.dhe,core)
        with self.assertRaisesRegex(ValueError,"edge-connected"):
            s.preview("merging",[1,3],winner=1)
        self.assertIs(s.state,original);self.assertIs(dhe.dhe,core)
        s.apply(preview)
        with self.assertRaisesRegex(ValueError,"stale"):s.apply(preview)
        s.apply(s.preview("point",[4]))
        with self.assertRaisesRegex(ValueError,"terminal"):s.preview("smoothing",[4])

    def test_allocation_failure_restores_live_topology_and_counters(self):
        s=session();state=s.state
        before=(dhe.dhe,dhe.killdhe,dhe.Vertex.cntId,dhe.DualHalfEdge.cntId)
        with patch.object(dhe,"MEF",side_effect=RuntimeError("allocation probe")):
            with self.assertRaisesRegex(RuntimeError,"allocation probe"):s.preview("smoothing",[3])
        self.assertIs(s.state,state)
        self.assertIs(dhe.dhe,before[0]);self.assertIs(dhe.killdhe,before[1])
        self.assertEqual((dhe.Vertex.cntId,dhe.DualHalfEdge.cntId),before[2:])

    def test_real_operators_are_called_in_order(self):
        events=[]
        names=("MEVVFS","MEV","MEF","Sew","JoinByFace")
        original={name:getattr(dhe,name) for name in names}
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in names:
                def record(*a,_name=name,**kw):
                    events.append(_name);return original[_name](*a,**kw)
                stack.enter_context(patch.object(dhe,name,record))
            session()
        self.assertEqual(events[0],"MEVVFS")
        self.assertLess(events.index("MEV"),events.index("MEF"))
        self.assertLess(events.index("MEF"),events.index("Sew"))
        self.assertLess(events.index("Sew"),events.index("JoinByFace"))

    def test_nested_target_without_temporary_dhe_triangulation(self):
        s=session([box(0,0,10,10)])
        source=s.state.features[1].geometry;target=box(2,2,8,8)
        patches,count=_polygon_patches(1,source,target,5,15)
        self.assertGreater(count,0)
        faces=[f for f in s.state.faces if f.part!='top']+_layer_faces({1:source},{1:target},patches,5,15)
        model,_,_=realize(faces,s.origin)
        actual=PlaneSlicer(model).sections(SlicePlane.horizontal(15))['1']
        self.assertLess(actual.symmetric_difference(target).area,1e-6)

    def test_headless_figure_contains_actual_geometry(self):
        s=session();s.apply(s.preview("point",[3]))
        figure=render_overview(s)
        self.assertEqual(len(figure.axes),2)
        figure.canvas.draw()


@unittest.skipUnless(Path(DEFAULT_DATA).is_file(),"Local research Shapefile unavailable")
class DefaultDatasetTests(unittest.TestCase):
    def test_real_cases_and_repeated_stack(self):
        for operation,selected,kwargs in [("smoothing",[1],{}),("polyline",[1,4],{}),("point",[2],{}),("merging",[2,5],{"winner":2}),("aggregation",[1,4],{})]:
            with self.subTest(operation=operation):
                s=GeneralizationSession(load_shapefile());s.apply(s.preview(operation,selected,**kwargs))
                self.assertEqual(s.state.z,15)
        s=GeneralizationSession(load_shapefile())
        for operation,selected in [("smoothing",[1]),("simplification",[1]),("point",[4]),("smoothing",[1])]:
            s.apply(s.preview(operation,selected))
        self.assertEqual(s.state.z,45)


if __name__=="__main__":unittest.main()
