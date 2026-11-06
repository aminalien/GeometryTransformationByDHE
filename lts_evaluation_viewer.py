"""Manual LTS analysis and explicit export; opening the window measures nothing."""
import json
from pathlib import Path

from lts_evaluation import evaluate_history,load_reports,save_report,profile_figure


class EvaluationWindow:
    def __init__(self,workspace):
        import tkinter as tk
        from tkinter import ttk
        self.workspace=workspace;self.closed=False;self.record=None;self.records=[];self.tables={}
        self.root=tk.Toplevel(workspace.root);self.root.title('Quantitative Evaluation - DHE LTS')
        self.root.geometry('1250x720');self.root.protocol('WM_DELETE_WINDOW',self.close)
        body=ttk.Frame(self.root,padding=10);body.pack(fill='both',expand=True)
        row=ttk.Frame(body);row.pack(fill='x')
        self.mode=tk.StringVar(value='Last operation')
        ttk.Combobox(row,textvariable=self.mode,values=('Last operation','Whole history'),state='readonly',width=20).pack(side='left')
        ttk.Button(row,text='Evaluate',command=self.run).pack(side='left',padx=5)
        ttk.Button(row,text='Save results',command=self.save).pack(side='left')
        ttk.Button(row,text='Plot / export profiles',command=self.plot).pack(side='left',padx=5)
        ttk.Label(row,text='Result scope').pack(side='left')
        self.scope=tk.StringVar(value='All');self.scope_menu=ttk.Combobox(row,textvariable=self.scope,state='readonly',width=20)
        self.scope_menu.pack(side='left');self.scope_menu.bind('<<ComboboxSelected>>',lambda e:self.display())
        self.step=tk.StringVar(value='All');self.step_menu=ttk.Combobox(row,textvariable=self.step,state='readonly',width=8)
        self.step_menu.pack(side='left',padx=5);self.step_menu.bind('<<ComboboxSelected>>',lambda e:self.display())
        selection=ttk.LabelFrame(body,text='Features to evaluate',padding=5);selection.pack(fill='x',pady=5)
        choices=ttk.Frame(selection);choices.pack(fill='x')
        self.feature_mode=tk.StringVar(value='All features')
        choice=ttk.Combobox(choices,textvariable=self.feature_mode,values=('All features','Choose features'),state='readonly',width=20)
        choice.pack(side='left');choice.bind('<<ComboboxSelected>>',lambda e:self._feature_mode_changed())
        ttk.Button(choices,text='Use workspace selection',command=self.use_workspace_selection).pack(side='left',padx=5)
        ttk.Label(choices,text='Custom selection follows complete merge/aggregation families; repeated families are counted once.').pack(side='left')
        self.feature_list=tk.Listbox(selection,selectmode='extended',exportselection=False,height=4)
        scrollbar=ttk.Scrollbar(selection,command=self.feature_list.yview)
        self.feature_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.feature_list.pack(fill='x',expand=True)
        self.feature_indices=[];self.feature_session=workspace.session;self._refresh_features()
        row=ttk.Frame(body);row.pack(fill='x',pady=5)
        ttk.Entry(row,textvariable=workspace.variables['evaluation_folder']).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Browse save/review folder',command=self.browse).pack(side='left',padx=4)
        ttk.Button(row,text='Load saved reports',command=self.refresh).pack(side='left')
        self.menu=ttk.Combobox(body,state='readonly');self.menu.pack(fill='x')
        self.menu.bind('<<ComboboxSelected>>',lambda e:self.show(self.records[self.menu.current()]))
        self.info=ttk.Label(body,text='Choose a mode and click Evaluate. Apply never runs quantitative analysis.',wraplength=1200)
        self.info.pack(fill='x',pady=7)
        book=ttk.Notebook(body);book.pack(fill='both',expand=True)
        for key,title in (('summary','Endpoint / history summary'),('sections','11-level section profiles'),('structure','Complexity / validation')):
            frame=ttk.Frame(book);book.add(frame,text=title)
            table=ttk.Treeview(frame,show='headings');x=ttk.Scrollbar(frame,orient='horizontal',command=table.xview)
            y=ttk.Scrollbar(frame,command=table.yview);table.configure(xscrollcommand=x.set,yscrollcommand=y.set)
            table.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
            frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1);self.tables[key]=table
            table.bind('<<TreeviewSelect>>',lambda e,k=key:self.row_details(k))
        self.status=ttk.Label(body,text='Results remain in memory until Save. Geometry metrics use actual DHE sections.',wraplength=1200)
        self.status.pack(fill='x',pady=7)

    def browse(self):
        from tkinter import filedialog
        path=filedialog.askdirectory(parent=self.root,title='Choose evaluation save/review folder')
        if path:self.workspace.variables['evaluation_folder'].set(path)

    def _feature_mode_changed(self):
        self.feature_list.configure(state='normal' if self.feature_mode.get()=='Choose features' else 'disabled')

    def _refresh_features(self):
        selected={self.feature_indices[i] for i in self.feature_list.curselection()}
        s=self.workspace.session;features={}
        if s is not self.feature_session:selected=set()
        self.feature_session=s
        if s:
            for state in s.history[:s.cursor+1]:features.update(state.features)
        self.feature_indices=sorted(features)
        self.feature_list.configure(state='normal');self.feature_list.delete(0,'end')
        for i,k in enumerate(self.feature_indices):
            f=features[k];status='current' if k in s.state.features else 'retired'
            self.feature_list.insert('end',f'{k}: {f.label} | {f.geometry.geom_type} | {status}')
            if k in selected:self.feature_list.selection_set(i)
        self._feature_mode_changed()

    def use_workspace_selection(self):
        self._refresh_features();self.feature_mode.set('Choose features');self._feature_mode_changed()
        self.feature_list.selection_clear(0,'end')
        for i,k in enumerate(self.feature_indices):
            if k in self.workspace.selected:self.feature_list.selection_set(i)

    def run(self):
        w=self.workspace
        if w.busy or not w.session:return
        self._refresh_features()
        feature_ids=None
        if self.feature_mode.get()=='Choose features':
            feature_ids=tuple(self.feature_indices[i] for i in self.feature_list.curselection())
            if not feature_ids:
                self.status.configure(text='Choose at least one feature before evaluating.');return
        mode='last_operation' if self.mode.get()=='Last operation' else 'whole_history'
        states=tuple(w.session.history[:w.session.cursor+1]);model=w.session.model
        dataset=dict(w.evaluation_journal.dataset);session_id=w.evaluation_journal.session_id
        def measure():
            try:return evaluate_history(states,len(states)-1,mode,model,dataset,session_id,feature_ids=feature_ids)
            except Exception as exc:return dict(_analysis_error=f'{type(exc).__name__}: {exc}')
        def done(record):
            if '_analysis_error' in record:
                if not self.closed:self.status.configure(text='Evaluation failed: '+record['_analysis_error'])
                w.status.set('Evaluation unavailable: '+record['_analysis_error'])
                return
            w.evaluation_journal.complete(record)
            if not self.closed:self.show(record)
            w.status.set('Quantitative evaluation complete. No files written; choose Save to export.')
        w._start(measure,done,'Evaluating the requested applied-history snapshot…')

    def show(self,record):
        self.record=record;legacy=record.get('schema_version')==1
        self.scope_menu.configure(values=('All',)+tuple(sorted({r['scope'] for r in record.get('tables',{}).get('summary',record.get('metrics',[]))})))
        self.step_menu.configure(values=('All',)+tuple(str(v) for v in sorted({r['step'] for r in record.get('tables',{}).get('summary',[])})))
        self.scope.set('All');self.step.set('All')
        self.info.configure(text=f"{'Legacy automatic evaluation' if legacy else record['mode']} | {record['dataset']['source']} | end step {record.get('end_index',record.get('history_step'))} | {record['evaluation_id']}")
        selection=record.get('feature_selection')
        if selection:
            self.info.configure(text=self.info.cget('text')+f"\nRequested IDs: {selection['requested_ids']}; additional family cell IDs: {selection['expanded_cell_ids']}. Full-complex live validation is global context.")
        if not legacy and any(r['missing_logical_features'] for r in record['tables']['structure']):
            self.info.configure(text=self.info.cget('text')+'\nSome logical features are absent from actual DHE sections; see Complexity / validation and the report.')
        self.display();self.status.configure(text='Saved: '+record['_path'] if record.get('_path') else 'Measured result is not saved. Click Save results to export tables and explanatory report.')

    def display(self):
        if not self.record:return
        preferred={
            'summary':('step','scope','z','geometry_type','area_m2','area_change','area_change_pct','perimeter_m','perimeter_change','perimeter_change_pct','hausdorff_m','normalized_hausdorff','operation_descriptor','descriptor_value'),
            'sections':('step','scope','t','z','geometry_type','area_m2','area_change','area_change_pct','perimeter_m','perimeter_change','perimeter_change_pct','hausdorff_m','normalized_hausdorff'),
            'structure':('step','scope','z','retained_vertices','retained_edges','retained_faces','retained_cells','top_supporting_vertices','top_supporting_edges','top_supporting_faces','top_supporting_cells','missing_edges','nonmanifold_edges','misoriented_edges','invalid_closed_cells','inconsistent_interfaces','missing_terminal_features','live_dual_status','live_validation_scope')}
        for key,table in self.tables.items():
            rows=self.record.get('tables',{}).get(key,[])
            if self.record.get('schema_version')==1 and key=='summary':rows=self.record['metrics']
            rows=[r for r in rows if (self.scope.get()=='All' or 'scope' not in r or r['scope']==self.scope.get()) and (self.step.get()=='All' or 'step' not in r or str(r['step'])==self.step.get())]
            columns=preferred[key] if self.record.get('schema_version')==2 else ('scope','label','value','unit','reason')
            table.delete(*table.get_children());table.configure(columns=columns)
            for col in columns:table.heading(col,text=col.replace('_',' '));table.column(col,width=145,stretch=False)
            for i,r in enumerate(rows):
                def text(v):return 'N/A' if v is None else f'{v:.8g}' if isinstance(v,float) else str(v)
                table.insert('','end',iid=str(i),values=tuple(text(r.get(col)) for col in columns))
            table._evaluation_rows=rows

    def row_details(self,key):
        table=self.tables[key]
        if table.selection():self.status.configure(text=json.dumps(table._evaluation_rows[int(table.selection()[0])],ensure_ascii=False))

    def refresh(self):
        loaded,warnings=load_reports(self.workspace.variables['evaluation_folder'].get())
        for r in loaded:self.workspace.evaluation_journal.records.setdefault(r['evaluation_id'],r)
        self.records=sorted(self.workspace.evaluation_journal.records.values(),key=lambda r:r['created_utc'],reverse=True)
        self.menu.configure(values=[f"{r['created_utc'][:19]} | {r.get('mode',r.get('operation'))} | {Path(r['dataset']['source']).name} | {r['evaluation_id'][:8]}" for r in self.records])
        if self.records:self.menu.current(0);self.show(self.records[0])
        if warnings:self.status.configure(text=f'Skipped {len(warnings)} unreadable report(s): '+warnings[0])

    def save(self):
        w=self.workspace
        if w.busy or self.record is None:return
        if self.record.get('schema_version')!=2:
            self.status.configure(text='Legacy report retained unchanged; request a new manual evaluation to use the updated framework.');return
        record=self.record;folder=w.variables['evaluation_folder'].get()
        def write():
            try:return dict(_path=str(save_report(record,folder)))
            except Exception as exc:return dict(_save_error=str(exc))
        def done(outcome):
            record.update(outcome);w.evaluation_pending=False
            if '_path' in outcome:record.pop('_save_error',None)
            if w.close_after_evaluation:w.close();return
            message='Save failed: '+outcome['_save_error'] if '_save_error' in outcome else 'Saved '+outcome['_path']
            w.status.set(message)
            if not self.closed:self.status.configure(text=message)
        w.evaluation_pending=True;w._start(write,done,'Saving the explicitly requested evaluation…')

    def plot(self):
        if not self.record or self.record.get('schema_version')!=2:return
        from tkinter import filedialog
        from methodology_publication import save_formats
        scopes=sorted({r['scope'] for r in self.record['tables']['sections']})
        if not scopes:self.status.configure(text='No transition intervals to plot.');return
        scope=self.scope.get() if self.scope.get() in scopes else scopes[0]
        path=filedialog.askdirectory(parent=self.root,title='Export measured profile figure (SVG/PDF/PNG)')
        if not path:return
        try:
            figure,settings=profile_figure(self.record,scope)
            stem='lts_profile_'+self.record['evaluation_id']+'_'+str(scopes.index(scope))
            try:files,_,_=save_formats(figure,Path(path),stem,settings)
            finally:figure.clear()
            if self.record.get('feature_selection'):
                metadata=dict(evaluation_id=self.record['evaluation_id'],scope=scope,
                    source_step_id=self.record['source_step_id'],end_step_id=self.record['end_step_id'],
                    feature_selection=self.record['feature_selection'],scope_definition=self.record['scope_definitions'][scope])
                (Path(path)/(stem+'.json')).write_text(json.dumps(metadata,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
            self.status.configure(text=f'Exported {len(files)} profile figure files for {scope}.')
        except (ValueError,OSError,RuntimeError) as exc:self.status.configure(text='Profile export failed: '+str(exc))

    def on_history_changed(self):
        if not self.closed:
            self._refresh_features()
            self.status.configure(text='History changed. Existing results are retained; click Evaluate for the current history state.')

    def clear(self):self.on_history_changed()

    def close(self):
        if not self.closed:self.closed=True;self.root.destroy()
