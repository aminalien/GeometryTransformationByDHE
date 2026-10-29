"""Publication settings, actual main figures, editable typography and safe exports."""
from pathlib import Path
import json
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
from matplotlib.mathtext import MathTextParser
from matplotlib.text import Text
from PIL import Image

from methodology_figure_generator import Repository, render, render_main, export_view, export_main, MethodologyApp
from methodology_publication import (
    PublicationSettings, MAIN_CAPTIONS, main_items, require_times_new_roman, style,
    TextEditor, raster_spec, capture_views,
)


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo=Repository()

    def test_five_main_recipes_actual_data_and_terminal_contacts(self):
        for stage,count in ((1,2),(2,4),(3,4),(4,5),(5,12)):
            items=main_items(self.repo,stage)
            self.assertEqual(len(items),count)
            self.assertTrue(all(i.panel.status=="actual" for i in items))
            self.assertEqual([i.letter for i in items],[chr(97+i) for i in range(count)])
            fig=render_main(self.repo,stage); fig.canvas.draw()
            self.assertEqual(len(fig.axes),count)
            self.assertGreater(fig.get_figwidth(),7.2)
            for artist in fig.findobj(Text):
                if artist.get_text(): self.assertIn("Times New Roman",artist.get_fontfamily())
            self.assertTrue(all(a.get_fontsize()==16 for key,a in fig._publication_artists.items() if "/z_level:" in key))
            fig.clear()
        terminal=[i for i in main_items(self.repo,5) if i.key.endswith("terminal_representation")]
        self.assertEqual([i.panel.shapes[0].geometry.geom_type for i in terminal],["Polygon","Point","MultiLineString","Polygon"])

    def test_times_math_glyphs_and_missing_font_failure(self):
        with matplotlib.rc_context(style(PublicationSettings())):
            glyphs=MathTextParser("path").parse(r"$z_s\quad z_t\quad z_m\quad z_b$",dpi=100).glyphs
            self.assertEqual({g[0].family_name for g in glyphs},{"Times New Roman"})
        require_times_new_roman.cache_clear()
        try:
            with patch("methodology_publication.font_manager.findfont",side_effect=ValueError("not installed")):
                with self.assertRaisesRegex(RuntimeError,"no substitute"):
                    render(self.repo.get("p2p"),1)
        finally:
            require_times_new_roman.cache_clear()

    def test_settings_roundtrip_and_validation(self):
        settings=PublicationSettings({"fonts":{"z_level":20,"vertex":8},"layout":{"width_in":12}})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,"settings.json"); settings.save(path)
            self.assertEqual(PublicationSettings.load(path).values,settings.values)
        for changes in ({"export":{"dpi":0}},{"export":{"dpi":float("inf")}},
                        {"layout":{"width_in":-1}},{"fonts":{"legend":0}},
                        {"export":{"size_mode":"pixels"}},{"layout":{"unknown":1}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): PublicationSettings(changes)

    def test_pixel_dimensions_and_arbitrary_dpi(self):
        data=self.repo.get("p2pt")
        for dpi in (300,600,1200,2400):
            settings=PublicationSettings({"export":{"dpi":dpi,"size_mode":"pixels","width_px":900,"height_px":700},
                                          "layout":{"top_in":.08,"bottom_in":.08,"show_title":False,"show_footer":False}})
            with tempfile.TemporaryDirectory() as folder:
                paths=export_view(data,5,"terminal_representation",folder,settings=settings)
                with Image.open(next(p for p in paths if p.suffix==".png")) as image:
                    self.assertEqual(image.size,(900,700))
                    self.assertAlmostEqual(image.info["dpi"][0],dpi,delta=.1)

    def test_memory_rejection_keeps_vector_outputs_and_records_reason(self):
        settings=PublicationSettings({"export":{"dpi":99999,"memory_budget_mib":1}})
        with tempfile.TemporaryDirectory() as folder:
            paths=export_view(self.repo.get("p2pt"),5,"terminal_representation",folder,settings=settings)
            self.assertEqual([p.suffix for p in paths],[".svg",".pdf"])
            record=json.loads(Path(folder,"figure_manifest.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(record["formats"]["png"]["status"],"unavailable")
            self.assertIn("budget",record["formats"]["png"]["reason"])
            self.assertFalse(list(Path(folder).glob("*.partial.*")))

    def test_large_canvas_preview_is_bounded_and_extreme_dpi_is_reported(self):
        settings=PublicationSettings({"layout":{"width_in":200,"height_in":150}})
        fig=render(self.repo.get("p2pt"),5,"terminal_representation",settings=settings)
        fig.canvas.draw()
        self.assertLessEqual(fig.bbox.width,1600.001)
        self.assertLessEqual(fig.bbox.height,2400.001)
        spec=raster_spec(fig,settings,dpi=1e308)
        self.assertIn("overflow",spec["reason"])
        fig.clear()

    def test_physical_sizes_camera_limits_and_independent_fonts(self):
        settings=PublicationSettings({"layout":{"width_in":11,"height_in":9},"fonts":{"title":17,"z_level":23,"name":12},
            "panels":{"stage5_p2pt_realized_dhe/realized_dhe":{"elev":37,"azim":23,"zoom":1.1,"xlim":[0,5]}}})
        fig=render(self.repo.get("p2pt"),5,"realized_dhe",settings=settings); fig.canvas.draw()
        self.assertEqual(tuple(fig.get_size_inches()),(11,9))
        self.assertEqual(fig.axes[0].elev,37); self.assertEqual(fig.axes[0].azim,23)
        self.assertEqual(tuple(fig.axes[0].get_xlim()),(0,5))
        self.assertEqual(fig._publication_artists[fig._publication_key+"/title"].get_fontsize(),17)
        self.assertEqual({a.get_fontsize() for k,a in fig._publication_artists.items() if "/z_level:" in k},{23})
        fig.clear()

    def test_drag_3d_label_offsets_and_axes_labels_survive_export(self):
        settings=PublicationSettings()
        fig=render(self.repo.get("p2pt"),5,"realized_dhe",settings=settings); fig.canvas.draw()
        editor=TextEditor(fig,settings); editor.set_enabled(True)
        key=next(k for k in fig._publication_artists if "/z_level:" in k)
        artist=fig._publication_artists[key]
        bbox=artist.get_window_extent(fig.canvas.get_renderer())
        start=SimpleNamespace(x=(bbox.x0+bbox.x1)/2,y=(bbox.y0+bbox.y1)/2,button=1)
        editor.press(start)
        self.assertEqual(editor.selected,key)
        editor.motion(SimpleNamespace(x=start.x+24,y=start.y+12)); editor.release(None)
        self.assertIn("offset",settings["annotations"][key])
        saved=tuple(artist.get_position())
        fig.axes[0].view_init(elev=40,azim=21); fig.canvas.draw()
        self.assertEqual(tuple(artist.get_position()),saved)
        editor.disconnect()
        views=capture_views(fig); fig.clear()
        clone=render(self.repo.get("p2pt"),5,"realized_dhe",settings=settings,axes_views=views)
        self.assertEqual(tuple(clone._publication_artists[key].get_position()),saved)
        clone.clear()
        with tempfile.TemporaryDirectory() as folder:
            export_view(self.repo.get("p2pt"),5,"realized_dhe",folder,dpi=300,settings=settings,axes_views=views)
            record=json.loads(Path(folder,"figure_manifest.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(tuple(record["publication_settings"]["annotations"][key]["offset"]),saved)

    def test_main_captions_match_local_manuscript(self):
        path=Path("DHE_LTS_Methodology_Final_With_Algorithms.md")
        if not path.exists(): self.skipTest("Private manuscript is not distributed with the tool")
        manuscript=path.read_text(encoding="utf-8")
        for stage,caption in MAIN_CAPTIONS.items():
            self.assertIn(f"*Figure {stage+1}. {caption}*",manuscript)
        self.assertEqual(len(re.findall(r"^\*Figure [2-6]\. ",manuscript,re.M)),5)
        self.assertNotIn("?",manuscript)
        self.assertIn("last ← MEV(v, s(last))",manuscript)
        self.assertIn("Sew(a, s(b))",manuscript)
        self.assertEqual(manuscript.count("```text"),2)


if __name__=="__main__": unittest.main()
