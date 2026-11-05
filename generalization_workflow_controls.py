"""Desktop draft-level editor and final-only automatic-run controls."""
from dataclasses import asdict
import json
import threading

from automatic_generalization import AutomaticSettings,build_automatic_run,apply_automatic_run
from generalization_levels import normalize_actions


class WorkflowControls:
    def __init__(self,host):
        self.host=host;self.actions=[];self.cancel=threading.Event();self.automatic_running=False
        self.progress_message='';self.settings=AutomaticSettings();self.report=None
        tk,ttk=host.tk,host.ttk
        group=host._group('Level operations · one ΔZ')
        self.list=tk.Listbox(group,height=4,exportselection=False);self.list.pack(fill='x')
        self.list.bind('<<ListboxSelect>>',self.edit)
        self.buttons=[]
        for label,command in [('Add current selection',self.add),('Update selected action',self.update),('Remove action',self.remove),('Clear level',self.clear)]:
            button=ttk.Button(group,text=label,command=command);button.pack(fill='x');self.buttons.append(button)
        ttk.Label(group,text='One operation per source feature. Preview / Apply uses this table when it is nonempty.',wraplength=280).pack(fill='x')
        group=host._group('Automatic generalization')
        for label,command in [('Automatic settings / Run…',self.dialog),('Run summary…',self.summary)]:
            button=ttk.Button(group,text=label,command=command);button.pack(fill='x');self.buttons.append(button)
        self.cancel_button=ttk.Button(group,text='Cancel automatic run',command=self.cancel.set);self.cancel_button.pack(fill='x')
        self.cancel_button.configure(state='disabled')

    def current(self):
        h=self.host
        from generalization_session import OPERATIONS
        return dict(operation=OPERATIONS[h.operation.get()],selected=sorted(h.selected),
            tolerance=float(h.variables['tolerance'].get()),iterations=int(h.variables['iterations'].get()),
            ratio=float(h.variables['ratio'].get()),winner=int(h.winner.get()) if h.operation.get()=='Merging' else None)

    def changed(self):
        self.list.delete(0,'end')
        for a in self.actions:self.list.insert('end',f"{a['operation']}: {', '.join(map(str,a['selected']))}"+(f" → {a['winner']}" if a['winner'] else ''))
        self.host._invalidate()

    def add(self):
        if self.host.busy or not self.host.session:return
        try:self.actions=normalize_actions([*self.actions,self.current()],self.host.session.state);self.changed()
        except ValueError as exc:self.host.status.set(str(exc))

    def edit(self,event=None):
        if self.host.busy or not self.list.curselection():return
        a=self.actions[self.list.curselection()[0]];h=self.host
        from generalization_session import OPERATIONS
        h.operation.set(next(k for k,v in OPERATIONS.items() if v==a['operation']));h._select(set(a['selected']))
        for key in ('tolerance','iterations','ratio'):h.variables[key].set(str(a[key]))
        if a['winner'] is not None:h.winner.set(str(a['winner']))

    def update(self):
        if self.host.busy or not self.list.curselection():return
        try:
            actions=list(self.actions);actions[self.list.curselection()[0]]=self.current()
            self.actions=normalize_actions(actions,self.host.session.state);self.changed()
        except ValueError as exc:self.host.status.set(str(exc))

    def remove(self):
        if self.host.busy or not self.list.curselection():return
        del self.actions[self.list.curselection()[0]];self.changed()

    def clear(self,notify=True):
        if notify and self.host.busy:return
        self.actions=[];self.list.delete(0,'end')
        if notify:self.host._invalidate()

    def busy(self,value):
        for button in self.buttons:button.configure(state='disabled' if value else 'normal')
        self.cancel_button.configure(state='normal' if value and self.automatic_running else 'disabled')

    def dialog(self):
        h=self.host
        if h.busy or not h.session:return
        tk,ttk=h.tk,h.ttk
        top=tk.Toplevel(h.root);top.title('Automatic generalization · final result only');top.geometry('580x780')
        ttk.Label(top,text='Selected polygons become one current feature. Levels are built privately; failure or cancellation keeps the original map.',wraplength=540).pack(padx=15,pady=10)
        notebook=ttk.Notebook(top);notebook.pack(fill='both',expand=True,padx=12)
        basic=ttk.Frame(notebook,padding=10);advanced=ttk.Frame(notebook,padding=10)
        notebook.add(basic,text='Run');notebook.add(advanced,text='Advanced rules')
        values={}
        for key,value in asdict(self.settings).items():
            parent=basic if key in ('scale','scale_multiplier','dz','final_kind','class_field','max_levels') else advanced
            row=ttk.Frame(parent);row.pack(fill='x',pady=3)
            ttk.Label(row,text=key.replace('_',' '),width=26).pack(side='left')
            values[key]=tk.StringVar(value=str(value))
            if key=='final_kind':widget=ttk.Combobox(row,textvariable=values[key],values=('automatic','polygon','point','polyline'),state='readonly')
            elif key=='class_field':
                fields=sorted({str(k) for f in h.session.state.features.values() for k in f.attributes})
                widget=ttk.Combobox(row,textvariable=values[key],values=('',*fields),state='readonly')
            else:widget=ttk.Entry(row,textvariable=values[key])
            widget.pack(side='right',fill='x',expand=True)
        message=tk.StringVar();ttk.Label(top,textvariable=message,wraplength=540).pack(padx=12)
        def run():
            try:
                settings={}
                for key,var in values.items():
                    default=getattr(self.settings,key)
                    settings[key]=var.get() if isinstance(default,str) else int(var.get()) if isinstance(default,int) else float(var.get())
                self.settings=AutomaticSettings(**settings);self.settings.validate()
                if not h.selected:raise ValueError('Select current polygons first.')
                top.destroy();self.run()
            except ValueError as exc:message.set(str(exc))
        ttk.Button(top,text='Run all levels · show final result',command=run).pack(fill='x',padx=12,pady=10)

    def run(self):
        h=self.host
        if h.busy or not h.session:return
        selected=tuple(h.selected);self.cancel.clear();self.automatic_running=True;self.progress_message='Analyzing selected polygons…'
        def work():
            try:return build_automatic_run(h.session,selected,self.settings,cancelled=self.cancel.is_set,
                progress=lambda text:setattr(self,'progress_message',text))
            finally:self.automatic_running=False
        def done(run):
            if self.cancel.is_set():h.status.set('Cancelled; original map retained.');return
            h._close_inspector();apply_automatic_run(h.session,run);h.proposal=None;self.report=run.report
            h.selected={run.report['final_index']};h._refresh()
            h.status.set(f"Automatic run complete: {len(run.states)} levels; final feature {run.report['final_index']} ({run.report['final_geometry']}).")
            if h.evaluation_window and not h.evaluation_window.closed:h.evaluation_window.on_history_changed()
            self.summary()
        h._start(work,done,'Constructing all automatic levels privately…')

    def summary(self):
        h=self.host
        if h.busy:return
        report=self.report or getattr(h.session,'last_automatic_report',None)
        if not report:h.status.set('No completed automatic run.');return
        top=h.tk.Toplevel(h.root);top.title('Automatic run · decisions and levels');top.geometry('950x500')
        text=h.tk.Text(top,wrap='none');text.pack(fill='both',expand=True)
        text.insert('end',f"Final feature {report['final_index']} · {report['final_geometry']} · scale 1:{report['final_scale']:g}\n")
        for i,step in enumerate(report['steps'],1):
            text.insert('end',f"\nLevel {i}: Z {step['z_s']:g}–{step['z_t']:g} · 1:{step['scale']:g}\n")
            for a in step['actions']:text.insert('end',f"  {a['operation']} {a['selected']} · score {a['score']:.4g} · {a['reason']}\n")
            text.insert('end',f"  Affected context: {step['affected']}\n")
        text.insert('end',f"\nRejected candidate sets: {len(report['rejections'])}\nResidual cartographic violation: {report['remaining_cartographic_violation']:.4g}\n")
        text.configure(state='disabled')
        def save():
            from tkinter import filedialog
            path=filedialog.asksaveasfilename(parent=top,defaultextension='.json',filetypes=[('Run summary','*.json')])
            if path:
                from pathlib import Path
                Path(path).write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        h.ttk.Button(top,text='Save decision summary…',command=save).pack()
