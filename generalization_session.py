"""Native, independent map-generalization workspace launched by main.py."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

from generalization_geometry import DEFAULT_DATA, load_shapefile, download_osm
from generalization_model import GeneralizationSession

OPERATIONS = {"Simplification":"simplification", "Smoothing":"smoothing", "Collapse to point":"point",
              "Collapse to polyline":"polyline", "Merging":"merging", "Aggregation":"aggregation"}


def original_id(attributes):
    names={str(k).lower():k for k in attributes}
    key=next((names[name] for name in ("osm_id","id","fid","objectid") if name in names),None)
    return str(attributes[key]) if key is not None else "—"


def launch_generalization(path=DEFAULT_DATA):
    """Separate process: DHECore contains process-global topology registries."""
    return subprocess.Popen([sys.executable,str(Path(__file__).with_name("main.py")),"--scene","generalization","--input-shp",str(path)],
                            cwd=str(Path(__file__).parent))


def parse_indices(text):
    result=set()
    for token in re.split(r"[,;\s]+",text.strip()):
        if not token:
            continue
        if "-" in token:
            a,b=map(int,token.split("-",1))
            if not 1<=a<=b or b-a>100000:
                raise ValueError("Use increasing positive index ranges, e.g. 1-5.")
            result.update(range(a,b+1))
        else:
            result.add(int(token))
    if any(i<1 for i in result):
        raise ValueError("Feature indices start at 1.")
    return result


class GeneralizationWindow:
    def __init__(self, path=DEFAULT_DATA, *, root=None, autoload=True, initial_height=5.):
        import tkinter as tk
        from tkinter import ttk
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.widgets import RectangleSelector
        self.tk,self.ttk=tk,ttk
        self.initial_height=initial_height
        self.root=root or tk.Tk()
        self.root.title("DHE · Map Generalization")
        self.root.geometry("1650x1000")
        self.messages=queue.Queue()
        self.busy=False
        self.closed=False
        self.session=None
        self.proposal=None
        self.selected=set()
        self.inspector=None
        self.visualizations=[]
        self.slicer=None
        self.face_index=0
        self.edge_index=0
        self.syncing=False
        self.setting_plane=False
        self.revision=0
        self.variables={}
        body=ttk.Panedwindow(self.root,orient="horizontal")
        body.pack(fill="both",expand=True)
        side=ttk.Frame(body,width=335)
        body.add(side,weight=0)
        # A scrollable control panel remains usable on smaller screens.
        scroll=tk.Canvas(side,width=325,highlightthickness=0)
        bar=ttk.Scrollbar(side,orient="vertical",command=scroll.yview)
        scroll.configure(yscrollcommand=bar.set)
        bar.pack(side="right",fill="y");scroll.pack(side="left",fill="both",expand=True)
        self.controls=ttk.Frame(scroll,padding=8)
        win=scroll.create_window((0,0),window=self.controls,anchor="nw")
        self.controls.bind("<Configure>",lambda e:scroll.configure(scrollregion=scroll.bbox("all")))
        scroll.bind("<Configure>",lambda e:scroll.itemconfigure(win,width=e.width))
        pane=ttk.Frame(body)
        body.add(pane,weight=1)
        self.figure=Figure(figsize=(13,9),layout="constrained")
        self.figure.get_layout_engine().set(w_pad=.3,h_pad=.08,wspace=.07,hspace=.08)
        grid=self.figure.add_gridspec(2,2,width_ratios=(1,1.8),height_ratios=(1.25,1))
        self.map_ax=self.figure.add_subplot(grid[0,0])
        self.ax=self.figure.add_subplot(grid[:,1],projection="3d")
        self.section_ax=self.figure.add_subplot(grid[1,0])
        self.canvas=FigureCanvasTkAgg(self.figure,master=pane)
        self.canvas.get_tk_widget().pack(fill="both",expand=True)
        NavigationToolbar2Tk(self.canvas,pane)
        self.rectangle=RectangleSelector(self.map_ax,self._rectangle,button=[3],useblit=False,minspanx=3,minspany=3,spancoords="pixels")
        self.canvas.mpl_connect("button_press_event",self._map_click)
        self.canvas.mpl_connect("key_press_event",self._key)
        self.canvas.mpl_connect("scroll_event",self._plot_scroll)
        self._build_controls(path)
        self.status=tk.StringVar(value="Load a polygon dataset to begin.")
        ttk.Label(self.root,textvariable=self.status,wraplength=1550,anchor="w").pack(fill="x",padx=8,pady=5)
        self.root.protocol("WM_DELETE_WINDOW",self.close)
        self.poll_id=self.root.after(100,self._poll)
        self.load_id=None
        if autoload:
            self.load_id=self.root.after(150,self.load)

    def _var(self,key,default):
        self.variables[key]=self.tk.StringVar(value=str(default))
        return self.variables[key]

    def _group(self,title):
        frame=self.ttk.LabelFrame(self.controls,text=title,padding=7)
        frame.pack(fill="x",pady=4)
        return frame

    def _entry(self,parent,label,key,default):
        row=self.ttk.Frame(parent);row.pack(fill="x",pady=2)
        self.ttk.Label(row,text=label,width=18).pack(side="left")
        self.ttk.Entry(row,textvariable=self._var(key,default),width=12).pack(side="right",fill="x",expand=True)

    def _build_controls(self,path):
        ttk,tk=self.ttk,self.tk
        group=self._group("Data source")
        ttk.Entry(group,textvariable=self._var("path",path)).pack(fill="x")
        row=ttk.Frame(group);row.pack(fill="x",pady=4)
        ttk.Button(row,text="Browse…",command=self.browse).pack(side="left")
        ttk.Button(row,text="Load",command=self.load).pack(side="left",padx=3)
        ttk.Button(row,text="OSM bbox…",command=self.osm_dialog).pack(side="left")
        self.data_label=ttk.Label(group,text="",wraplength=280)
        self.data_label.pack(fill="x")
        group=self._group("Selection · click map / right-drag box")
        self.tree=ttk.Treeview(group,columns=("id","kind"),show="tree headings",selectmode="extended",height=7)
        self.tree.heading("#0",text="Index");self.tree.column("#0",width=48)
        self.tree.heading("id",text="Original ID");self.tree.column("id",width=115)
        self.tree.heading("kind",text="Type");self.tree.column("kind",width=65)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>",self._tree_select)
        self._entry(group,"Indices / ranges","indices","")
        ttk.Button(group,text="Select indices",command=self.select_indices).pack(fill="x")
        self._entry(group,"Original IDs","ids","")
        ttk.Button(group,text="Select original IDs",command=self.select_ids).pack(fill="x")
        row=ttk.Frame(group);row.pack(fill="x",pady=3)
        ttk.Button(row,text="All polygons",command=self.select_all).pack(side="left")
        ttk.Button(row,text="Clear",command=lambda:self._select(set())).pack(side="left",padx=4)
        self.selection_label=ttk.Label(group,text="None selected",wraplength=280)
        self.selection_label.pack(fill="x")
        ttk.Button(group,text="Attributes / lineage…",command=self.attributes).pack(fill="x")
        group=self._group("Generalization")
        self.operation=tk.StringVar(value="Simplification")
        menu=ttk.Combobox(group,textvariable=self.operation,values=tuple(OPERATIONS),state="readonly")
        menu.pack(fill="x");menu.bind("<<ComboboxSelected>>",lambda e:self._invalidate())
        for label,key,value in [("Height increment ΔZ","dz",10),("Tolerance (m)","tolerance",1),("Smoothing iterations","iterations",1),("Corner ratio","ratio",.25)]:
            self._entry(group,label,key,value)
            self.variables[key].trace_add("write",lambda *args:self._invalidate())
        ttk.Label(group,text="Merge winner index").pack(anchor="w")
        self.winner=tk.StringVar()
        self.winner_combo=ttk.Combobox(group,textvariable=self.winner,state="readonly",values=())
        self.winner_combo.pack(fill="x")
        self.winner.trace_add("write",lambda *args:self._invalidate())
        row=ttk.Frame(group);row.pack(fill="x",pady=5)
        self.preview_button=ttk.Button(row,text="Preview",command=lambda:self.compute(False));self.preview_button.pack(side="left",expand=True,fill="x")
        self.apply_button=ttk.Button(row,text="Apply",command=lambda:self.compute(True));self.apply_button.pack(side="left",expand=True,fill="x")
        group=self._group("History")
        self.history_var=tk.StringVar()
        self.history_combo=ttk.Combobox(group,textvariable=self.history_var,state="readonly")
        self.history_combo.pack(fill="x")
        self.history_combo.bind("<<ComboboxSelected>>",self._history)
        ttk.Button(group,text="Undo",command=self.undo).pack(fill="x",pady=2)
        group=self._group("DHE inspection")
        row=ttk.Frame(group);row.pack(fill="x")
        ttk.Button(row,text="Previous face",command=lambda:self._face(-1)).pack(side="left")
        ttk.Button(row,text="Next face",command=lambda:self._face(1)).pack(side="left")
        row=ttk.Frame(group);row.pack(fill="x")
        ttk.Button(row,text="Previous edge",command=lambda:self._edge(-1)).pack(side="left")
        ttk.Button(row,text="Next edge",command=lambda:self._edge(1)).pack(side="left")
        self.edge_label=ttk.Label(group,text="",wraplength=280);self.edge_label.pack(fill="x")
        self.fill=tk.BooleanVar(value=True)
        ttk.Checkbutton(group,text="Fill cells",variable=self.fill,command=self.draw).pack(anchor="w")
        ttk.Button(group,text="Open full DHE inspector",command=self.inspect).pack(fill="x")
        ttk.Button(group,text="Open transition visualization…",command=self.visualize_transition).pack(fill="x")
        ttk.Button(group,text="Last transition diagnostics…",command=self.diagnostics).pack(fill="x")
        self._entry(group,"3D Z exaggeration","exaggeration",3)
        ttk.Button(group,text="Update 3D view",command=self.draw).pack(fill="x")
        group=self._group("Cross-section · ↑/↓, wheel, buttons")
        self.orientation=tk.StringVar(value="Horizontal")
        combo=ttk.Combobox(group,textvariable=self.orientation,values=("Horizontal","Vertical","Inclined"),state="readonly")
        combo.pack(fill="x");combo.bind("<<ComboboxSelected>>",lambda e:self._plane_range())
        self._entry(group,"Tilt (degrees)","tilt",45)
        self._entry(group,"Azimuth (degrees)","azimuth",0)
        self._entry(group,"Step size","step",.1)
        for key in ("tilt","azimuth","step"):
            self.variables[key].trace_add("write",lambda *a:self._plane_range())
        self.plane_value=tk.DoubleVar(value=0.)
        self.slider=tk.Scale(group,orient="horizontal",from_=0,to=5,resolution=.1,variable=self.plane_value,command=self._slice,showvalue=True,takefocus=True,length=270)
        self.slider.pack(fill="x")
        self.slider.bind("<MouseWheel>",self._wheel)
        self.slider.bind("<Button-1>",lambda e:self.show_plane.set(True),add=True)
        self.slider.bind("<B1-Motion>",lambda e:self.show_plane.set(True),add=True)
        self.slider.bind("<Up>",lambda e:self.move_plane(1))
        self.slider.bind("<Down>",lambda e:self.move_plane(-1))
        row=ttk.Frame(group);row.pack(fill="x")
        ttk.Button(row,text="Lower",command=lambda:self.move_plane(-1)).pack(side="left",expand=True,fill="x")
        ttk.Button(row,text="Raise",command=lambda:self.move_plane(1)).pack(side="left",expand=True,fill="x")
        self.show_plane=tk.BooleanVar(value=False)
        ttk.Checkbutton(group,text="Show slicing plane in 3D",variable=self.show_plane,command=self._slice).pack(anchor="w")
        ttk.Button(self.controls,text="Export current figure…",command=self.export).pack(fill="x",pady=8)

    def _start(self,function,callback,message):
        if self.busy:
            return
        self._close_inspector()
        self.busy=True
        self.preview_button.configure(state="disabled");self.apply_button.configure(state="disabled")
        self.status.set(message)
        def work():
            try:self.messages.put((callback,function(),None))
            except Exception as exc:self.messages.put((callback,None,exc))
        threading.Thread(target=work,daemon=True,name="DHE generalization worker").start()

    def _poll(self):
        if self.closed:return
        try:
            callback,result,error=self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy=False
            self.preview_button.configure(state="normal");self.apply_button.configure(state="normal")
            if error:
                self.status.set("Model unchanged: "+str(error))
                from tkinter import messagebox
                messagebox.showerror("Operation could not be completed",str(error),parent=self.root)
            else:
                try:callback(result)
                except Exception as exc:
                    self.status.set(str(exc))
                    from tkinter import messagebox
                    messagebox.showerror("Workspace error",str(exc),parent=self.root)
        self.poll_id=self.root.after(100,self._poll)

    def load(self):
        if self.busy:return
        path=self.variables["path"].get()
        self._start(lambda:GeneralizationSession(load_shapefile(path),self.initial_height),self._loaded,"Loading and constructing initial DHE cells…")

    def _loaded(self,session):
        self._close_inspector()
        self.session=session;self.proposal=None;self.selected=set();self.face_index=0
        self.data_label.configure(text=f"{len(session.state.features)} polygons · {session.data.frame.crs.name}\n{session.data.source}")
        self._refresh()
        rejected=session.data.rejected
        self.status.set(f"Loaded {len(session.state.features)} features. Z=0–{session.state.z:g}. "+(f"{len(rejected)} unsupported records: see report." if rejected else ""))
        if rejected:
            self._text_dialog("Dataset report","\n".join(rejected))

    def browse(self):
        from tkinter import filedialog
        path=filedialog.askopenfilename(parent=self.root,filetypes=[("Shapefile","*.shp")])
        if path:self.variables["path"].set(path);self.load()

    def osm_dialog(self):
        if self.busy:return
        top=self.tk.Toplevel(self.root);top.title("Download OSM buildings")
        values=[16.99,51.12,17.00,51.13]
        if self.session:
            values=list(self.session.data.frame.to_crs(4326).total_bounds)
        variables=[]
        for label,value in zip(("West longitude","South latitude","East longitude","North latitude"),values):
            self.ttk.Label(top,text=label).pack(anchor="w",padx=12)
            var=self.tk.StringVar(value=f"{value:.7f}");variables.append(var)
            self.ttk.Entry(top,textvariable=var).pack(fill="x",padx=12,pady=3)
        self.ttk.Label(top,text="Complete building footprints · © OpenStreetMap contributors\nUse a small neighbourhood bounding box.").pack(padx=12,pady=8)
        def fetch():
            try:bounds=[float(v.get()) for v in variables]
            except ValueError:return
            top.destroy()
            self._start(lambda:GeneralizationSession(download_osm(*bounds),self.initial_height),self._loaded,"Downloading OSM buildings and constructing DHE…")
        self.ttk.Button(top,text="Download",command=fetch).pack(pady=10)

    def _invalidate(self):
        if not hasattr(self,"status"):return
        self.revision+=1
        if self.proposal is not None:
            self.proposal=None
            self._compile(self.session.model)
            self.draw()
            self.status.set("Parameters changed. Preview will be recomputed.")

    def _select(self,indices):
        if self.busy or self.session is None:return
        indices=set(indices)
        missing=indices-set(self.session.state.features)
        if missing:
            self.status.set(f"Unknown or retired indices: {sorted(missing)}");return
        self.selected=indices
        self._invalidate()
        self.syncing=True
        if {int(i) for i in self.tree.selection()} != indices:
            self.tree.selection_set(tuple(map(str,sorted(indices))))
        self.syncing=False
        self.variables["indices"].set(", ".join(map(str,sorted(indices))))
        self.selection_label.configure(text="Selected: "+(", ".join(map(str,sorted(indices))) or "none"))
        self.winner_combo.configure(values=tuple(map(str,sorted(indices))))
        if self.winner.get() not in set(map(str,indices)):
            self.winner.set(str(min(indices)) if indices else "")
        self.draw()

    def _tree_select(self,event=None):
        indices={int(i) for i in self.tree.selection()}
        if not self.syncing and indices != self.selected:self._select(indices)

    def select_indices(self):
        try:self._select(parse_indices(self.variables["indices"].get()))
        except ValueError as exc:self.status.set(str(exc))

    def select_ids(self):
        if not self.session:return
        ids=set(re.split(r"[,;\s]+",self.variables["ids"].get().strip()))
        selected={k for k,f in self.session.state.features.items() if original_id(f.attributes) in ids}
        if not selected:self.status.set("No matching original IDs in the current map.")
        else:self._select(selected)

    def select_all(self):
        if self.session:self._select({k for k,f in self.session.state.features.items() if f.geometry.geom_type=="Polygon"})

    def _map_click(self,event):
        if self.busy or not self.session or event.inaxes is not self.map_ax or event.button!=1 or event.xdata is None:return
        if self.canvas.toolbar and self.canvas.toolbar.mode:return
        from shapely.geometry import Point
        p=Point(event.xdata,event.ydata)
        candidates=[k for k,f in self.session.state.features.items() if f.geometry.covers(p)]
        if candidates:
            k=min(candidates);self._select(self.selected^{k})

    def _rectangle(self,start,end):
        if not self.session or start.xdata is None or end.xdata is None:return
        from shapely.geometry import box
        region=box(min(start.xdata,end.xdata),min(start.ydata,end.ydata),max(start.xdata,end.xdata),max(start.ydata,end.ydata))
        found={k for k,f in self.session.state.features.items() if region.intersects(f.geometry)}
        self._select(self.selected-found if end.key and "shift" in end.key else self.selected|found)

    def compute(self,apply=False):
        if self.busy or not self.session:return
        if apply and self.proposal is not None:
            self._apply(self.proposal);return
        try:
            operation=OPERATIONS[self.operation.get()]
            kwargs=dict(dz=float(self.variables["dz"].get()))
            if operation=="simplification":kwargs["tolerance"]=float(self.variables["tolerance"].get())
            if operation=="smoothing":kwargs.update(iterations=int(self.variables["iterations"].get()),ratio=float(self.variables["ratio"].get()))
            if operation=="merging":kwargs["winner"]=int(self.winner.get())
            selected=tuple(self.selected)
        except ValueError as exc:self.status.set("Invalid parameter: "+str(exc));return
        def complete(proposal):
            if revision!=self.revision:
                self.status.set("Selection or parameters changed during construction. Compute the operation again.")
                return
            if apply:self._apply(proposal)
            else:
                self.proposal=proposal
                self._compile(proposal.model)
                self.draw(fit=True)
                self.status.set("Validated preview. Affected: "+", ".join(map(str,proposal.affected))+". "+" ".join(proposal.notes))
        revision=self.revision
        self._start(lambda:self.session.preview(operation,selected,**kwargs),complete,"Planning, constructing, and validating the complete batch…")

    def _apply(self,proposal):
        self._close_inspector()
        self.session.apply(proposal);self.proposal=None
        self.selected.intersection_update(self.session.state.features)
        self._refresh()
        self.status.set(f"Applied {self.session.state.description}. "+" ".join(proposal.notes))

    def _refresh(self):
        self.syncing=True
        self.tree.delete(*self.tree.get_children())
        for k,f in sorted(self.session.state.features.items()):
            self.tree.insert("","end",iid=str(k),text=str(k),values=(original_id(f.attributes),f.geometry.geom_type))
        self.tree.selection_set(tuple(map(str,self.selected)))
        self.syncing=False
        choices=[f"{i}: {s.description}" for i,s in enumerate(self.session.history)]
        self.history_combo.configure(values=choices);self.history_combo.current(self.session.cursor)
        self._compile(self.session.model)
        self.draw(fit=True)

    def _compile(self,model):
        from plane_slicing import PlaneSlicer
        self.slicer=PlaneSlicer(model)
        self.display_model=model
        self.face_index%=len(model.generalization_face_records)
        self._plane_range(draw=False)

    def _history(self,event=None):
        if self.busy or not self.session:return
        index=self.history_combo.current()
        def done(_):
            self._close_inspector();self.proposal=None;self.selected=set();self._refresh()
            self.status.set(self.session.state.description)
        self._start(lambda:self.session.go(index),done,"Restoring historical DHE model…")

    def undo(self):
        if self.session and self.session.cursor and not self.busy:
            self.history_combo.current(self.session.cursor-1);self._history()

    def _colour(self,index):
        from generalization_geometry import feature_color
        return feature_color(index)

    def _geometry(self,ax,geometry,colour,*,fill=True,linewidth=1.2,alpha=.35,style="-"):
        if geometry.is_empty:return
        if geometry.geom_type=="Polygon":
            x,y=geometry.exterior.xy
            if fill:ax.fill(x,y,color=colour,alpha=alpha)
            ax.plot(x,y,color=colour,lw=linewidth,ls=style)
            for ring in geometry.interiors:ax.plot(*ring.xy,color=colour,lw=linewidth)
        elif geometry.geom_type=="Point":ax.plot(geometry.x,geometry.y,"o",color=colour,ms=6)
        elif geometry.geom_type in ("LineString","LinearRing"):ax.plot(*geometry.xy,color=colour,lw=2)
        elif hasattr(geometry,"geoms"):
            for g in geometry.geoms:self._geometry(ax,g,colour,fill=fill,linewidth=linewidth,alpha=alpha,style=style)

    def draw(self,fit=False):
        if not self.session:return
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        state=self.session.state
        camera=(self.ax.elev,self.ax.azim)
        self.map_ax.clear();self.ax.clear()
        for k,f in state.features.items():
            self._geometry(self.map_ax,f.geometry,self._colour(k),linewidth=2.7 if k in self.selected else 1.,alpha=.65 if k in self.selected else .25)
            p=f.geometry.representative_point();self.map_ax.annotate(str(k),(p.x,p.y),fontsize=9,ha="center",va="center")
        if self.proposal:
            for k,g in self.proposal.targets.items():self._geometry(self.map_ax,g,"black",fill=False,linewidth=1.8,style="--")
        self.map_ax.set(title=f"Current top map · Z={state.z:g} m",xlabel="Local X (m)",ylabel="Local Y (m)")
        self.map_ax.set_aspect("equal",adjustable="box")
        faces=self.display_model.generalization_faces
        grouped=defaultdict(list)
        for face in faces:grouped[face.owner].append(face.points)
        for owner,polygons in grouped.items():
            self.ax.add_collection3d(Poly3DCollection(polygons,facecolors=self._colour(owner),edgecolors=self._colour(owner),alpha=.24 if self.fill.get() else 0.,linewidths=.45))
            if not self.fill.get():
                self.ax.add_collection3d(Poly3DCollection(polygons,facecolors=(0,0,0,0),edgecolors=self._colour(owner),linewidths=.45))
        points=self.slicer.vertices
        low,high=points.min(axis=0),points.max(axis=0)
        pad=(high-low)*.04+.25
        self.ax.set(xlim=(low[0]-pad[0],high[0]+pad[0]),ylim=(low[1]-pad[1],high[1]+pad[1]),zlim=(0,high[2]+pad[2]),xlabel="X (m)",ylabel="Y (m)",zlabel="Z (m)",title="Validated preview" if self.proposal else f"Continuous DHE cells · Z≤{state.z:g} m")
        try:
            exaggeration=float(self.variables['exaggeration'].get())
            if not 0<exaggeration<1000:raise ValueError()
        except ValueError:exaggeration=3.
        self.ax.set_box_aspect((max(high[0]-low[0],1),max(high[1]-low[1],1),max(high[2],1)*exaggeration))
        from matplotlib.ticker import MaxNLocator
        self.ax.zaxis.set_major_locator(MaxNLocator(nbins=4))
        self.ax.view_init(elev=camera[0],azim=camera[1])
        self._highlight()
        self._slice(draw=False)
        self.canvas.draw_idle()

    def _highlight(self):
        import DHECore as dhe
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        face,seed=self.display_model.generalization_face_records[self.face_index]
        edges=dhe.face_edges(seed);self.edge_index%=len(edges)
        edge=edges[self.edge_index]
        self.ax.add_collection3d(Poly3DCollection([face.points],facecolors=self._colour(face.owner),alpha=.5,edgecolors="none"))
        self.ax.plot([edge.V.x,edge.S.V.x],[edge.V.y,edge.S.V.y],[edge.V.z,edge.S.V.z],color="red",lw=2.6)
        self.edge_label.configure(text=f"Feature {face.owner} · face {self.face_index+1}/{len(self.display_model.generalization_face_records)}\nHalf-edge {edge.info} · ID {edge.i}")

    def _face(self,delta):
        if self.session and not self.busy:
            self.face_index=(self.face_index+delta)%len(self.display_model.generalization_face_records);self.edge_index=0;self.draw()

    def _edge(self,delta):
        if self.session and not self.busy:self.edge_index+=delta;self.draw()

    def _plane(self):
        from plane_slicing import SlicePlane
        orientation=self.orientation.get()
        value=self.plane_value.get()
        if orientation=="Horizontal":return SlicePlane.horizontal(value)
        tilt=90. if orientation=="Vertical" else float(self.variables["tilt"].get())
        return SlicePlane.from_angles(self.slicer.center,tilt,float(self.variables["azimuth"].get()),value)

    def _plane_range(self,draw=True):
        if not self.slicer or self.setting_plane:return
        try:
            step=float(self.variables["step"].get())
            from math import isfinite
            if not isfinite(step) or step<=0:raise ValueError("Step size must be positive and finite.")
            if self.orientation.get()=="Horizontal":bounds=(0.,float(self.slicer.maximum[2]))
            else:bounds=self.slicer.offset_bounds(self._plane())
            self.setting_plane=True
            self.slider.configure(from_=bounds[0],to=bounds[1],resolution=step)
            self.plane_value.set(min(bounds[1],max(bounds[0],self.plane_value.get())))
        except ValueError as exc:
            if hasattr(self,"status"):self.status.set(str(exc))
        finally:self.setting_plane=False
        if draw:self._slice()

    def _slice(self,*args,draw=True):
        if self.setting_plane or not self.slicer:return
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        try:
            plane=self._plane()
            sections=self.slicer.sections(plane,include_degenerate=True)
            self.section_ax.clear()
            for owner,geometry in sections.items():self._geometry(self.section_ax,geometry,self._colour(int(owner)))
            self.section_ax.set_aspect("equal",adjustable="box")
            self.section_ax.set_title(f"{self.orientation.get()} section · {'Z' if self.orientation.get()=='Horizontal' else 'offset'}={self.plane_value.get():g} m",fontsize=10)
            if hasattr(self,"plane_artist"):
                try:self.plane_artist.remove()
                except (ValueError,NotImplementedError):pass
                del self.plane_artist
            if self.show_plane.get():
                outline=self.slicer.plane_outline(plane)
                if len(outline):
                    self.plane_artist=Poly3DCollection([outline],facecolors="purple",edgecolors="purple",alpha=.15)
                    self.ax.add_collection3d(self.plane_artist)
            if draw:self.canvas.draw_idle()
        except ValueError as exc:self.status.set("Section: "+str(exc))

    def move_plane(self,direction):
        if not self.slicer:return "break"
        try:
            step=float(self.variables["step"].get())
            from math import isfinite
            if not isfinite(step) or step<=0:
                self.status.set("Step size must be positive and finite.")
                return "break"
            value=self.plane_value.get()+direction*step
            self.plane_value.set(max(float(self.slider.cget("from")),min(float(self.slider.cget("to")),value)))
            self.show_plane.set(True);self._slice()
        except ValueError:pass
        return "break"

    def _wheel(self,event):return self.move_plane(1 if event.delta>0 else -1)

    def _plot_scroll(self,event):
        if event.inaxes is self.section_ax:self.move_plane(1 if event.button=="up" else -1)

    def _key(self,event):
        if event.key in ("up","down"):self.move_plane(1 if event.key=="up" else -1)
        elif event.key in ("left","right"):self._edge(1 if event.key=="right" else -1)

    def inspect(self):
        if not self.session or self.busy or self.proposal:return
        import DHECore as dhe
        from plot_dhe_interactive import interactive_plot_from_lists
        self._close_inspector()
        self.inspector=interactive_plot_from_lists(self.session.model.active_edges(),dhe.Getdhe()[1],show=False,highlight_cells=True)
        self.inspector.fig.show()

    def _close_inspector(self):
        if self.inspector:
            import matplotlib.pyplot as plt
            plt.close(self.inspector.fig);self.inspector=None

    def visualize_transition(self):
        if not self.session or self.busy:return
        from generalization_viewer import TransitionVisualizationWindow
        try:
            self.visualizations=[window for window in self.visualizations if not window.closed]
            window=TransitionVisualizationWindow(self.root,self.session,self.proposal)
            self.visualizations.append(window)
            return window
        except (ValueError,RuntimeError) as exc:self.status.set(str(exc))

    def attributes(self):
        if not self.session:return
        lines=[]
        for k in sorted(self.selected):
            f=self.session.state.features[k]
            lines.append(f"{f.label}\nType: {f.geometry.geom_type}\nLineage: {f.lineage}\n"+"\n".join(f"{key}: {value}" for key,value in f.attributes.items()))
        self._text_dialog("Attributes and lineage","\n\n".join(lines) or "Select a feature first.")

    def diagnostics(self):
        import json
        report=getattr(self.session,'last_diagnostic',None) if self.session else None
        self._text_dialog('Transition diagnostics',json.dumps(report,indent=2,default=str) if report else 'No failed transition has been recorded in this session.')

    def _text_dialog(self,title,text):
        top=self.tk.Toplevel(self.root);top.title(title)
        widget=self.tk.Text(top,width=85,height=24,wrap="word");widget.pack(fill="both",expand=True)
        widget.insert("1.0",text);widget.configure(state="disabled")

    def export(self):
        from tkinter import filedialog,simpledialog
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".png",filetypes=[("PNG","*.png"),("SVG","*.svg"),("PDF","*.pdf")])
        if not path:return
        dpi=simpledialog.askinteger("Export DPI","Raster resolution",initialvalue=300,minvalue=50,parent=self.root)
        if dpi:
            try:
                self.figure.savefig(path,dpi=dpi,bbox_inches="tight")
                self.status.set("Saved "+path)
            except Exception as exc:self.status.set("Export failed: "+str(exc))

    def close(self):
        self.closed=True
        for window in self.visualizations:window.close()
        for identifier in (self.poll_id,self.load_id):
            if identifier:
                self.root.after_cancel(identifier)
        self._close_inspector()
        self.root.destroy()

    def show(self):self.root.mainloop()


def run(path=DEFAULT_DATA,initial_height=5.):
    window=GeneralizationWindow(path,initial_height=initial_height)
    window.show()
    return window.session


def render_overview(session):
    """Headless export of actual current top geometries and realized DHE faces."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from generalization_geometry import feature_color
    figure=Figure(figsize=(15,8),layout="constrained")
    FigureCanvasAgg(figure)
    ax2=figure.add_subplot(121);ax3=figure.add_subplot(122,projection="3d")
    for k,f in session.state.features.items():
        color=feature_color(k)
        geometry=f.geometry
        if geometry.geom_type=="Polygon":
            ax2.fill(*geometry.exterior.xy,color=color,alpha=.4)
            ax2.plot(*geometry.exterior.xy,color=color)
        elif geometry.geom_type=="Point":ax2.plot(geometry.x,geometry.y,"o",color=color)
        else:ax2.plot(*geometry.xy,color=color,lw=2)
        p=geometry.representative_point();ax2.annotate(str(k),(p.x,p.y),fontsize=8)
    for owner in {f.owner for f in session.state.faces}:
        ax3.add_collection3d(Poly3DCollection([f.points for f in session.state.faces if f.owner==owner],facecolors=feature_color(owner),edgecolors=feature_color(owner),alpha=.35,linewidths=.4))
    import numpy as np
    vertices=np.array([p for f in session.state.faces for p in f.points]);lo,hi=vertices.min(axis=0),vertices.max(axis=0)
    ax3.set(xlim=(lo[0]-1,hi[0]+1),ylim=(lo[1]-1,hi[1]+1),zlim=(0,hi[2]+1),xlabel="X (m)",ylabel="Y (m)",zlabel="Z (m)")
    ax3.set_box_aspect((max(hi[0]-lo[0],1),max(hi[1]-lo[1],1),max(hi[2],1)*3))
    from matplotlib.ticker import MaxNLocator
    ax3.zaxis.set_major_locator(MaxNLocator(nbins=4))
    ax2.set_aspect("equal");ax2.set(title=f"Top map · Z={session.state.z:g} m",xlabel="Local X (m)",ylabel="Local Y (m)")
    figure.suptitle(session.state.description)
    return figure
