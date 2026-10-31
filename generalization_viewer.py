"""Independent Tk publication viewer; owns no live DHE objects."""
from dataclasses import replace
import json
import math

from generalization_visualization import (TransitionSnapshot, ViewSettings, current_plane,
    default_publication_settings,export_transition, render_slices, render_transition, view_bounds, visible_owners)
from methodology_publication import PublicationSettings,scoped_canvas


class TransitionVisualizationWindow:
    def __init__(self,parent,session,proposal=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk,self.ttk=tk,ttk
        self.root=tk.Toplevel(parent);self.root.title('DHE · Transition visualization and publication export')
        self.root.geometry('1600x1000');self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.snapshots=[]
        for i,state in enumerate(session.history[:session.cursor+1]):
            if state.operation:self.snapshots.append(TransitionSnapshot.capture(state,session.history[:i]))
        if proposal:self.snapshots.append(TransitionSnapshot.capture(proposal.state,session.history[:session.cursor+1]))
        if not self.snapshots:
            self.root.destroy();raise ValueError('Preview or apply a generalization operation first.')
        self.closed=False;self.figure=None;self.canvas=None;self.toolbar=None;self.setting=False
        self.settings=default_publication_settings();self.slicers={};self.variables={};self.hidden=set()
        self.status=tk.StringVar(value='Read-only geometry · display cut caps do not modify the DHE model.')
        body=ttk.Panedwindow(self.root,orient='horizontal');body.pack(fill='both',expand=True)
        side=ttk.Frame(body,width=310);body.add(side,weight=0)
        scroll=tk.Canvas(side,width=300,highlightthickness=0);bar=ttk.Scrollbar(side,command=scroll.yview)
        scroll.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');scroll.pack(fill='both',expand=True)
        self.controls=ttk.Frame(scroll,padding=8);item=scroll.create_window((0,0),window=self.controls,anchor='nw')
        self.controls.bind('<Configure>',lambda e:scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>',lambda e:scroll.itemconfigure(item,width=e.width))
        self.plot_frame=ttk.Frame(body);body.add(self.plot_frame,weight=1)
        self.operation=tk.StringVar();self.operation_menu=ttk.Combobox(self.controls,textvariable=self.operation,state='readonly',
            values=[f'{i+1}: {s.label}'+(' · preview' if proposal and i==len(self.snapshots)-1 else '') for i,s in enumerate(self.snapshots)])
        self.operation_menu.pack(fill='x');self.operation_menu.current(len(self.snapshots)-1)
        self.operation_menu.bind('<<ComboboxSelected>>',self._change_operation)
        self.scope=self.combo('Features',('Latest affected features','All features'),'Latest affected features',self._scope_changed)
        self.extent=self.combo('Vertical extent',('Latest interval Zb–Zt','Total history'),'Latest interval Zb–Zt',self._extent_changed)
        self.entry('Display lower level Zb','zb',self.snapshot.operation['z_b'])
        self.feature_list=tk.Listbox(self.controls,selectmode='extended',exportselection=False,height=5)
        self.feature_list.pack(fill='x');ttk.Button(self.controls,text='Hide selected features',command=self.hide_selected).pack(fill='x')
        ttk.Button(self.controls,text='Show all eligible features',command=self.show_all).pack(fill='x')
        self.opacity=self.entry('Face opacity (0–1)','opacity',.4)
        for title,key,default in [('Elevation','elevation',25),('Azimuth','azimuth',-60),('Zoom','zoom',1),('Z display factor','exaggeration',1)]:self.entry(title,key,default)
        self.outlines=tk.BooleanVar(value=True);self.legend=tk.BooleanVar(value=True);self.caps=tk.BooleanVar(value=True)
        for title,var in [('Source / target outlines',self.outlines),('Legend',self.legend),('Close display cuts (illustration)',self.caps)]:
            ttk.Checkbutton(self.controls,text=title,variable=var,command=self.redraw).pack(anchor='w')
        ttk.Button(self.controls,text='Update view / camera',command=self._set_camera).pack(fill='x',pady=4)
        self.orientation=self.combo('Plane orientation',('Horizontal','Vertical','Inclined'),'Horizontal',self._orientation_changed)
        self.entry('Tilt from horizontal (degrees)','tilt',45)
        self.entry('Plane azimuth (degrees)','plane_azimuth',0)
        ttk.Button(self.controls,text='Update plane angles',command=self._update_angles).pack(fill='x')
        self.step=self.entry('Step size','step',.1)
        self.step.trace_add('write',lambda *a:self._step_changed())
        self.value=tk.DoubleVar(value=self.snapshot.operation['z_s'])
        self.last_plane_value=self.value.get()
        self.slider=tk.Scale(self.controls,variable=self.value,orient='horizontal',resolution=.1,
            command=lambda v:self._slide(),length=275,digits=8)
        self.slider.pack(fill='x');self.slider.bind('<MouseWheel>',self._wheel)
        row=ttk.Frame(self.controls);row.pack(fill='x')
        ttk.Button(row,text='Lower',command=lambda:self.move(-1)).pack(side='left',expand=True,fill='x')
        ttk.Button(row,text='Raise',command=lambda:self.move(1)).pack(side='left',expand=True,fill='x')
        self.show_plane=tk.BooleanVar(value=False)
        ttk.Checkbutton(self.controls,text='Show plane in 3D',variable=self.show_plane,command=self.redraw).pack(anchor='w')
        self.presentation=self.combo('Figure',('3D + live section','3D figure','2D slice grid','3D slice grid'),'3D + live section',lambda e:self.redraw())
        self.count=self.combo('Slice count',('6','9'),'9',lambda e:self.redraw())
        ttk.Label(self.controls,text='Exports: SVG + PDF + PNG',font=('TkDefaultFont',10,'bold')).pack(anchor='w',pady=(8,0))
        for title,key,default in [('Width (inches)','width',10),('Height (inches)','height',8),('DPI (300, 600, 1200, …)','dpi',600),
                                  ('Pixel width (blank = physical)','pixels_w',''),('Pixel height (optional)','pixels_h','')]:self.entry(title,key,default)
        ttk.Button(self.controls,text='Fonts / publication settings…',command=self.edit_settings).pack(fill='x')
        self.individual=tk.BooleanVar(value=False)
        ttk.Checkbutton(self.controls,text='Also export individual slice panels',variable=self.individual).pack(anchor='w')
        ttk.Button(self.controls,text='Export clean 3D figure…',command=lambda:self.export('scene')).pack(fill='x',pady=3)
        ttk.Button(self.controls,text='Export 2D slice grid…',command=lambda:self.export('2d')).pack(fill='x')
        ttk.Button(self.controls,text='Export 3D slice grid…',command=lambda:self.export('3d')).pack(fill='x',pady=3)
        ttk.Label(self.root,textvariable=self.status,wraplength=1450).pack(fill='x',padx=8,pady=4)
        self.root.bind('<Up>',lambda e:self.move(1));self.root.bind('<Down>',lambda e:self.move(-1))
        self._update_feature_list();self._range();self.redraw()

    @property
    def snapshot(self):return self.snapshots[self.operation_menu.current()]

    @property
    def slicer(self):
        index=self.operation_menu.current()
        if index not in self.slicers:self.slicers[index]=self.snapshot.slicer()
        return self.slicers[index]

    def entry(self,title,key,value):
        self.ttk.Label(self.controls,text=title).pack(anchor='w')
        var=self.tk.StringVar(value=str(value));self.variables[key]=var
        widget=self.ttk.Entry(self.controls,textvariable=var);widget.pack(fill='x')
        return var

    def combo(self,title,values,value,command):
        self.ttk.Label(self.controls,text=title).pack(anchor='w')
        var=self.tk.StringVar(value=value)
        widget=self.ttk.Combobox(self.controls,textvariable=var,values=values,state='readonly');widget.pack(fill='x')
        widget.bind('<<ComboboxSelected>>',command);return var

    def options(self,*,camera=True,limits=True):
        elevation=float(self.variables['elevation'].get());azimuth=float(self.variables['azimuth'].get())
        axis_limits=None
        if camera and self.figure and self.presentation.get() in ('3D + live section','3D figure'):
            ax=self.figure.axes[0]
            if hasattr(ax,'elev'):
                elevation,azimuth=ax.elev,ax.azim
                if limits:axis_limits=(ax.get_xlim(),ax.get_ylim(),ax.get_zlim())
        return ViewSettings(scope='all' if self.scope.get()=='All features' else 'affected',
            extent='history' if self.extent.get()=='Total history' else 'interval',
            z_b=float(self.variables['zb'].get()),hidden=tuple(sorted(self.hidden)),opacity=float(self.opacity.get()),
            elevation=elevation,azimuth=azimuth,zoom=float(self.variables['zoom'].get()),
            exaggeration=float(self.variables['exaggeration'].get()),outlines=self.outlines.get(),legend=self.legend.get(),
            axis_limits=axis_limits,
            cut_caps=self.caps.get(),plane_visible=self.show_plane.get(),orientation=self.orientation.get(),
            value=self.value.get(),tilt=float(self.variables['tilt'].get()),plane_azimuth=float(self.variables['plane_azimuth'].get()))

    def publication_settings(self):
        settings=self.settings.copy()
        settings['layout'].update(width_in=float(self.variables['width'].get()),height_in=float(self.variables['height'].get()))
        settings['export']['dpi']=float(self.variables['dpi'].get())
        width=self.variables['pixels_w'].get().strip();height=self.variables['pixels_h'].get().strip()
        settings['export'].update(size_mode='pixels' if width or height else 'physical',
            width_px=int(width) if width else None,height_px=int(height) if height else None)
        settings.validate();return settings

    def _change_operation(self,event=None):
        self.hidden.clear();self.variables['zb'].set(str(self.snapshot.operation['z_b']))
        self.value.set(self.snapshot.operation['z_s'] if self.orientation.get()=='Horizontal' else 0.)
        self._update_feature_list();self._range();self.redraw(fit=True)

    def _scope_changed(self,event=None):
        self.hidden.clear();self._update_feature_list();self._range();self.redraw(fit=True)

    def _extent_changed(self,event=None):self._range();self.redraw(fit=True)

    def _update_feature_list(self):
        self.feature_list.delete(0,'end')
        owners=sorted(visible_owners(self.snapshot,ViewSettings(scope='all' if self.scope.get()=='All features' else 'affected')))
        self.feature_owners=owners;names=dict(self.snapshot.names)
        for owner in owners:self.feature_list.insert('end',names.get(owner,f'Feature {owner}')+(' · hidden' if owner in self.hidden else ''))

    def hide_selected(self):
        self.hidden.update(self.feature_owners[i] for i in self.feature_list.curselection());self._update_feature_list();self._range();self.redraw(fit=True)

    def show_all(self):self.hidden.clear();self._update_feature_list();self._range();self.redraw(fit=True)

    def _step_value(self):
        step=float(self.step.get())
        if not math.isfinite(step) or step<=0:raise ValueError('Step size must be finite and positive.')
        return step

    def _step_changed(self):
        try:self.slider.configure(resolution=self._step_value())
        except (ValueError,AttributeError):pass

    def _range(self):
        self.setting=True
        try:
            options=self.options();lo,hi=view_bounds(self.snapshot,options)
            if options.orientation!='Horizontal':
                import numpy as np
                plane=current_plane(self.snapshot,self.slicer,replace(options,value=0.))
                points=np.array([p for f in self.snapshot.faces if f.owner in visible_owners(self.snapshot,options) for p in f.points])
                points[:,2]=np.clip(points[:,2],lo,hi);distances=plane.signed_distance(points);lo,hi=distances.min(),distances.max()
            self.slider.configure(from_=lo,to=hi,resolution=self._step_value())
            self.value.set(max(lo,min(hi,self.value.get())))
        except (ValueError,IndexError) as exc:self.status.set(str(exc))
        finally:
            self.last_plane_value=self.value.get();self.setting=False

    def _orientation_changed(self,event=None):
        self.value.set(self.snapshot.operation['z_s'] if self.orientation.get()=='Horizontal' else 0.)
        self._range();self.redraw()

    def _update_angles(self):self._range();self.redraw()

    def _slide(self):
        if not self.setting and self.value.get()!=self.last_plane_value:
            self.last_plane_value=self.value.get();self.show_plane.set(True);self.redraw()

    def move(self,direction):
        try:
            value=self.value.get()+direction*self._step_value()
            self.setting=True
            self.value.set(max(float(self.slider.cget('from')),min(float(self.slider.cget('to')),value)))
            self.show_plane.set(True)
        except ValueError as exc:
            self.status.set(str(exc));return 'break'
        finally:
            self.last_plane_value=self.value.get();self.setting=False
        self.redraw();return 'break'

    def _wheel(self,event):return self.move(1 if event.delta>0 else -1)

    def _set_camera(self):
        self._range();self.redraw(camera=False,fit=True)

    def redraw(self,*,camera=True,fit=False):
        if self.closed or self.setting:return
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
            options=self.options(camera=camera,limits=not fit);settings=self.publication_settings()
            # Preview dimensions stay usable; requested pixel sizing is for exports.
            settings['export']['size_mode']='physical'
            presentation=self.presentation.get()
            if presentation in ('2D slice grid','3D slice grid'):
                figure=render_slices(self.snapshot,int(self.count.get()),'2d' if presentation=='2D slice grid' else '3d',options,settings,slicer=self.slicer)
            else:figure=render_transition(self.snapshot,options,settings,with_section=presentation=='3D + live section',slicer=self.slicer)
            if self.figure:self.figure.clear()
            if self.toolbar:self.toolbar.destroy()
            if self.canvas:
                self._cancel_canvas_draw()
                self.canvas.get_tk_widget().destroy()
            self.figure=figure;self.canvas=FigureCanvasTkAgg(figure,master=self.plot_frame)
            scoped_canvas(self.canvas,settings)
            self.toolbar=NavigationToolbar2Tk(self.canvas,self.plot_frame,pack_toolbar=False);self.toolbar.pack(side='bottom',fill='x')
            self.canvas.get_tk_widget().pack(fill='both',expand=True)
            self.canvas.mpl_connect('scroll_event',lambda e:self.move(1 if e.button=='up' else -1))
            self.canvas.mpl_connect('key_press_event',lambda e:self.move(1 if e.key=='up' else -1) if e.key in ('up','down') else None)
            self.canvas.draw_idle()
            lo,hi=view_bounds(self.snapshot,options)
            self.status.set(f'Read-only · Zb={lo:g}, Zs={self.snapshot.operation["z_s"]:g}, Zt={hi:g} · plane value={self.value.get():g}. Display cut caps are illustrations.')
        except (ValueError,RuntimeError,OverflowError) as exc:self.status.set(str(exc))

    def edit_settings(self):
        try:current=self.publication_settings()
        except ValueError as exc:self.status.set(str(exc));return
        dialog=self.tk.Toplevel(self.root);dialog.title('Publication settings · editable JSON')
        text=self.tk.Text(dialog,width=90,height=32);text.pack(fill='both',expand=True)
        text.insert('1.0',json.dumps(current.values,indent=2))
        def apply():
            try:
                self.settings=PublicationSettings(json.loads(text.get('1.0','end')))
                for key,value in [('width',self.settings['layout']['width_in']),('height',self.settings['layout']['height_in'] or 8),
                                  ('dpi',self.settings['export']['dpi']),('pixels_w',self.settings['export']['width_px'] or ''),
                                  ('pixels_h',self.settings['export']['height_px'] or '')]:self.variables[key].set(str(value))
                dialog.destroy();self.redraw()
            except (ValueError,TypeError) as exc:self.status.set(str(exc))
        self.ttk.Button(dialog,text='Apply settings',command=apply).pack(fill='x')

    def export(self,content):
        from tkinter import filedialog
        output=filedialog.askdirectory(parent=self.root,title='Choose publication export folder')
        if not output:return
        try:
            self.status.set('Exporting SVG, PDF and PNG…');self.root.update_idletasks()
            paths,record=export_transition(self.snapshot,output,options=self.options(),settings=self.publication_settings(),
                count=int(self.count.get()),kind='3d' if content=='3d' else '2d',individual=self.individual.get(),
                content='scene' if content=='scene' else 'slices',slicer=self.slicer)
            warnings=[v['reason'] for row in record['exports'] for v in row['formats'].values() if v['status']!='exported']
            self.status.set(f'Exported {len(paths)} files and reproducible JSON settings to {output}. '+' '.join(warnings))
        except (ValueError,RuntimeError,OSError,MemoryError,OverflowError) as exc:self.status.set('Export failed: '+str(exc))

    def close(self):
        if self.closed:return
        self.closed=True
        self._cancel_canvas_draw()
        if self.figure:self.figure.clear()
        self.slicers.clear();self.root.destroy()

    def _cancel_canvas_draw(self):
        if self.canvas and self.canvas._idle_draw_id:
            self.canvas.get_tk_widget().after_cancel(self.canvas._idle_draw_id)
            self.canvas._idle_draw_id=None
