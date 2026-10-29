"""Real geometry, operator evidence, publication privacy and desktop smoke tests."""
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
from matplotlib.text import Text
from PIL import Image

import DHECore as dhe
import methodology_figure_data as source
from methodology_figure_generator import (
    CASES, STAGES, MethodologyApp, Repository, ViewOptions, capabilities,
    export_view, render, selected_panels, write_support_files,
)


def core_state():
    return dhe.dhe, dhe.killdhe, dhe.Vertex.cntId, dhe.DualHalfEdge.cntId


def strings(value):
    if isinstance(value,str):
        yield value
    elif is_dataclass(value):
        for f in fields(value):
            yield from strings(getattr(value,f.name))
    elif isinstance(value,(tuple,list)):
        for child in value:
            yield from strings(child)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repository()
        cls.data = {case:cls.repo.get(case) for case in CASES}

    def test_all_twenty_consistent_actual_stages_and_distinct_panels(self):
        count = 0
        for case,data in self.data.items():
            self.assertEqual(data.case,case)
            self.assertEqual(len(data.stages),5)
            keys = []
            for i,stage in enumerate(data.stages,1):
                count += 1
                self.assertEqual(stage.number,i)
                self.assertEqual(stage.title,STAGES[i-1])
                self.assertEqual(stage.status,"actual")
                self.assertTrue(all(p.status == "actual" for p in stage.panels))
                self.assertGreaterEqual(len(stage.panels),2)
                keys.append(tuple(p.key for p in stage.panels))
            self.assertEqual(len(set(keys)),5)
            self.assertEqual(data.cell_count,1 if case=="agg" else 5)
        self.assertEqual(count,20)

    def test_four_overlay_classes_area_colors_and_required_note(self):
        stage = self.data["p2p"].stages[1]
        panel = next(p for p in stage.panels if p.key=="region_classification")
        classes = {color:0. for color in (source.ORANGE,source.CYAN,source.GRAY,source.LIGHT)}
        for shape in panel.shapes:
            if shape.group == "region":
                classes[shape.color] += shape.geometry.area
        self.assertEqual(classes,{source.ORANGE:3.,source.CYAN:.5,source.GRAY:1.,source.LIGHT:11.5})
        self.assertEqual(stage.annotation,source.OVERLAY_NOTE)
        for case in ("p2pt","p2l","agg"):
            self.assertNotIn("region_classification",[p.key for p in self.data[case].stages[1].panels])

    def test_actual_operator_calls_order_semantics_and_no_aggregate_join(self):
        for case,data in self.data.items():
            counts = dict(data.operations)
            realization = dict(counts["realize"])
            for name in ("MEVVFS","MEV","MEF","Sew"):
                self.assertGreater(realization[name],0)
            self.assertEqual(realization.get("JoinByFace",0)>0,case!="agg")
            prep = dict(counts["prepare"])
            self.assertEqual(prep.get("MVE",0)>0,case=="p2p")
            sequence = [e.operator for e in data.events if e.operator=="MEVVFS"]
            self.assertEqual(sequence,["MEVVFS"])
            realization_events = data.events[next(i for i,e in enumerate(data.events) if e.operator=="MEVVFS"):]
            self.assertEqual([e.operator for e in realization_events[:3]],["MEVVFS","MEV","MEF"])
            self.assertTrue(all(e.verified for e in data.events),[(e.operator,e.verified) for e in data.events])
            for event in data.events:
                if event.operator=="MVE":
                    self.assertEqual(event.after[0],event.before[0])
                    self.assertEqual(event.after[-1],event.before[-1])
                    self.assertEqual(len(event.after),3)
            self.assertNotIn("KEF",realization)

    def test_final_realized_transition_facets_match_plans(self):
        for data in self.data.values():
            planned = data.stages[2].panels[1].facets
            realized = data.stages[4].panels[0].facets
            for facet in planned:
                self.assertTrue(any(facet.owner==f.owner and sorted(facet.points)==sorted(f.points) for f in realized))
            self.assertEqual(data.stages[4].panels[0].topology,True)
            self.assertFalse(data.stages[2].panels[1].topology)

    def test_actual_terminal_contacts_and_intermediate_sections(self):
        expected = {"p2p":"Polygon","p2pt":"Point","p2l":"MultiLineString","agg":"Polygon"}
        for case,data in self.data.items():
            owner = "Aggregate A" if case=="agg" else "Source S"
            terminal = dict(data.terminal)[owner]
            self.assertEqual(terminal.geom_type,expected[case])
            self.assertTrue(terminal.equals(data.target) or (terminal.geom_type==data.target.geom_type=="Polygon" and
                            terminal.symmetric_difference(data.target).area < 1e-8),case)
            self.assertGreater(dict(data.intermediate)[owner].area,0.)
            terminal_panel = data.stages[4].panels[2]
            self.assertTrue(terminal_panel.shapes[0].geometry.equals(terminal))
            source_faces = [f for f in data.stages[4].panels[0].facets if f.owner==owner]
            if case in {"p2pt","p2l"}:
                self.assertFalse([f for f in source_faces if all(p[2]==2 for p in f.points)])
        aggregate = self.data["agg"]
        self.assertEqual({f.owner for f in aggregate.stages[4].panels[0].facets},{"Aggregate A"})

    def test_snapshots_have_public_labels_only_no_live_core_records(self):
        def walk(v):
            self.assertNotIsInstance(v,(dhe.Vertex,dhe.DualHalfEdge))
            if is_dataclass(v):
                for f in fields(v):
                    walk(getattr(v,f.name))
            elif isinstance(v,(tuple,list)):
                for c in v:
                    walk(c)
        for data in self.data.values():
            walk(data)
            text = "\n".join(strings(data))
            for forbidden in ("private_","C:/University","C:\\University","OBJECTID","osm_id"):
                self.assertNotIn(forbidden,text)
            self.assertIsNone(re.search(r"0x[0-9a-f]{6,}",text))
            self.assertEqual(len(data.vertices),len(set(p for p,_ in data.vertices)))
            self.assertEqual(len(data.edges),len(set(p for p,_ in data.edges)))

    def test_core_and_profiler_restored_on_success_and_failure(self):
        before,profile = core_state(),sys.getprofile()
        source.build_case("p2pt")
        self.assertIs(dhe.dhe,before[0])
        self.assertIs(dhe.killdhe,before[1])
        self.assertEqual(core_state()[2:],before[2:])
        self.assertIs(sys.getprofile(),profile)
        with patch.object(source,"_preflight",side_effect=RuntimeError("capture failure")):
            with self.assertRaises(RuntimeError):
                source.build_case("p2p")
        self.assertIs(dhe.dhe,before[0])
        self.assertEqual(core_state()[2:],before[2:])
        with source.OperatorTrace({},"prepare"):
            self.assertNotEqual(sys.getprofile(),profile)
        self.assertIs(sys.getprofile(),profile)

    def test_render_all_combined_single_and_controls(self):
        for case,data in self.data.items():
            for stage in data.stages:
                for panel in ("combined",*(p.key for p in stage.panels)):
                    with self.subTest(case=case,stage=stage.number,panel=panel):
                        fig = render(data,stage.number,panel)
                        fig.canvas.draw()
                        self.assertEqual(len(fig.axes),len(selected_panels(stage,panel)))
                        for legend in fig.legends:
                            bbox = legend.get_window_extent(fig.canvas.get_renderer())
                            self.assertGreaterEqual(bbox.x0,0)
                            self.assertGreaterEqual(bbox.y0,0)
                            self.assertLessEqual(bbox.x1,fig.bbox.width)
                            self.assertLessEqual(bbox.y1,fig.bbox.height)
                        texts = "\n".join(t.get_text() for t in fig.findobj(Text))
                        self.assertNotIn("private_",texts)
                        fig.clear()
                for dimension in ("2D","3D"):
                    fig = render(data,stage.number,options=ViewOptions(dimension=dimension,legend=False,
                                 outlines=False,labels=False,primal=False,dual=False,ownership=False))
                    fig.canvas.draw()
                    self.assertTrue(all(ax.get_legend() is None for ax in fig.axes))
                    fig.clear()
        fig = render(self.data["p2p"],2,options=ViewOptions(vertex_ids=True,edge_ids=True))
        fig.canvas.draw()
        texts = "\n".join(t.get_text() for t in fig.findobj(Text))
        self.assertRegex(texts,r"v\d+")
        self.assertRegex(texts,r"e\d+")
        fig.clear()

    def test_exports_valid_privacy_manifest_captions_and_dpi(self):
        with tempfile.TemporaryDirectory() as directory:
            for case,data in self.data.items():
                paths = export_view(data,5,output=directory,dpi=300)
                self.assertEqual(paths[0].suffix,".svg")
                svg = paths[0].read_text(encoding="utf-8")
                self.assertIn("<svg",svg)
                self.assertNotIn("<image",svg)
                self.assertTrue(paths[1].read_bytes().startswith(b"%PDF"))
                with Image.open(paths[2]) as im:
                    self.assertAlmostEqual(im.info["dpi"][0],300,delta=.1)
                    self.assertEqual(im.size[0],3000)
                for filename in ("figure_captions.md","figure_manifest.json"):
                    content = Path(directory,filename).read_text(encoding="utf-8")
                    for banned in ("private_","C:/University","C:\\University","OBJECTID"):
                        self.assertNotIn(banned,content)
            export_view(self.data["p2pt"],5,"terminal_representation",directory,600)
            with Image.open(Path(directory,"stage5_p2pt_terminal_representation.png")) as image:
                self.assertAlmostEqual(image.info["dpi"][0],600,delta=.1)
            records = json.loads(Path(directory,"figure_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records),5)
            self.assertTrue(all(r["status"]=="actual" and r["checks"] for r in records))
            write_support_files(self.repo,directory)
            table = Path(directory,"visualization_status.md").read_text(encoding="utf-8")
            self.assertEqual(table.count("| actual |"),20)

    def test_current_camera_and_limits_survive_export(self):
        views = {"realized_dhe":{"xlim":[.1,3.9],"ylim":[.2,3.8],"zlim":[0,2],
                                  "elev":31.,"azim":77.,"roll":2.}}
        data = self.data["p2pt"]
        fig = render(data,5,"realized_dhe",axes_views=views)
        self.assertEqual(fig.axes[0].azim,77.)
        self.assertEqual(tuple(fig.axes[0].get_xlim()),(.1,3.9))
        fig.clear()
        with tempfile.TemporaryDirectory() as folder:
            export_view(data,5,"realized_dhe",folder,axes_views=views)
            record = json.loads(Path(folder,"figure_manifest.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(record["axes_views"],views)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest("Desktop display unavailable: "+str(exc))
        cls.app = MethodologyApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            if hasattr(cls,"app") and cls.app.figure:
                if cls.app.editor: cls.app.editor.disconnect()
                cls.app.canvas.toolbar = None
                cls.app.figure.clear()
        finally:
            if hasattr(cls,"root"):
                cls.root.destroy()

    def test_all_twenty_native_dropdown_events_and_panel_selections(self):
        app = self.app
        for case in CASES:
            app.case_var.set(CASES[case])
            app.case_box.event_generate("<<ComboboxSelected>>")
            self.root.update()
            for i,title in enumerate(STAGES,1):
                app.stage_var.set(f"{i}. {title}")
                app.stage_box.event_generate("<<ComboboxSelected>>")
                self.root.update()
                self.assertIn("Actual computed state",app.status.get())
                data,stage,key = app.selection()
                self.assertEqual((data.case,stage.number),(case,i))
                for label in app.panel_keys:
                    app.panel_var.set(label)
                    app.panel_box.event_generate("<<ComboboxSelected>>")
                    self.root.update()
                    self.assertIn("Actual computed state",app.status.get())
                app.panel_var.set("Combined panels")
        app.case_var.set(CASES["p2p"])
        app.stage_var.set(f"3. {STAGES[2]}")
        app.refresh()
        self.assertIn("disabled",app.widgets["primal"].state())
        self.assertIn("disabled",app.widgets["dual"].state())
        app.stage_var.set(f"4. {STAGES[3]}")
        app.refresh()
        self.assertNotIn("disabled",app.widgets["dual"].state())
        for key,var in app.vars.items():
            var.set(not var.get())
        app.refresh()
        self.assertIn("Actual computed state",app.status.get())

    def test_manuscript_mode_and_text_editor_native_controls(self):
        app=self.app
        for name,value in app.vars.items(): value.set(getattr(ViewOptions(),name))
        app.mode.set("Manuscript Figures 2–6")
        app.mode_box.event_generate("<<ComboboxSelected>>"); self.root.update()
        for stage,title in enumerate(STAGES,1):
            app.stage_var.set(f"{stage}. {title}")
            app.stage_box.event_generate("<<ComboboxSelected>>"); self.root.update()
            self.assertIn(f"Figure {stage+1}",app.status.get())
            self.assertIn("disabled",app.case_box.state())
            self.assertEqual(len(app.figure.axes),{1:2,2:4,3:4,4:5,5:12}[stage])
        app.edit_text.set(True); app.toggle_editor()
        self.assertTrue(app.editor.enabled)
        app.edit_text.set(False); app.toggle_editor()
        self.assertFalse(app.editor.enabled)
        app.mode.set("Case gallery"); app.refresh()


if __name__ == "__main__":
    unittest.main()
