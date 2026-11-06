"""Overlay measured profiles without changing their samples or references."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
from shapely.geometry import box

from generalization_model import _core
from lts_evaluation import (profile_figure,profile_comparison_figure,PROFILE_COMPARISON_COLORS,
                            PROFILE_METRICS,save_report,load_reports)
from methodology_publication import save_formats
from test_generalization import session
from test_lts_evaluation import evaluate


class ProfileComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s=session();s.apply(s.preview('point',[1],dz=5));s.apply(s.preview('smoothing',[3],dz=10))
        cls.history=evaluate(s,'whole_history',feature_ids=[1,3],consider_feature_family=False)
        other=session([box(0,0,10,5)])
        other.apply(other.preview('smoothing',[1],dz=10));other.apply(other.preview('point',[1],dz=20))
        cls.last=evaluate(other,feature_ids=[1],consider_feature_family=False)

    def test_current_profiles_keep_colors_steps_samples_and_na_gaps(self):
        before=deepcopy(self.history);core=_core()
        figure,_=profile_comparison_figure(self.history,'Feature 1',self.history,'Feature 3')
        for axis,(key,_) in zip(figure.axes,PROFILE_METRICS):
            self.assertEqual(len(axis.lines),4)
            for color,scope in zip(PROFILE_COMPARISON_COLORS,('Feature 1','Feature 3')):
                lines=[line for line in axis.lines if line.get_color()==color]
                self.assertEqual(len(lines),2)
                for step,line in zip((1,2),lines):
                    rows=[r for r in self.history['tables']['sections'] if r['scope']==scope and r['step']==step]
                    np.testing.assert_array_equal(line.get_xdata(),[r['z'] for r in rows])
                    np.testing.assert_allclose(line.get_ydata(),[np.nan if r[key] is None else r[key] for r in rows],equal_nan=True)
        self.assertTrue(np.isnan(figure.axes[0].lines[0].get_ydata()[-1]))
        self.assertEqual(len(figure.legends[0].texts),2)
        self.assertEqual(figure.axes[-1].get_xlabel(),'Transition Z')
        self.assertEqual(self.history,before);self.assertEqual(_core(),core)
        self.assertEqual(figure._profile_comparison['alignment'],'actual_z')
        figure.clear()

    def test_current_saved_and_saved_saved_keep_distinct_references_and_z(self):
        with tempfile.TemporaryDirectory() as folder:
            save_report(self.history,folder);save_report(self.last,folder)
            loaded,warnings=load_reports(folder);self.assertFalse(warnings)
            records={r['evaluation_id']:r for r in loaded}
            saved_a=records[self.history['evaluation_id']];saved_b=records[self.last['evaluation_id']]
            for first in (self.history,saved_a):
                figure,_=profile_comparison_figure(first,'Feature 3',saved_b,'Feature 1')
                profiles=figure._profile_comparison['profiles']
                self.assertEqual([p['z_range'] for p in profiles],[[5,20],[15,35]])
                self.assertEqual([p['reference_z'] for p in profiles],[5,15])
                self.assertNotEqual(profiles[0]['reference_diagonal_m'],profiles[1]['reference_diagonal_m'])
                self.assertEqual(profiles[1]['saved_report'],saved_b['_path'])
                self.assertEqual(profiles[1]['feature_selection']['policy'],'exact_ids')
                self.assertLessEqual(figure.axes[-1].get_xlim()[0],5)
                self.assertGreaterEqual(figure.axes[-1].get_xlim()[1],35)
                json.dumps(figure._profile_comparison,allow_nan=False)
                figure.clear()

    def test_all_missing_values_stay_missing_and_are_disclosed(self):
        missing=deepcopy(self.history)
        for row in missing['tables']['sections']:
            if row['scope']=='Feature 1':
                for key,_ in PROFILE_METRICS:row[key]=None
        figure,_=profile_comparison_figure(missing,'Feature 1',self.last,'Feature 1')
        self.assertFalse(any(figure._profile_comparison['profiles'][0]['valid_samples'].values()))
        self.assertTrue(any('no valid plotted values' in text.get_text() for text in figure.texts))
        for axis in figure.axes:
            self.assertTrue(all(np.isnan(line.get_ydata()).all() for line in axis.lines if line.get_color()==PROFILE_COMPARISON_COLORS[0]))
        figure.clear()

    def test_vector_raster_exports_and_single_profile_compatibility(self):
        figure,settings=profile_comparison_figure(self.history,'Feature 3',self.last,'Feature 1')
        settings['export']['dpi']=80
        with tempfile.TemporaryDirectory(dir='artifacts') as folder:
            files,formats,_=save_formats(figure,Path(folder),'comparison',settings)
            self.assertEqual({p.suffix for p in files},{'.svg','.pdf','.png'},formats)
            svg=(Path(folder)/'comparison.svg').read_text(encoding='utf-8')
            self.assertIn('profile-A-step1-area_change_pct',svg)
            self.assertIn('profile-B-step2-area_change_pct',svg)
            self.assertIn('#0072b2',svg.lower());self.assertIn('#d55e00',svg.lower())
            self.assertTrue((Path(folder)/'comparison.pdf').read_bytes().startswith(b'%PDF'))
            self.assertTrue((Path(folder)/'comparison.png').read_bytes().startswith(b'\x89PNG'))
        figure.clear()
        single,_=profile_figure(self.history,'Feature 3')
        self.assertEqual(len(single.axes),3);self.assertEqual(single._suptitle.get_text(),'Feature 3')
        single.clear()

    def test_duplicate_legacy_empty_unknown_and_nonfinite_profiles_reject(self):
        with self.assertRaisesRegex(ValueError,'distinct'):
            profile_comparison_figure(self.history,'Feature 1',self.history,'Feature 1')
        for invalid,scope in ((dict(schema_version=1,evaluation_id='legacy'),'Feature 1'),
                              (dict(schema_version=2,evaluation_id='empty',tables=dict(sections=[])),'Feature 1'),
                              (self.history,'Unknown feature')):
            with self.assertRaises(ValueError):profile_comparison_figure(self.history,'Feature 1',invalid,scope)
        bad=deepcopy(self.last);bad['tables']['sections'][0]['z']=float('nan')
        with self.assertRaisesRegex(ValueError,'invalid measured'):
            profile_comparison_figure(self.history,'Feature 1',bad,'Feature 1')


if __name__=='__main__':unittest.main()
