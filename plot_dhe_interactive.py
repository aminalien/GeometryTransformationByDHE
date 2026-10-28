# plot_dhe_interactive.py
# Local copy of aminalien/DHE; optional slicing and standalone viewing fixes.
# Keyboard-nav viewer for a DHE half-edge.
# Keys:
#   f -> e = e.NF          (next around face)
#   s -> e = e.S           (symmetric/twin)
#   a -> e = e.Adjacent()  (adjacent hop)
#   h -> toggle vertex labels
#   q / ESC -> close
import random
import matplotlib
import numpy as np
from matplotlib.widgets import CheckButtons
import math
from mpl_toolkits.mplot3d import proj3d
# Disable default key bindings that conflict with ours (f, s, etc.)
matplotlib.rcParams['keymap.fullscreen'] = []
matplotlib.rcParams['keymap.save'] = []
matplotlib.rcParams['keymap.quit'] = []
matplotlib.rcParams['keymap.grid'] = []
matplotlib.rcParams['keymap.grid_minor'] = []
matplotlib.rcParams['keymap.yscale'] = []
matplotlib.rcParams['keymap.xscale'] = []
matplotlib.rcParams['keymap.pan'] = []
matplotlib.rcParams['keymap.zoom'] = []
matplotlib.rcParams['keymap.back'] = []
matplotlib.rcParams['keymap.forward'] = []
import math
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa
from matplotlib.patches import Polygon as Poly2D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from matplotlib.widgets import TextBox
from matplotlib.widgets import Slider
from shapely.geometry import Polygon
from DHECore import (    make_vertex, reset_globals,ComplexSplice,KillComplexEdge,FindMatchesHE,
    MEVVFS, MEV, MEF, get_UniqueFace_in_cell, is_Face_planar, face_edges)
# import trimesh
from shapely.ops import triangulate
from top_face_overlay import RegionKind
REGION_COLORS = {
    RegionKind.ACTIVE_SOURCE_ONLY: (1.0, 0.55, 0.12, 1.0),
    RegionKind.ACTIVE_TARGET_ONLY: (0.20, 0.67, 0.35, 1.0),
    RegionKind.COMMON_NONACTIVE: (0.18, 0.47, 0.84, 1.0),
    RegionKind.BACKGROUND: (0.75, 0.78, 0.82, 1.0),
}
HILIGHT_COLOR = 'red'
FACE_HALFEDGE_COLOR = '#00bcd4'
NORMAL_COLOR = '0.65'
NORMAL_LW = 1
VERT_SIZE = 30


class DHEInteractivePlot:
    def __init__(self, seed_edge, loop_edges=None ,unique_duals=None, SlicingAnalyses=False, ExteraInfo=None, figure=None):

        if seed_edge is None:
            raise ValueError("Need a starting half-edge")

        self.vert_highlight = None
        self.e = seed_edge
        self.selection_callbacks = []
        self.face_halfedge_artists = []
        self.current_halfedge_line = None
        self.show_labels = True
        self.face_artist = None
        self.selected_face_artist = None
        self.current_face_fill_enabled = True
        self._selected_face_key = None
        self._selected_face_owner = None
        self._selection_edge = seed_edge
        self.cell_artists = []
        self.ShownFacesList=[]
        self.hi_line = None
        self.loop_edges = loop_edges or []
        self.unique_duals = unique_duals or []
        self.TextlabelList=list()
        self.LoDIndex=0
        self.ExteraInfo=ExteraInfo
        # 2D/3D detection
        def has3d(edges):
            if not edges:
                return False
            for he in edges:
                a, b = getattr(he, "V", None), getattr(getattr(he, "S", None), "V", None)
                if not (a and b):
                    continue
                if getattr(a, "z", 0.0) != 0.0 or getattr(b, "z", 0.0) != 0.0:
                    return True
            return False

        self.any3d = has3d(loop_edges) or getattr(self.e.V, "z", 0.0) != 0.0 or getattr(self.e.S.V, "z", 0.0) != 0.0

        self.fig = figure if figure is not None else plt.figure(figsize=(11, 7))
        self.fig.patch.set_facecolor("white")
        self.ax = self.fig.add_axes([0.24, 0.10, 0.74, 0.80], projection="3d" if self.any3d else None)
        self.ax.grid(False)
        # self.ax.set_xticks([])
        # self.ax.set_yticks([])
        # self.ax.set_zticks([0, 20, 40, 60,80 ,100])
        # # self.ax.tick_params(axis='z', colors='k')
        # self.ax.set_zticklabels(['      Source', '      LoD 0.2', '     LoD 0.4', '     LoD 0.6', '     LoD 0.8', '  Target'])
        self.ax.set_axis_off()
        # --- checkboxes and state ---
        self.dual_vertex_artists = []
        self.dual_edge_artists = []
        self.show_dual_vertices = False
        self.show_dual_edges = False
        self.show_lod_labels = False
        self.NumberDisconnectedFacesTotal=0
        self.NumberFeatureTotal = 0
        self.SlicingAnalyses = False
        if SlicingAnalyses:
            self.SlicingAnalyses=True
        self.esliceList=[]
        # print(self.e.D.V.x, self.e.D.V.y, self.e.D.V.z, self.e.D.V.color)

        # if self.any3d:
        #     # print(self.e.D.V.color)
        #     # self.dual_points = [
        #     #     self.ax.scatter(e.D.V.x, e.D.V.y, e.D.V.z, color=e.D.V.color, s=20)
        #     #     for e in self.loop_edges if hasattr(e, "D") and e.D and e.D.V
        #     # ]
        #     self.dual_points = [
        #         self.ax.scatter(e.D.V.x, e.D.V.y, e.D.V.z,
        #                         color=e.D.V.color, s=20)
        #         for e in unique_duals
        #     ]
        #     for pt in self.dual_points:
        #         pt.set_visible(False)
        # Get unique dual vertices (avoid duplicates)

        # --- create UI checkboxes (place them on left side of the figure) ---
        check_ax = self.fig.add_axes([0.02, 0.78, 0.22, 0.15])
        labels = ['Show Dual Vertices', 'Show Dual Half-Edges', 'Show LoD labels']
        actives = [self.show_dual_vertices, self.show_dual_edges, self.show_lod_labels]
        self.checkbox = CheckButtons(check_ax, labels, actives)
        for label in self.checkbox.labels:
            label.set_fontsize(8)
        self.checkbox.on_clicked(self._toggle_dual_visuals)
        # --- text box for selecting an edge by ID ---
        box_ax = self.fig.add_axes([0.09, 0.67, 0.12, 0.045])
        self.text_box = TextBox(box_ax, "Edge ID:", initial="")
        self.text_box.label.set_fontsize(9)


        self.HorizontalUniqueDHeList = list()
        def FindUiqueFeature():
            Listloopedges = self.loop_edges.copy()
            def findalledgesincell(e):

                oupute=e
                FaceCounter = 1
                starte = e
                DictEdges = {e: ""}
                finish = True
                while finish:
                    e = e.NF
                    if (starte == e):
                        i = -1
                        while finish:
                            e = list(DictEdges.keys())[i].S
                            if (e in DictEdges):
                                i -= 1
                                if (abs(i) > len(DictEdges)):
                                    finish = False
                                    break
                            else:
                                break
                        starte = e
                        DictEdges[e] = ""
                    else:
                        FaceCounter += 1
                        DictEdges[e] = ""

                for e in DictEdges:
                    # print(e.info)
                    if e in Listloopedges:
                        Listloopedges.remove(e)
                    if e.S in Listloopedges:
                        Listloopedges.remove(e.S)
                    if e.V.z<oupute.V.z and e.V.z==e.S.V.z:
                        oupute=e

                if oupute.NF.S.V.z!=oupute.NF.V.z:
                    oupute=oupute.S
                return oupute



            for e in Listloopedges:
                # print("heeere",e.info)
                self.HorizontalUniqueDHeList.append(findalledgesincell(e))

        def _on_submit(text):
            """Triggered only when Enter is pressed in the TextBox."""
            text = text.strip()
            if not text:
                return  # do nothing if empty
            for e in self.loop_edges:
                if getattr(e, "info", "") == text:
                    self._set_current_edge(e)
                    self._update_face_fill()
                    self._draw_highlight_line()
                    self._highlight_vertex()
                    self.fig.canvas.draw_idle()
                    break
        FindUiqueFeature()

        # Connect submit event
        self.text_box.on_submit(_on_submit)
        # Plot base edges if provided
        self.lines = []
        self.ShownFacesList=list()
        HorizontalLines=list()
        if loop_edges:
            seen = set()
            self.MaxDHE=loop_edges[0]
            self.MaxZallDHE=loop_edges[0].V.z
            self.MinZallDHE=loop_edges[0].V.z
            for he in loop_edges:
                # print(he.V.z)
                if he.V.z> self.MaxZallDHE:
                    self.MaxZallDHE=he.V.z
                    self.MaxDHE =he
                if he.V.z< self.MinZallDHE:
                    self.MinZallDHE=he.V.z
            # print(self.MaxZallDHE, self.MinZallDHE)


            for he in loop_edges:

                a = getattr(he, "V", None)
                b = getattr(getattr(he, "S", None), "V", None)
                if a.z == b.z:
                    HorizontalLines.append([a.z , he])
                if not (a and b):
                    continue
                key = tuple(sorted((getattr(a, "i", id(a)), getattr(b, "i", id(b)))))
                if key in seen:
                    continue
                seen.add(key)
                clr = he.D.V.color
                # clr=(238/255,0/255,0/255,1)
                EPS=1e-5
                ln=None
                if self.any3d:
                    # print(self.MaxZallDHE)
                    # print(a.z-b.z)
                    if abs(float(a.z) - float(b.z))>EPS:
                        clr=(0,0,0,0.2)

                        ln, = self.ax.plot([a.x, b.x], [a.y, b.y], [getattr(a, "z", 0.0), getattr(b, "z", 0.0)],
                                           color=clr, lw=NORMAL_LW)
                    if abs(float(a.z) - float(b.z))<EPS and (abs(float(self.MaxZallDHE)- float(a.z) )> EPS and abs(float(self.MinZallDHE)-float(a.z))>EPS ):
                        clr = (0, 0, 0, 0.2)
                        ln, = self.ax.plot([a.x, b.x], [a.y, b.y], [getattr(a, "z", 0.0), getattr(b, "z", 0.0)],
                                           color=clr, lw=NORMAL_LW)
                    elif abs(float(a.z) - float(b.z))<EPS :
                        if abs(float(self.MaxZallDHE)- float(a.z) )< EPS :
                            clr=(0/255,112/255,192/255,1)
                            clr = he.D.V.color
                        # elif abs(self.MinZallDHE-a.z)<EPS:
                        #     # clr = (1, 0, 0, 0.6)
                        ln, = self.ax.plot([a.x, b.x], [a.y, b.y], [getattr(a, "z", 0.0), getattr(b, "z", 0.0)],
                                           color=clr, lw=1)
                else:
                    ln, = self.ax.plot([a.x, b.x], [a.y, b.y], color=clr, lw=NORMAL_LW)
                if ln:
                    self.lines.append(ln)

            # print(self.MaxZallDHE, self.MinZallDHE)
            # if self.show_lod_labels:

            # print(self.MaxZallDHE)
            rows=HorizontalLines
            if rows:
                min_index, min_row = min(
                    enumerate(rows),
                    key=lambda x: x[1][0]  # first column
                )
                emin=min_row[1]
                if emin.NF.S.V.z!=emin.NF.V.z:
                    emin=emin.S
                self.MinimumHorizontalSurface=emin
                self.CurrentSlicingZ=emin.V.z
            # print("self.MinimumHorizontalSurface ",self.MinimumHorizontalSurface.info)
        # Draw vertex dots + labels
        self.vert_labels = []
        # self._draw_vertices(loop_edges)
        ExteraInfoEdgeList=[]
        for e in loop_edges:
            if e.exterainfo:
                ExteraInfoEdgeList.append(e)
                a, b = e.V, e.S.V
                if self.any3d and e.S.V.z!=0:
                    # direction vector from a → b
                    dx = b.x - a.x
                    dy = b.y - a.y
                    dz = b.z - a.z

                    # 3D arrow using quiver
                    self.hi_line = self.ax.quiver(
                        a.x, a.y, a.z,  # start point
                        dx, dy, dz,  # direction vector
                        color='#800080',
                        linewidth=1.5,
                        arrow_length_ratio=0.01,
                        zorder=10
                    )
        self._draw_vertices(ExteraInfoEdgeList)
        # Highlight and fill face
        self._draw_highlight_line()
        self._update_face_fill()

        self.ax.set_title(self._title_for_current())
        self.ax.set_xlabel("X"); self.ax.set_ylabel("Y")

        try:
            self.ax.set_box_aspect([1, 1, 0.5])
        except Exception:
            self.ax.set_aspect("equal", "box")

        self.cid = self.fig.canvas.mpl_connect("key_press_event", self.on_key)

        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        self.fig.patch.set_facecolor('white')
        self.ax.set_facecolor('white')
        if self.any3d:
            self.ax.xaxis.pane.fill = False
            self.ax.yaxis.pane.fill = False
            self.ax.zaxis.pane.fill = False
        # self.ax = self.fig.add_axes([0.05, 0.05, 0.9, 0.9], projection='3d')
    # ---------- visualization helpers ----------
    ###Fill-Cell :

    def _get_edges_in_same_cell(self, e):
        """Return all active edges whose dual vertex matches e.D.V."""
        FaceCounter = 1
        UniqueEdgeList = list()
        UniqueEdgeList.append(e)
        starte = e
        DictEdges = {e: ""}
        finish = True
        while finish:
            e = e.NF
            if (starte == e):
                i = -1
                while finish:
                    e = list(DictEdges.keys())[i].S

                    if (e in DictEdges):
                        i -= 1
                        if (abs(i) > len(DictEdges)):
                            finish = False
                            break
                    else:
                        FaceCounter += 1
                        UniqueEdgeList.append(e)
                        break
                starte = e
                DictEdges[e] = ""
            else:
                DictEdges[e] = ""
        return UniqueEdgeList, FaceCounter
    @staticmethod
    def _feature(dual):
        return str(getattr(dual, "logical_feature_id", dual.i))

    def _initialize_scene_faces(self, active_edges, shaded=False):
        """One material artist per physical face, with both incident owners."""
        self.scene_faces = {}
        seen = set()
        for edge in active_edges:
            if edge in seen:
                continue
            edges = face_edges(edge)
            seen.update(edges)
            points = [(e.V.x, e.V.y, e.V.z) for e in edges]
            key = tuple(sorted(points))
            record = self.scene_faces.setdefault(key, dict(
                points=points, seeds=[], artist=None, owner=None))
            record["seeds"].append(edge)
        self.shaded_features = ({self._feature(e.D.V) for e in active_edges}
                                if shaded else set())
        for record in self.scene_faces.values():
            owners = {self._feature(e.D.V) for e in record["seeds"]}
            record["hidden"] = len(record["seeds"]) == 2 and len(owners) == 1
            record["exterior"] = any(e.Adjacent().D.V.i == "VINF"
                                     for e in record["seeds"])
        self._refresh_scene_colors()

    def _refresh_scene_colors(self):
        # Some legacy navigation paths assign e directly. Treat an edge change
        # as a fresh selection, including another edge of the same face.
        if self.e is not self._selection_edge:
            self._selection_edge = self.e
            self.current_face_fill_enabled = True
        if not hasattr(self, "scene_faces"):
            self._render_current_face()
            return
        focus = self._feature(self.e.D.V)
        exterior_focus = focus == "VINF"
        selected_key = tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(self.e)))
        for key, record in self.scene_faces.items():
            seeds = record["seeds"]
            incident = [e for e in seeds if self._feature(e.D.V) == focus]
            chosen = incident[0] if incident else min(seeds, key=lambda e: str(e.D.V.i))
            owner = self._feature(chosen.D.V)
            record["owner"] = "VINF" if exterior_focus else owner
            # Selection owns this face independently, even after x or l.
            # Keeping its background material hidden also prevents double fill.
            visible = (key != selected_key and bool(self.shaded_features) and not record["hidden"] and (
                record["exterior"] if exterior_focus else any(
                    self._feature(e.D.V) in self.shaded_features for e in seeds)))
            if not visible:
                if record["artist"] is not None:
                    record["artist"].remove()
                    record["artist"] = None
                continue
            color = self.e.D.V.color if exterior_focus else chosen.D.V.color
            if record["artist"] is None:
                artist = Poly3DCollection(
                    [record["points"]], edgecolor=(0.15, 0.15, 0.15, 0.5),
                    linewidth=0.45, alpha=0.42)
                self.ax.add_collection3d(artist)
                record["artist"] = artist
            record["artist"].set_facecolor(color)
            record["artist"].set_alpha(0.42)
        self.cell_artists = [r["artist"] for r in self.scene_faces.values()
                             if r["artist"] is not None]
        self._render_current_face()

    def _remove_current_face_artist(self):
        if self.face_artist is not None:
            self.face_artist.remove()
        self.face_artist = None
        self.selected_face_artist = None
        self._selected_face_key = None
        self._selected_face_owner = None

    def _render_current_face(self):
        """Draw the selected face regardless of which cells are shaded."""
        points = [(e.V.x, e.V.y, e.V.z) for e in face_edges(self.e)]
        key = tuple(sorted(points))
        if not self.current_face_fill_enabled or len(set(points)) < 3:
            self._remove_current_face_artist()
            return
        if (key != self._selected_face_key or self.e.D.V is not self._selected_face_owner
                or self.face_artist is None):
            self._remove_current_face_artist()
            if self.any3d:
                self.face_artist = Poly3DCollection([points], edgecolor="none", alpha=0.50)
                self.ax.add_collection3d(self.face_artist)
            else:
                self.face_artist = Poly2D([p[:2] for p in points], closed=True,
                                         edgecolor="none", alpha=0.50)
                self.ax.add_patch(self.face_artist)
            self._selected_face_key = key
            self._selected_face_owner = self.e.D.V
        self.face_artist.set_facecolor(self.fix_color(self.e.D.V.color))
        self.face_artist.set_alpha(0.50)
        self.selected_face_artist = self.face_artist

    def _clear_current_face_fill(self):
        self.current_face_fill_enabled = False
        self._refresh_scene_colors()
        self.fig.canvas.draw_idle()

    def _highlight_cell_faces(self):
        if not hasattr(self, "scene_faces"):
            self._initialize_scene_faces(self.loop_edges)
        self.shaded_features.add(self._feature(self.e.D.V))
        self._refresh_scene_colors()
        self.fig.canvas.draw_idle()

    def _clear_HilightedLine_Vertex(self):
        if self.current_halfedge_line is not None:
            self.current_halfedge_line.remove()
            self.current_halfedge_line = None
        for artist in self.face_halfedge_artists:
            artist.remove()
        self.face_halfedge_artists.clear()
        if self.hi_line is not None:
            try:
                self.hi_line.remove()
            except Exception:
                pass
            self.hi_line = None
        if self.vert_highlight is not None:
            try:
                self.vert_highlight.remove()
            except Exception:
                pass
            self.vert_highlight = None
    def _clear_cell_faces(self):
        for art in self.cell_artists:
            art.remove()
        self.cell_artists.clear()
        if hasattr(self, "scene_faces"):
            self.shaded_features.clear()
            for record in self.scene_faces.values():
                record["artist"] = None
        self.fig.canvas.draw_idle()
    ######### Fill-Cell done!!
    ##CheckBoxes:
    ##CheckBoxes:
    def CheckHoles(self,esliceList):
        # for e1 in esliceList:
        #     verts1 = self._collect_face_vertices(e1)
        #     tri1 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts1]
        #     poly1 = Polygon(tri1)
        #
        #     for e2 in esliceList:
        #         # print(j)
        #         if e1!=e2:
        #             verts2 = self._collect_face_vertices(e2)
        #             tri2 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts2]
        #             poly2 = Polygon(tri2)
        #             if poly1.equals(poly2):
        #                 print("yyyyyyyyy")
        #                 esliceList.remove(e2)
        print(len(esliceList))
        for e1 in esliceList:
            verts1 = self._collect_face_vertices(e1)
            tri1 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts1]
            poly1 = Polygon(tri1)

            for e2 in esliceList:
                # print(j)
                if e1!=e2:
                    verts2 = self._collect_face_vertices(e2)
                    tri2 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts2]
                    poly2 = Polygon(tri2)
                    # if poly2.equals(poly1):
                    #     esliceList.remove(e2)
                    if poly1.contains(poly2) and not poly1.equals(poly2):
                        esliceList.remove(e2)
                        matches=FindMatchesHE(e1, e2, TypeOfMatch='nearest', tol=1e-3,
                                      OneMatch=True)
                        # for match in matches:
                        MEF(matches[1], matches[2])
                    elif poly2.contains(poly1) and not poly2.equals(poly1):
                        esliceList.remove(e1)
                        matches=FindMatchesHE(e2, e1, TypeOfMatch='nearest', tol=1e-3,
                                      OneMatch=True)
                        # for match in matches:
                        MEF(matches[1], matches[2].S)
        return esliceList
    def SlicingAnalysesFunction(self):
        def FindHausdorffdistance(elist1, elist2):
            # print(len(elist1), len(elist2))
            def Hausdorffdistance(e1,e2):
                starte1=e1
                MaxX=e1.V.x
                MaxY=e1.V.y
                MinX=e1.V.x
                MinY=e1.V.y
                dist=math.sqrt(pow(e1.V.x- e2.V.x, 2)+ pow(e1.V.y- e2.V.y, 2) )
                FinalMaxDist=[dist, [e1,e2]]
                Meandisplacement=0
                i=0
                while(True):
                    i+=1
                    if e1.V.x>MaxX:
                        MaxX=e1.V.x
                    if e1.V.y>MaxY:
                        MaxY=e1.V.y
                    if e1.V.x<MinX:
                        MinX=e1.V.x
                    if e1.V.y<MinY:
                        MinY=e1.V.y
                    dist=math.sqrt(pow(e1.V.x- e2.V.x, 2)+ pow(e1.V.y- e2.V.y, 2) )
                    MinDist=[dist, [e1,e2]]
                    starte2 = e2
                    while (True):
                        if e2.V.x > MaxX:
                            MaxX = e2.V.x
                        if e2.V.y > MaxY:
                            MaxY = e2.V.y
                        if e2.V.x < MinX:
                            MinX = e2.V.x
                        if e2.V.y < MinY:
                            MinY = e2.V.y
                        e2 = e2.NF
                        dist=math.sqrt(pow(e1.V.x- e2.V.x, 2)+ pow(e1.V.y- e2.V.y, 2) )
                        if e2 == starte2:
                            break
                        if dist<MinDist[0]:
                            MinDist=[dist, [e1,e2]]
                    # print(MinDist[0])
                    Meandisplacement+=MinDist[0]
                    if MinDist[0]>FinalMaxDist[0]:
                        FinalMaxDist=MinDist
                    e1=e1.NF
                    if e1==starte1:
                        break
                Meandisplacement=Meandisplacement/i
                D=math.sqrt(pow((MaxX-MinX), 2)+pow((MaxY-MinY), 2))
                # print(FinalMaxDist[0]/D , Meandisplacement/D )
                return FinalMaxDist[0]/D , Meandisplacement/D
            def FindPoly(e):
                Coords=[]
                starte=e
                while(True):
                    Coords.append((e.V.x, e.V.y))
                    e=e.NF
                    if e==starte:
                        break
                if len(Coords)>=3:
                    poly=Polygon(Coords)
                    return poly
                else:
                    return False
                # if FinalMinDist:
                #     MEF(FinalMinDist[1][0], FinalMinDist[1][1])
            polyList1=[]
            for e1 in elist1:
                poly1=FindPoly(e1)
                # print(poly1.centroid)
                if poly1:
                    polyList1.append([e1,poly1])
            polyList2=[]
            for e2 in elist2:
                poly2 = FindPoly(e2)
                if poly2:
                    polyList2.append([e2,poly2])
            # for pol1 in polyList1:
            MatchedDHEList=[]
            for pol1 in polyList1:
                Centre1=pol1[1].centroid
                Mindist=None
                for pol2 in polyList2:
                    Centre2 = pol2[1].centroid
                    distance = Centre1.distance(Centre2)
                    if Mindist:
                        if distance<Mindist[1]:
                            Mindist=[pol2, distance]
                    else:
                        Mindist = [pol2, distance]
                # print("min ",Mindist[1] )
                MatchedDHEList.append([pol1[0],Mindist[0][0] ])
            # print(MatchedDHEList)
            MaxNHausdorffdistance=0
            MaxNMeandisplacement=0
            for match in MatchedDHEList:
                # print("i to i+1")
                NHausdorffdistance1 ,NMeandisplacement1= Hausdorffdistance(match[0], match[1])
                # print("i+1 to i")
                NHausdorffdistance2 ,NMeandisplacement2=Hausdorffdistance(match[1], match[0])
                if NHausdorffdistance1 >MaxNHausdorffdistance:
                    MaxNHausdorffdistance=NHausdorffdistance1
                if NHausdorffdistance2 >MaxNHausdorffdistance:
                    MaxNHausdorffdistance=NHausdorffdistance2
                if NMeandisplacement1 >MaxNMeandisplacement:
                    MaxNMeandisplacement=NMeandisplacement1
                if NMeandisplacement2 >MaxNMeandisplacement:
                    MaxNMeandisplacement=NMeandisplacement2
            return MaxNHausdorffdistance, MaxNMeandisplacement
            # for e1 in elist1:
            #     for e2 in elist2:
            #         if e1.D.V==e2.D.V:
            #             Hausdorffdistance(e1, e2)

        eslice=self.esliceList

        if len(eslice)>1:

            eslice1=eslice[-1]
            eslice2=eslice[-2]
            MaxNHausdorffdistance, MaxNMeandisplacement=FindHausdorffdistance(eslice1, eslice2)
            print("between Slice ", self.LoDIndex - 1, "and Slice ", self.LoDIndex, " : NHausdorffdistance: ", MaxNHausdorffdistance,", NMeandisplacement: ", MaxNMeandisplacement)

            # print()
    def CalculateArea(self, e):
        area=None
        starte = e
        Coords = []
        while (True):
            Coords.append(((e.V.x, e.V.y)))
            e = e.NF
            if e == starte:
                break
        if len(Coords) > 3:
            poly = Polygon(Coords)
            area = poly.area
        return area

    def Areadeviationforeachfeature(self, eslice, Aref):
        areaS=self.CalculateArea(eslice)

        return (100 * abs(areaS - Aref) / Aref)
    def CaculateAreadeviation(self,esliceList, Aref):
        sumarea=0
        for e in esliceList:
            starte=e
            Coords=[]
            while(True):
                Coords.append(((e.V.x, e.V.y)))
                e=e.NF
                if e==starte:
                    break
            if len(Coords)>3:
                poly=Polygon(Coords)
                area=poly.area
                sumarea+=area
        if not isinstance(Aref, list):

            print("total Area deviation from total Source area: ", 100*abs(sumarea-Aref)/Aref)
        else:
            print("total Area deviation from total Source area: ", 100 * abs(sumarea - Aref[0]) / Aref[0])
            print("total Area deviation from total Target area: ", 100 * abs(sumarea - Aref[1]) / Aref[1])

    def _Move_Slicing_Surface(self, k, RemovePreviouseFaces=True):
        if getattr(self, "transition_panel", None) is not None:
            panel = self.transition_panel
            panel.move_section(1 if k == "up" else -1)
            return
        try:
            from Slicing import GenereteSlicing
        except ModuleNotFoundError as exc:
            if exc.name != 'Slicing':
                raise
            print('Slicing is optional and is not included in this construction example.')
            return
        def are_parallel(A, B, C, D, eps=1e-9):
            v1 = np.array(B) - np.array(A)
            v2 = np.array(D) - np.array(C)
            cross = np.cross(v1, v2)
            return np.linalg.norm(cross) < eps
        self.DivisionNumber=20
        # print(self.SlicingAnalyses)
        MovementZ=(self.MaxZallDHE-self.MinZallDHE)/self.DivisionNumber
        # MovementZ=1
        # print(self.CurrentSlicingZ)
        if k=='up':
            # self.LoDIndex+=10/self.DivisionNumber
            self.LoDIndex += 1
            self.CurrentSlicingZ = self.CurrentSlicingZ +MovementZ
            # print("before", self.CurrentSlicingZ)
            if self.CurrentSlicingZ >=self.MaxZallDHE:
                # print(self.CurrentSlicingZ)
                self.CurrentSlicingZ=0.999*self.MaxZallDHE
        else:
            # self.LoDIndex -= 10/self.DivisionNumber
            self.LoDIndex -= 1
            self.CurrentSlicingZ = self.CurrentSlicingZ -MovementZ
            if self.CurrentSlicingZ<=self.MinZallDHE:

                self.CurrentSlicingZ=1.001*self.MinZallDHE
                if self.CurrentSlicingZ==0:
                    self.CurrentSlicingZ=0.001
        # self.CurrentSlicingZ=1
        # self.CurrentSlicingZ=8*(self.MaxZallDHE-self.MinZallDHE)/DivisionNumber
        # print(self.CurrentSlicingZ)
        # print("after",self.CurrentSlicingZ)

        # # eDown=self.MinimumHorizontalSurface
        for art in self.ShownFacesList:
            art.remove()
            # self.ShownFacesList.remove(art)
        surface = np.array([
            [1, 1, float(self.CurrentSlicingZ)],
            [2, 1, float(self.CurrentSlicingZ)],
            [1, 2, float(self.CurrentSlicingZ)]
        ], dtype=float)
        self.ShownFacesList = []
        esliceList=[]
        # print(len(self.HorizontalUniqueDHeList))
        print(f"--------------Slice {self.LoDIndex } ------------")
        for eDown in self.HorizontalUniqueDHeList:
          # if eDown.D.V.i!=1110:
            # print(eDown)
            e=eDown
            starte=e
            while(True):

                A = (e.V.x, e.V.y, e.V.z)
                B = (e.S.V.x, e.S.V.y, e.S.V.z)
                C = (e.NF.V.x, e.NF.V.y, e.NF.V.z)
                D = (e.NF.S.V.x, e.NF.S.V.y, e.NF.S.V.z)
                if not are_parallel(A, B, C, D, eps=1e-4):
                    eDown=e
                    break
                e = e.NF
                if starte==e:
                    break
            # surface = np.array([
            #     [float(eDown.V.x), float(eDown.V.y), float(self.CurrentSlicingZ)],
            #     [float(eDown.S.V.x), float(eDown.S.V.y), float(self.CurrentSlicingZ)],
            #     [float(eDown.NF.S.V.x), float(eDown.NF.S.V.y), float(self.CurrentSlicingZ)]
            # ], dtype=float)
            # print(surface)
            # print(eDown.info, self.CurrentSlicingZ)
            # print(eDown.info)
            SlicedheList=GenereteSlicing(eDown, surface)
            # Aref = self.CalculateArea(eDown)
            # AreaDeviation=self.Areadeviationforeachfeature(SlicedheList[-1], Aref)
            # print("AreaDeviation (Aslice-Asource)/Asource*100 for feature ID: ", eDown.D.V.i , " is :", AreaDeviation)
            if SlicedheList:
                esliceList.extend(SlicedheList)
            if not SlicedheList:
                print("Not SLicessssd:", eDown)

        # print(len(esliceList))
        # esliceList=self.CheckHoles(esliceList)
        # print(len(esliceList))
        self._update_face_fill2(esliceList)
        if self.SlicingAnalyses:

            self.esliceList.append(esliceList)
            self.SlicingAnalysesFunction()
            if self.ExteraInfo:
                self.CaculateAreadeviation( esliceList, Aref=self.ExteraInfo)




        # for eslice in esliceList:
        #         self.e=eslice
        #         # print(eslice.info)
        #         self.ShownFacesList.append(self._update_face_fill(removePreviousFace=removePreviousFace))
        #         removePreviousFace=False

    def _toggle_dual_visuals(self, label):
        """Handle checkbox toggles for dual vertices and dual edges."""
        if label == 'Show Dual Vertices':
            self.show_dual_vertices = not self.show_dual_vertices
            if self.show_dual_vertices:
                self._draw_dual_vertices()
            else:
                self._clear_dual_vertices()

        elif label == 'Show Dual Half-Edges':
            self.show_dual_edges = not self.show_dual_edges
            if self.show_dual_edges:
                self._draw_dual_half_edges()
            else:
                self._clear_dual_half_edges()
        if label=='Show LoD labels':
            self.show_lod_labels = not self.show_lod_labels
            # print(self.show_lod_labels)
            if self.show_lod_labels:
                self._draw_lod_labels()
            else:
                self._clear_lod_labels()

        # redraw figure
        self.fig.canvas.draw_idle()

    def _autoscale_view(self):
        all_x = []
        all_y = []
        all_z = []

        for e in self.loop_edges:
            v1, v2 = e.V, e.S.V
            all_x += [v1.x, v2.x]
            all_y += [v1.y, v2.y]
            all_z += [v1.z, v2.z]

        self.ax.set_xlim(min(all_x) - 10, max(all_x) + 10)
        self.ax.set_ylim(min(all_y) - 10, max(all_y) + 10)
        self.ax.set_zlim(min(all_z) - 10, max(all_z) + 10)

    def expand_axes(self, pad=5000):
        xs, ys, zs = [], [], []
        for e in self.loop_edges:
            for v in [e.V, e.S.V]:
                xs.append(v.x)
                ys.append(v.y)
                zs.append(getattr(v, "z", 0.0))

        self.ax.set_xlim(min(xs) - pad, max(xs) + pad)
        self.ax.set_ylim(min(ys) - pad, max(ys) + pad)
        if self.any3d:
            self.ax.set_zlim(min(zs) - pad, max(zs) + pad)

    def _set_global_bounds(self):
        """Compute bounding box of all vertices and set expanded limits."""
        xs, ys, zs = [], [], []

        for he in self.loop_edges:
            for v in (he.V, he.S.V):
                xs.append(v.x)
                ys.append(v.y)
                zs.append(getattr(v, "z", 0.0))

        # Compute min/max
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        zmin, zmax = min(zs), max(zs)

        # Expand by 10% margin
        pad_x = (xmax - xmin) * 0.1
        pad_y = (ymax - ymin) * 0.1
        pad_z = (zmax - zmin) * 0.1

        # Apply limits
        if self.any3d:
            self.ax.set_xlim3d(xmin - pad_x, xmax + pad_x)
            self.ax.set_ylim3d(ymin - pad_y, ymax + pad_y)
            self.ax.set_zlim3d(zmin - pad_z, zmax + pad_z)
            self.ax.set_box_aspect([1, 1, 0.5])
        else:
            self.ax.set_xlim(xmin - pad_x, xmax + pad_x)
            self.ax.set_ylim(ymin - pad_y, ymax + pad_y)
            self.ax.set_aspect("equal", "box")
        # -- Move label positions --
        self.ax.xaxis.set_label_coords(0.5, -0.13)
        self.ax.yaxis.set_label_coords(-0.13, 0.5)
        if self.any3d:
            self.ax.zaxis.set_label_coords(1.15, 0.5)

        # # -- Move the tick labels outward --
        # self.ax.xaxis.set_tick_params(pad=12)
        # self.ax.yaxis.set_tick_params(pad=12)
        # self.ax.zaxis.set_tick_params(pad=12)
        #
        # # -- Optional: move the spines to nicer positions --
        # self.ax.xaxis._axinfo["juggled"] = (0, 1, 2)
        # self.ax.yaxis._axinfo["juggled"] = (1, 2, 0)
        # self.ax.zaxis._axinfo["juggled"] = (2, 0, 1)
    def _draw_dual_vertices(self):


        # Store them for later use
        self.unique_dual_vertices = list(self.unique_duals.values())
        # Create scatter artists only once
        self.dual_vertex_artists = []
        for v in self.unique_dual_vertices:
            # sc = self.ax.scatter(v.x, v.y, v.z, color=v.color, s=25, depthshade=False, visible=False)
            s = self.ax.scatter(
                        v.x, v.y, v.z,
                        color=v.color,
                        s=100,
                        edgecolors='k',
                        depthshade=True,
                        alpha=1,
                        zorder=20
                    )
            self.dual_vertex_artists.append(s)
        # """Add small spheres/scatter for each e.D.V."""
        # self._clear_dual_vertices()  # clear previous only (not everything)
        # active_edges = getattr(self, "loop_edges", [])
        # for e in active_edges:
        #     Dv = getattr(getattr(e, "D", None), "V", None)
        #     if Dv is None:
        #         continue
        #     col = getattr(Dv, "color", (0.8, 0.8, 0.8, 1.0))
        #
        #     # print(col, "duaaaal")
        #     x, y, z = Dv.x, Dv.y, getattr(Dv, "z", 0.0)
        #     s = self.ax.scatter(
        #         x, y, z,
        #         color=col,
        #         s=80,
        #         edgecolors='k',
        #         depthshade=True,
        #         alpha=col[3],
        #         zorder=10
        #     )
        #     self.dual_vertex_artists.append(s)

    def _clear_dual_vertices(self):
        """Remove only the dual vertex markers."""
        for artist in self.dual_vertex_artists:
            artist.remove()
        self.dual_vertex_artists = []

    def _draw_lod_labels(self):

        for e in self.HorizontalUniqueDHeList:
            self.TextlabelList.append(self.ax.text(self.MaxDHE.S.V.x, self.MaxDHE.S.V.y, self.MaxZallDHE * 1.04, "Target (l=1)", color="k",
                         fontsize=14, fontname="Times New Roman", fontweight='bold') )
            self.TextlabelList.append(self.ax.text(e.D.V.x, e.D.V.y, self.MinZallDHE +0.04, "Source (l=0)", color="k", fontsize=14, fontname="Times New Roman", fontweight='bold'))
            checknothorizontalLine=True
            e=self.MaxDHE
            starte=e
            while(True):
                if e.V.z==e.S.V.z:
                    checknothorizontalLine=False
                e=e.NV
                if e==starte:
                    break
            if checknothorizontalLine:
                v = self.MaxDHE.V
                self.TextlabelList.append(self.ax.scatter(
                    v.x, v.y, v.z,
                    color=self.MaxDHE.D.V.color,
                    s=100,
                    edgecolors='k',
                    depthshade=True,
                    alpha=1,
                    zorder=20
                ))

    def _clear_lod_labels(self):
        for txt in self.TextlabelList:
            # self.TextlabelList.remove(txt)
            txt.remove()
        self.TextlabelList = []
    def _draw_dual_half_edges(self):
        """Add green lines for unique dual half-edges (avoid duplicates)."""
        self._clear_dual_half_edges()
        active_edges = getattr(self, "loop_edges", [])

        unique_pairs = set()  # store (min_id, max_id) of dual vertices to ensure uniqueness

        for e in active_edges:
            eD = getattr(e, "D", None)
            if eD is None or not getattr(eD, "S", None):
                continue
            v1 = getattr(eD, "V", None)
            v2 = getattr(eD.S, "V", None)
            if v1 is None or v2 is None:
                continue

            # Skip if one vertex is VINF (infinite vertex)
            if getattr(v1, "i", None) == "VINF" or getattr(v2, "i", None) == "VINF":
                continue

            # Create an undirected pair key (avoid reversed duplicates)
            pair_key = tuple(sorted([id(v1), id(v2)]))
            if pair_key in unique_pairs:
                continue
            unique_pairs.add(pair_key)

            # Draw the unique dual edge
            xs = [v1.x, v2.x]
            ys = [v1.y, v2.y]
            zs = [getattr(v1, "z", 0.0), getattr(v2, "z", 0.0)]
            if zs[0] == zs[1]:
               clr=(0, 0.68, 0.313, 1)  # limegreen-ish with alpha
            else:

                clr=(1, 0.75, 0, 1)
                clr = (1, 0, 0, 1)
            line = self.ax.plot(xs, ys, zs, color=clr, lw=5, alpha=0.9, zorder=5)[0]
            self.dual_edge_artists.append(line)

    def _clear_dual_half_edges(self):
        """Remove only the dual half-edge lines."""
        for artist in self.dual_edge_artists:
            artist.remove()
        self.dual_edge_artists = []

    ###
    ##Camera zoom-in zoomout should be stable with each press event:
    def _store_view_state(self):
        """Store current view (zoom/rotation) depending on Axes type."""
        if hasattr(self.ax, "get_xlim3d"):  # 3D Axes
            self._view_state = {
                "xlim": self.ax.get_xlim3d(),
                "ylim": self.ax.get_ylim3d(),
                "zlim": self.ax.get_zlim3d(),
                "elev": getattr(self.ax, "elev", 30),
                "azim": getattr(self.ax, "azim", -60),
            }
        else:  # 2D fallback
            self._view_state = {
                "xlim": self.ax.get_xlim(),
                "ylim": self.ax.get_ylim(),
            }

    def _restore_view_state(self):
        """Restore previous view/zoom depending on Axes type."""
        if not hasattr(self, "_view_state"):
            return

        if hasattr(self.ax, "set_xlim3d"):  # 3D Axes
            self.ax.set_xlim3d(self._view_state["xlim"])
            self.ax.set_ylim3d(self._view_state["ylim"])
            self.ax.set_zlim3d(self._view_state["zlim"])
            self.ax.view_init(
                elev=self._view_state["elev"],
                azim=self._view_state["azim"]
            )
        else:  # 2D Axes
            self.ax.set_xlim(self._view_state["xlim"])
            self.ax.set_ylim(self._view_state["ylim"])

    def _draw_vertices(self, loop_edges):

        seen = {}
        def add_v(v):
            if v is not None:
                seen[getattr(v, "i", id(v))] = v

        if loop_edges:
            for he in loop_edges:
                # print("he")

                add_v(getattr(he, "V", None))
                # add_v(getattr(getattr(he, "S", None), "V", None))
        # else:
        #     for v in self._collect_face_vertices(self.e):
        #         # print("fff")
        #         add_v(v)

        xs = [v.x for v in seen.values()]
        ys = [v.y for v in seen.values()]
        zs = [getattr(v, "z", 0.0) for v in seen.values()]
        ids = [getattr(v, "i", None) for v in seen.values()]

        if self.any3d:
            self.ax.scatter(xs, ys, zs, s=VERT_SIZE, c='k', depthshade=True)
        else:
            self.ax.scatter(xs, ys, s=VERT_SIZE, c='k')

        # for (x, y, z, vid) in zip(xs, ys, zs, ids):
        #     label = str(vid) if vid is not None else ''
        #     if self.any3d:
        #         t = self.ax.text(1.01*x, y, 1.03*z, label,fontweight='bold', fontsize=6, ha='left', va='bottom', visible=self.show_labels)
        #     else:
        #         t = self.ax.text(x, y, label, fontsize=1, ha='left', va='bottom', visible=self.show_labels)
        #     self.vert_labels.append(t)

    def _title_for_current(self):
        e = self.e
        info = getattr(e, 'info', '')
        exterainfo= getattr(e, 'exterainfo', '')
        if exterainfo:
            exterainfo=exterainfo[0]
        a, b = e.V, e.S.V
        return f"{info}  |  Feature {self._feature(e.D.V)} / cell {e.D.V.i}  |  V:{getattr(a, 'i', '?')} → {getattr(b, 'i', '?')}"

    def _collect_face_vertices(self, e):
        # verts = [e_start.V]
        # cur = e_start.NF
        # guard = 0
        # while cur is not e_start and guard < 10000:
        #     verts.append(cur.V)
        #     cur = cur.NF
        #     guard += 1
        # return verts
        verts = list()
        # print("NewFace")
        start=e
        while (True):
            # print(e.info)
            verts.append(e.V)
            e=e.NF
            if start==e:
                break
        # print("Donee")
        return verts

    def _draw_highlight_line(self):
        self._clear_HilightedLine_Vertex()
        a, b = self.e.V, self.e.S.V
        if self.any3d:
            self.current_halfedge_line, = self.ax.plot(
                [a.x, b.x], [a.y, b.y], [a.z, b.z],
                color=HILIGHT_COLOR, lw=3, zorder=135)
        else:
            self.hi_line = self.ax.annotate(
                "", xy=(b.x, b.y), xytext=(a.x, a.y),
                arrowprops=dict(arrowstyle="->", lw=3, color=HILIGHT_COLOR))
        self._refresh_scene_colors()
        self.ax.set_title(self._title_for_current(), fontsize=9)
        for callback in self.selection_callbacks:
            callback(self.e)
        self.fig.canvas.draw_idle()

    def _highlight_vertex(self):
        # Selection is represented only by the current red half-edge.
        if self.vert_highlight is not None:
            self.vert_highlight.remove()
            self.vert_highlight = None

    def _set_current_edge(self, e):
        """Update the current edge and refresh visuals."""
        self.e = e
        self._selection_edge = e
        self.current_face_fill_enabled = True
        self._refresh_scene_colors()
        self._store_view_state()
        # ... do any plotting or removing ...
        self.fig.canvas.draw_idle()
        self._restore_view_state()
        # optional: print which edge is active
        print(f"CurrentEdge -> {getattr(e, 'info', e)}")

    def fix_color(self, col, default=(0.8, 0.8, 0.8, 1.0)):
        """Ensure color is a plain RGBA tuple."""
        if col is None:
            return default
        if hasattr(col, "mask") or isinstance(col, np.ma.MaskedArray):
            col = tuple(col.data.tolist())
        elif isinstance(col, np.ndarray):
            col = tuple(col.tolist())
        elif not isinstance(col, (tuple, list)) or len(col) < 3:
            return default
        return tuple(float(x) for x in col[:4])

    def _update_face_fill(self, removePreviousFace=True):
        self._refresh_scene_colors()
        self.fig.canvas.draw_idle()

    def _draw_line_from_edge(self, e, color='k', lw=2.0, alpha=1.0):
        a, b = e.V, e.S.V

        if self.any3d:
            line, = self.ax.plot(
                [a.x, b.x],
                [a.y, b.y],
                [getattr(a, "z", 0.0), getattr(b, "z", 0.0)],
                color=color,
                lw=lw,
                alpha=alpha,
                zorder=8
            )
        else:
            line, = self.ax.plot(
                [a.x, b.x],
                [a.y, b.y],
                color=color,
                lw=lw,
                alpha=alpha,
                zorder=8
            )

        return line

    def update_edge_lines(self, edges, clear=True):
        """
        Draw lines for a list of edges.
        Removes previous lines if clear=True.
        """
        # if clear:
        #     self._clear_shown_lines()
        colV = getattr(getattr(edges[0], "D", None), "V", None)
        face_color = getattr(colV, "color", (0.2, 0.6, 1.0, 0.25))
        face_color = (0, 0, 0, 0.8)
        avg_x, avg_y, avg_z= (0,0,0)
        for e in edges:
            avg_x+= e.V.x
            avg_y += e.V.y
            avg_z += e.V.z
            ln = self._draw_line_from_edge(
                e,
                color=face_color,
                lw=2.0,
                alpha=1.0
            )
            self.ShownFacesList.append(ln)
        avg_x /=len(edges)
        avg_y /= len(edges)
        avg_z /= len(edges)
        # self.TextlabelList.append(
        #     self.ax.text(avg_x, avg_y, avg_z * 1.04, f"l={self.LoDIndex / 10}", color="k", fontsize=14,
        #                  fontname="Times New Roman"))

        self.fig.canvas.draw_idle()
    def CheckLineOrpolygonOFe(self, e):
        #If its line it returns True
        starte=e
        edgeList=list()
        Check=False
        while(True):
            edgeList.append(e)
            if e.S==e.NF or e.S==e.NF.S:
                Check=True
            e=e.NF

            if e==starte:
                break
        if Check:
            return edgeList
        return False
    def CheckHole(self,e1, eList):
            print("nnnneeew")
            verts1 = self._collect_face_vertices(e1)
            tri1 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts1]
            A = Polygon(tri1)
            holes=[]
            outputtri=[tri1]
            check=False
            for e2 in eList:

                if e1!=e2:

                    verts2 = self._collect_face_vertices(e2)
                    tri2 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts2]

                    B = Polygon(tri2)

                    if A.contains(B) and not A.equals(B):
                        holes.append(B)
                        print(e1.info, e2.info)
                        check=True
                       # return True
            if check:
                poly = Polygon(shell=A, holes=holes)
                # print(poly)
                # vertices, faces = trimesh.creation.triangulate_polygon(poly)
                # vertices3d = np.column_stack([vertices, np.full(len(vertices), 10.0)])

                return outputtri
            else:
                return False
    def _update_face_fill2(self, eList):
        """Fill the face bounded by the current edge, if valid."""
        # Remove previous face artist safely
        # print(len(eList))

        if eList:


            # face_color=(0, 0, 0, 0.5)
            # for e1 in eList:
            #     for e2 in eList:
            #         if e1!=e2:
            #             verts1 = self._collect_face_vertices(e1)
            #             tri1 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts1]
            #             verts2 = self._collect_face_vertices(e2)
            #             tri2 = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts2]
            #             A = Polygon(tri1)
            #             B = Polygon(tri2)
            #             poly2d=None
            #             if A.within(B):
            #                 poly2d = Polygon(
            #                     [(v.x, v.y) for v in verts2],
            #                     holes=[[(v.x, v.y) for v in verts1]]
            #                 )
            #
            #             if B.within(A):
            #                     poly2d = Polygon(
            #                         [(v.x, v.y) for v in verts1],
            #                         holes=[[(v.x, v.y) for v in verts2]]
            #                     )
            #             if poly2d:
            #                     if e1 in eList:
            #                         eList.remove(e1)
            #                     if e2 in eList:
            #                         eList.remove(e2)
            #                     tris = triangulate(poly2d)
            #                     triangles_3d = []
            #                     z=e1.V.z
            #                     for t in tris:
            #                         coords = list(t.exterior.coords)[:-1]
            #                         triangles_3d.append([(x, y, z) for x, y in coords])
            #                     patch = Poly3DCollection(
            #                         [triangles_3d],
            #                         facecolor=face_color,
            #                         edgecolor='none',
            #                         alpha=0.5 * face_color[3],
            #                     )
            #                     self.ShownFacesList.append(patch)
            #                     self.ax.add_collection3d(patch)

            # print(len(eList))
            areaSum=0

            CellDict=dict()

            for e in eList:
                colV = getattr(getattr(e, "D", None), "V", None)
                face_color = getattr(colV, "color", (0.2, 0.6, 1.0, 0.25))
                # face_color=(0, 0, 0, 1)
                # if len(eList)>1:
                #     face_color = [
                #         random.random(),  # R
                #         random.random(),  # G
                #         random.random(),  # B
                #         1 # A
                #     ]
                # face_color = getattr(colV, "color", (0, 0, 0, 0.25))
                self.e=e
                verts = self._collect_face_vertices(e)


                # for e2 in eList:
                #     if e!=e2:
                #         print(len(FindMatchesHE(e,e2,TypeOfMatch='sym')))

                # if len(verts) < 3:
                #
                #     # No valid face (e.g., only two symmetric half-edges)
                #     self.fig.canvas.draw_idle()
                #     return
                #     # --- Get color safely ---

                edges=self.CheckLineOrpolygonOFe(e)
                # print(len(verts),edges )
                if edges:
                    # print(edges)
                    self.update_edge_lines( edges)

                    # --- draw polygon ---

                elif self.any3d:
                        tri = [(v.x, v.y, getattr(v, "z", 0.0)) for v in verts]
                        v = np.array(tri)
                        alpha=0.5 * face_color[3]
                        poly = Polygon(tri)
                        # if not poly.is_valid or not poly.is_simple:
                        #     print("Valid:", poly.is_valid,  poly.is_simple)
                        # print(len(tri))
                        # print(poly)
                        # print(poly)
                        area = poly.area

                        id=e.D.V.i
                        # print(id)
                        if id not in CellDict:
                            CellDict[id]=dict()
                            CellDict[id]['NumberEdges']=0
                            CellDict[id]['NumberFaces'] = 0
                            CellDict[id]['Area'] = 0
                        CellDict[id]['NumberFaces']+=1
                        CellDict[id]['NumberEdges'] += len(verts)
                        CellDict[id]['Area']+=round(area,ndigits=-8)
                        # # if e.D.V.i==f'Target {1}':
                        #     NumberFace+=1
                        #     areaSum=areaSum+area
                        #     print(e.D.V.i, len(verts), NumberFace, round(areaSum,ndigits=-8))
                        # print(area)
                        # avg_x, avg_y, avg_z = v.mean(axis=0)
                        # tri2=self.CheckHole( e, eList)
                        # if tri2:
                        #     tri=tri2
                            # alpha=0.1
                        # print(avg_x, avg_y, avg_z) {self.LoDIndex/10}
                        # self.TextlabelList.append(self.ax.text(avg_x, avg_y, avg_z*1.04, f"l={self.LoDIndex/10}", color="k", fontsize=14, fontname="Times New Roman" ))
                        # print(tri)
                        patch=Poly3DCollection(
                            [tri],
                            facecolor=face_color,
                            edgecolor='none',
                            alpha=alpha,
                        )
                        self.ShownFacesList.append( patch )
                        self.ax.add_collection3d(patch)
            # print(CellDict)
            # print("fffffff")
            NumberFace=0
            NumberEdges=0
            NumberDisconnectedFaces=0
            for id in CellDict:
                NumberEdges+= CellDict[id]['NumberEdges']
                NumberFace+= CellDict[id]['NumberFaces']
                if CellDict[id]['NumberFaces']>1:
                    NumberDisconnectedFaces+=1
                    self.NumberDisconnectedFacesTotal+=1
                    # self.NumberFacesTotal-= (CellDict[id]['NumberFaces']-1)
            self.NumberFeatureTotal+=len(CellDict)
            # print("Slice ",self.LoDIndex,": number of features: ", len(CellDict), ", Total Number Edges: ", NumberEdges, ", Total Number Faces: ", NumberFace , ", \n Number Disconnected Features: ", NumberDisconnectedFaces,",  Number Disconnected Features in Total all slices: ",self.NumberDisconnectedFacesTotal, " From ", self.NumberFeatureTotal, " Feature")
            print("number of features: ", len(CellDict), ", Total Number Edges: ", NumberEdges, ", Total Number Faces: ", NumberFace , ", \n Number Disconnected Features: ", NumberDisconnectedFaces)

            # print( "Total Number Edges: ", NumberEdges, ", Total Number Faces: ", NumberFace )

            # self.fig.canvas.draw_idle()
            #
            # self._store_view_state()
            # # ... do any plotting or removing ...
            # self.fig.canvas.draw_idle()
            # self._restore_view_state()

    # ---------- key events ----------
    def on_key(self, event):
        if getattr(self, "plane_controls", None) is not None and self.plane_controls.is_editing:
            return
        k = (event.key or '').lower()
        if getattr(self, "plane_controls", None) is not None and k in ("up", "down"):
            self.plane_controls.move_section(1 if k == "up" else -1)
            return
        check=True
        if k == 'f':
            self._set_current_edge(self.e.NF)
        elif k == 's':
            self._set_current_edge(self.e.S)
        elif k == 'a':
            self._set_current_edge(self.e.Adjacent())
        elif k == 'd':
            self._set_current_edge(self.e.D)
        elif k == 'v':
            self._set_current_edge(self.e.NV)
        elif k == 'o':
            self._set_current_edge(self.e.PrevF())
        elif k == 'e':
            self._set_current_edge(self.e.NextE())
        elif k == 'z':
             print((self.e.V.x,self.e.V.y,self.e.V.z))
        elif k == 'c':
            print("Highlighting cell of current edge...")
            self._highlight_cell_faces()

        elif k == 'x':
            print("Clearing highlighted cells...")
            self._clear_cell_faces()
        elif k == 'k':
            check=False
            self._clear_HilightedLine_Vertex()

        elif k == 'l':
            check=False
            self._clear_HilightedLine_Vertex()
            self._clear_current_face_fill()
        elif k == 'h':
            self.show_labels = not self.show_labels
            for t in self.vert_labels:
                t.set_visible(self.show_labels)
        elif k == 'up':
            # print("up")

            self._Move_Slicing_Surface( k)

        elif k == 'down':
            # print("down")
            self._Move_Slicing_Surface(k)
        elif k in ('q', 'escape'):
            plt.close(self.fig)
            return
        # self.text_box.set_val("")
        # self.text_box.set_val("")
        # refresh after navigation
        if check:
            self._draw_highlight_line()
            self._highlight_vertex()
            self._update_face_fill()

            self._store_view_state()
            # ... do any plotting or removing ...
            self.fig.canvas.draw_idle()
            self._restore_view_state()
    def _on_click(self, event):
        """Handle mouse clicks in 3D: select nearest projected edge."""
        if event.inaxes != self.ax:
            return
        if event.button != 1:  # left-click only
            return
        if event.xdata is None or event.ydata is None:
            return

        # Convert click location to display coordinates
        click_px, click_py = event.x, event.y

        min_dist = float("inf")
        nearest_edge = None

        # iterate through all visible edges
        for e in getattr(self, "loop_edges", []):
            v1 = getattr(e, "V", None)
            v2 = getattr(getattr(e, "S", None), "V", None)
            if v1 is None or v2 is None:
                continue

            # 3D coordinates of edge endpoints
            x1, y1, z1 = v1.x, v1.y, getattr(v1, "z", 0.0)
            x2, y2, z2 = v2.x, v2.y, getattr(v2, "z", 0.0)

            # project 3D to 2D display space
            x1p, y1p, _ = proj3d.proj_transform(x1, y1, z1, self.ax.get_proj())
            x2p, y2p, _ = proj3d.proj_transform(x2, y2, z2, self.ax.get_proj())

            # convert to screen coordinates (in pixels)
            trans = self.ax.transData.transform
            p1s = trans((x1p, y1p))
            p2s = trans((x2p, y2p))
            px, py = np.array([p1s, p2s]).T

            # distance from click point to line segment in screen space
            A = np.array([click_px - px[0], click_py - py[0]])
            B = np.array([click_px - px[1], click_py - py[1]])
            L = np.array([px[1] - px[0], py[1] - py[0]])
            L_len2 = L.dot(L)
            if L_len2 == 0:
                dist = np.linalg.norm(A)
            else:
                t = np.clip(A.dot(L) / L_len2, 0, 1)
                proj = np.array([px[0], py[0]]) + t * L
                dist = np.linalg.norm([click_px - proj[0], click_py - proj[1]])

            if dist < min_dist:
                min_dist = dist
                nearest_edge = e

        # click tolerance in pixels
        tol = 10.0
        if nearest_edge is not None and min_dist < tol:
            # update the current edge
            if hasattr(self, "_set_current_edge"):
                self._set_current_edge(nearest_edge)
            else:
                self.e = nearest_edge

            print(f"Clicked edge: {getattr(nearest_edge, 'info', 'unknown')}  (screen dist = {min_dist:.1f}px)")
            self._update_face_fill()
            self._draw_highlight_line()
            self.fig.canvas.draw_idle()
    def show(self):
        # plt.tight_layout()
        plt.show()


class _RoofOverlayPanel:
    """Linked transition projection and feature sections of the actual DHE shells."""

    def __init__(self, viewer, overlay, transition=None):
        self.viewer, self.overlay, self.transition = viewer, overlay, transition
        self.selected_fragment = None
        self.selection_3d_lines = []
        self.halfedge_2d_artists = []
        self.current_halfedge_2d_artist = None
        self.current_is_vertical = False
        self.classified = overlay.triangulation is not None
        self.section_artists = []
        self.section_step = 0.1
        all_fragments = transition.facets if transition else overlay.fragments
        self.edge_to_fragment = {
            edge: fragment for fragment in all_fragments for edge in face_edges(fragment.seed)}
        self.display_fragments = (
            transition.connecting_facets + [
                f for f in transition.projection_regions
                if f.region_kind in (RegionKind.COMMON_NONACTIVE, RegionKind.BACKGROUND)]
            if transition else overlay.fragments)
        viewer.fig.set_size_inches(18, 9)
        viewer.ax.set_position([0.17, 0.18, 0.40, 0.70])
        viewer.checkbox.ax.set_position([0.01, 0.78, 0.15, 0.15])
        viewer.text_box.ax.set_position([0.06, 0.67, 0.09, 0.045])
        self.ax = viewer.fig.add_axes([0.62, 0.18, 0.35, 0.70])
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_xlabel("Local X (m)")
        self.ax.set_ylabel("Local Y (m)")
        self.ax.set_title("XY transition projection" if transition else
                          f"Roof overlay at z = {overlay.height:g} m")
        self.patches = {}
        regions = ({r.region_id: r for r in overlay.triangulation.regions}
                   if self.classified else {})
        labelled = set()
        for fragment in self.display_fragments:
            geometry = fragment.geometry
            patch = Poly2D(
                list(geometry.exterior.coords), closed=True,
                facecolor=(fragment.seed.D.V.color if fragment.seed is not None else
                           next(c.dual_vertex.color for c in transition.cells if c.feature_id == fragment.cell_id)),
                alpha=0.18,
                edgecolor="0.55", linewidth=0.6)
            self.ax.add_patch(patch)
            self.patches[fragment.label] = patch
            region = regions.get(getattr(fragment, "parent_region_id", getattr(fragment, "region_id", None)))
            label = region.region_id if region else fragment.cell_id
            if label in labelled or geometry.area <= overlay.tolerance ** 2:
                continue
            labelled.add(label)
            center = (region.geometry if region else geometry).representative_point()
            self.ax.annotate(
                label, (center.x, center.y), xytext=(3, 4), textcoords="offset points",
                fontsize=6, color="0.25",
                bbox=dict(facecolor="white", alpha=0.65, edgecolor="none", pad=0.5))
        source_xy = np.asarray(overlay.source_geometry.exterior.coords)
        target = overlay.target_geometry
        target_xy = np.asarray(target.exterior.coords if target.geom_type == "Polygon" else target.coords)
        self.ax.plot(*source_xy.T, color="#135998", lw=1.7,
                     label=f"Source {overlay.source_id} (Z={overlay.height:g})")
        target_z = transition.target_z if transition else overlay.height
        self.ax.plot(*target_xy.T, color="#bb0088", lw=1.7,
                     marker="o" if target.geom_type == "Point" else None,
                     label=f"Target {overlay.target_id} (Z={target_z:g})")
        self.target_artists_3d = viewer.ax.plot(
            target_xy[:, 0], target_xy[:, 1], np.full(len(target_xy), target_z),
            color="#bb0088", lw=1.6, marker="o" if target.geom_type == "Point" else None,
            markersize=5, zorder=105)
        self.ax.plot([], [], color="0.5", lw=0.6, label="DHE facet projections")
        for index, vertex in enumerate(overlay.crossing_vertices, 1):
            self.ax.plot(vertex.x, vertex.y, "ko", ms=3)
            self.ax.annotate(f"I{index}", (vertex.x, vertex.y), xytext=(5, 6),
                             textcoords="offset points", fontsize=7)
        self.ax.margins(0.07)
        self.status = viewer.fig.text(0.62, 0.105, "", fontsize=8)
        viewer.fig.text(0.02, 0.30, "Current half-edge", color=HILIGHT_COLOR, fontsize=9)
        viewer.fig.text(0.02, 0.265, "Other face edges\n(projection only)",
                        color=FACE_HALFEDGE_COLOR, fontsize=8)
        if not transition:
            viewer.fig.text(0.62, 0.065, "Click a projected face to inspect its DHE half-edges.", fontsize=8)
        stage = ("Shared DHE transition" if transition else
                 "Active-region triangulation" if self.classified else "DHE roof overlay")
        viewer.fig.suptitle(
            f"{stage} | Source {overlay.source_id} → Target {overlay.target_id}", fontsize=14)
        viewer.fig.canvas.manager.set_window_title(
            f"DHE: {overlay.source_id} to {overlay.target_id}")
        viewer.selection_callbacks.append(self.on_selection)
        self.click_connection = viewer.fig.canvas.mpl_connect("button_press_event", self.on_click)
        active_source = [f for f in self.display_fragments
                         if f.region_kind is RegionKind.ACTIVE_SOURCE_ONLY]
        initial = (max(active_source, key=lambda f: f.geometry.area) if active_source else
                   next(f for f in self.display_fragments
                        if f.cell_id == overlay.source_id and f.inside_target))
        viewer._set_current_edge(initial.seed)
        viewer._draw_highlight_line()
        viewer._update_face_fill()
        self.ax.legend(loc="upper left", fontsize=7, framealpha=0.94)

    @staticmethod
    def _face_key(edge):
        return tuple(sorted((e.V.x, e.V.y, e.V.z) for e in face_edges(edge)))

    def on_selection(self, edge):
        self.selected_fragment = self.edge_to_fragment.get(edge)
        for artist in self.halfedge_2d_artists:
            artist.remove()
        self.halfedge_2d_artists.clear()
        self.current_halfedge_2d_artist = None
        focus = self.viewer._feature(edge.D.V)
        selected_key = self._face_key(edge)
        for fragment in self.display_fragments:
            patch = self.patches[fragment.label]
            seed = fragment.seed
            if seed is None:
                patch.set_linewidth(0.6)
                continue
            adjacent = seed.Adjacent()
            if self.viewer._feature(adjacent.D.V) == focus:
                seed = adjacent
            patch.set_facecolor(seed.D.V.color)
            selected = self._face_key(fragment.seed) == selected_key
            patch.set_edgecolor(FACE_HALFEDGE_COLOR if selected else "0.55")
            patch.set_linewidth(2 if selected else 0.6)
            patch.set_alpha(0.32 if selected else 0.18)
        coords = np.asarray([(e.V.x, e.V.y, e.V.z) for e in face_edges(edge)])
        self.current_is_vertical = False
        for e in face_edges(edge):
            a, b = e.V, e.S.V
            current = e is edge
            color = HILIGHT_COLOR if current else FACE_HALFEDGE_COLOR
            vertical = np.hypot(b.x-a.x, b.y-a.y) <= self.overlay.tolerance
            if vertical:
                artist, = self.ax.plot(a.x, a.y, "o", color=color,
                                       ms=6 if current else 4, zorder=135)
            else:
                artist = self.ax.annotate(
                    "", xy=(b.x, b.y), xytext=(a.x, a.y),
                    arrowprops=dict(arrowstyle="->", color=color, lw=2.6 if current else 1.8),
                    zorder=135)
            self.halfedge_2d_artists.append(artist)
            if current:
                self.current_halfedge_2d_artist = artist
                self.current_is_vertical = vertical
        f = self.selected_fragment
        text = f"{edge.info} | feature {focus} / cell {edge.D.V.i}"
        if f is not None:
            text += (f" | parent {f.parent_region_id}\n"
                     f"{getattr(f, 'part', 'base roof')} | "
                     f"Z={coords[:, 2].min():g}–{coords[:, 2].max():g} m")
            if f.region_kind is not None:
                text += f" | {f.region_kind.description}"
            elif not self.classified:
                text += " | inside target" if f.inside_target else " | outside target"
        if self.current_is_vertical:
            text += " | vertical in plan"
        self.status.set_text(text)
        self.viewer.fig.canvas.draw_idle()

    def on_click(self, event):
        if (event.inaxes is not self.ax or event.button != 1
                or event.xdata is None or event.ydata is None):
            return
        from shapely.geometry import Point
        point = Point(event.xdata, event.ydata)
        fragment = next((f for f in self.display_fragments
                         if f.seed is not None and f.geometry.area > self.overlay.tolerance ** 2
                         and f.geometry.covers(point)), None)
        if fragment is not None:
            edge = fragment.seed
            opposite = edge.Adjacent()
            if self.viewer._feature(opposite.D.V) == self.viewer._feature(self.viewer.e.D.V):
                edge = opposite
            self.viewer._set_current_edge(edge)
            self.viewer._draw_highlight_line()
            self.viewer._update_face_fill()


def interactive_plot_dhe(seed_edge, loop_edges=None):
    viewer = DHEInteractivePlot(seed_edge, loop_edges=loop_edges)
    viewer.show()
def interactive_plot_from_lists(dhe_List, Kill_List=None, pick='first', SlicingAnalyses=False, ExteraInfo=None, *, show=True, highlight_cells=False, overlay=None, transition=None, figure=None):
    """
    Visualize all half-edges in dhe_List except those in Kill_List.
    Highlight and start from a chosen CurrentEdge.
    Returns the viewer; show=False supports noninteractive preview rendering.
    highlight_cells=True shades all supplied cells using their dual vertex colors.
    overlay adds a linked plan-view panel of subdivided DHE roof fragments.
    pick: 'first', 'last', or 'random'
    """
    # if SlicingAnalyses:
    #     self.SlicingAnalyses=True

    if not dhe_List:
        raise ValueError("dhe_List is empty")

    Kill_List = Kill_List or []
    active_edges = [e for e in dhe_List if e not in Kill_List]
    unique_duals = {
        id(e.D.V): e.D.V
        for e in active_edges
        if getattr(e, "D", None)
           and getattr(e.D, "V", None)
           # and e.D.V not in Kill_List
    }
    if not active_edges:
        raise ValueError("No active edges to display (all killed).")

    # Choose the starting edge
    if pick == 'last':
        seed_edge = active_edges[-1]
    elif pick == 'random':
        seed_edge = random.choice(active_edges)
    else:
        seed_edge = active_edges[0]

    # print(f"Starting from edge {getattr(seed_edge, 'info', '')}")

    # Use the existing interactive viewer class
    if figure is None:
        figure = plt.figure(figsize=(11, 7))
    else:
        figure.clear()
    baseline = {(event, cid) for event, callbacks in figure.canvas.callbacks.callbacks.items()
                for cid in callbacks}
    viewer = DHEInteractivePlot(seed_edge, loop_edges=active_edges , unique_duals=unique_duals,
                                SlicingAnalyses=SlicingAnalyses, ExteraInfo=ExteraInfo, figure=figure)
    viewer.baseline_connections = baseline
    viewer._initialize_scene_faces(active_edges, shaded=highlight_cells)
    viewer._draw_highlight_line()
    viewer._update_face_fill()
    viewer._set_global_bounds()
    if viewer.any3d:
        xs = [e.V.x for e in active_edges]
        ys = [e.V.y for e in active_edges]
        zs = [e.V.z for e in active_edges]
        viewer.ax.set_box_aspect([max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)])
    viewer.fig.suptitle("DHE extrusion — " + ", ".join(str(v.i) for v in unique_duals.values()), fontsize=13)
    viewer.fig.canvas.manager.set_window_title("DHE extrusion: " + ", ".join(str(v.i) for v in unique_duals.values()))
    logical_duals = {}
    for vertex in unique_duals.values():
        logical_duals.setdefault(viewer._feature(vertex), vertex)
    for index, (feature_id, vertex) in enumerate(logical_duals.items()):
        viewer.fig.text(0.02, 0.58 - index * 0.035, f"Feature {feature_id}",
                        color=vertex.color, fontsize=9)
    viewer.fig.text(0.02, 0.38, "Exterior VINF", color=(0.55, 0.2, 0.75), fontsize=9)
    viewer.navigation_hint = viewer.fig.text(0.25, 0.035, "Drag: rotate   f: face   s: twin   a: adjacent   c: shade cell   x: clear   q: close", fontsize=8)
    if overlay is not None:
        viewer.overlay_panel = _RoofOverlayPanel(viewer, overlay, transition)
        if transition is not None:
            from plane_controls import PlaneSlicingPanel
            viewer.plane_controls = PlaneSlicingPanel(viewer, transition.model)
            viewer.transition_panel = viewer.plane_controls
    if show:
        viewer.show()
    return viewer
