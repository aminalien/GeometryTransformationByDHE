"""Measured XY comparisons must reflect model snapshots, never display coordinates."""
import unittest

from shapely.geometry import Polygon,MultiPolygon,Point,LineString,box
from web_measurements import geometry_summary,comparison,layer_comparison
from generalization_model import GeneralizationSession
from web_generalization_scene import WebScene
from web_generalization_data import prepare_web_frame
from test_web_generalization import frame
from test_mapping import layers
from mapping_workspace import MappingWorkspace


class MeasurementTests(unittest.TestCase):
    def test_holes_multipart_and_symbols_have_explicit_dimensions(self):
        holed=Polygon(box(0,0,10,10).exterior.coords,[box(2,2,4,4).exterior.coords])
        result=geometry_summary([holed,MultiPolygon([box(20,0,22,2),box(25,0,27,2)]),Point(0,0),LineString([(0,0),(3,4)])])
        self.assertEqual(result['features'],4)
        self.assertEqual(result['polygon_area'],104)
        self.assertEqual(result['polygon_perimeter'],64)
        self.assertEqual(result['vertices'],19)
        self.assertEqual(result['geometry_types']['MultiPolygon'],1)

    def test_zero_baseline_percentage_is_unavailable(self):
        source=geometry_summary([Point(0,0)])
        target=geometry_summary([box(0,0,2,2)])
        result=comparison(source,target,scope='test',before_label='Source',after_label='Target')
        self.assertIsNone(result['changes']['polygon_area']['percent'])
        self.assertEqual(result['changes']['polygon_area']['absolute'],4)
        self.assertEqual(result['units']['polygon_perimeter'],'m')

    def test_preview_apply_history_and_collapse_baselines(self):
        session=GeneralizationSession(prepare_web_frame(frame()))
        proposal=session.preview('point',[1])
        preview=WebScene(session,1,proposal=proposal).payload['measurements']
        self.assertEqual(preview['before']['polygon_area'],150)
        self.assertEqual(preview['after']['polygon_area'],100)
        self.assertEqual(preview['changes']['vertices']['absolute'],-3)
        self.assertEqual(len(session.history),1)
        session.apply(proposal)
        applied=WebScene(session,2).payload['measurements']
        self.assertEqual(applied['before'],preview['before'])
        self.assertEqual(applied['after'],preview['after'])
        self.assertEqual(applied['after']['geometry_types'],{'Point':1,'Polygon':2})
        session.cursor=0
        initial=WebScene(session,3).payload['measurements']
        self.assertEqual(initial['before'],initial['after'])

    def test_merged_comparison_counts_current_features_not_retained_cells(self):
        session=GeneralizationSession(prepare_web_frame(frame([box(0,0,5,5),box(5,0,10,5)])))
        session.apply(session.preview('merging',[1,2],winner=1))
        result=WebScene(session,2).payload['measurements']
        self.assertEqual(result['before']['features'],2)
        self.assertEqual(result['after']['features'],1)
        self.assertEqual(result['before']['polygon_area'],result['after']['polygon_area'])
        self.assertLess(result['after']['polygon_perimeter'],result['before']['polygon_perimeter'])

    def test_mapping_snapshot_measurements_do_not_follow_later_input_changes(self):
        source,target=layers([box(0,0,2,2)],[box(0,0,3,3)])
        workspace=MappingWorkspace();workspace.layers=dict(source=source,target=target)
        workspace.input_layers=workspace.layers.copy()
        try:
            workspace.execute('build',0)
            snapshot=workspace.result.payload['measurements']
            self.assertEqual(snapshot['before']['polygon_area'],4)
            self.assertEqual(snapshot['after']['polygon_area'],9)
            _,replacement=layers([box(0,0,2,2)],[Point(1,1)])
            workspace.layers['target']=replacement
            self.assertEqual(layer_comparison(workspace.layers)['after']['polygon_area'],0)
            self.assertEqual(snapshot['after']['polygon_area'],9)
        finally:workspace.close()


if __name__=='__main__':unittest.main()
