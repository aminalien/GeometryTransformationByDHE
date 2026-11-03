"""Compact Tk review of saved numerical evaluations; owns no geometry or DHE."""
import json
from pathlib import Path


class EvaluationWindow:
    def __init__(self,workspace):
        import tkinter as tk
        from tkinter import ttk
        self.workspace=workspace;self.closed=False;self.records=[];self.record=None
        self.root=tk.Toplevel(workspace.root);self.root.title('DHE - Applied operation evaluations')
        self.root.geometry('1000x620');self.root.protocol('WM_DELETE_WINDOW',self.close)
        frame=ttk.Frame(self.root,padding=10);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Saved evaluations (including earlier runs and undone operations)').pack(anchor='w')
        self.menu=ttk.Combobox(frame,state='readonly');self.menu.pack(fill='x',pady=4)
        self.menu.bind('<<ComboboxSelected>>',lambda e:self.show(self.records[self.menu.current()]))
        row=ttk.Frame(frame);row.pack(fill='x')
        ttk.Label(row,textvariable=workspace.variables['evaluation_folder']).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Browse folder',command=workspace.browse_evaluations).pack(side='right')
        ttk.Button(row,text='Refresh',command=self.refresh).pack(side='right',padx=4)
        self.info=ttk.Label(frame,wraplength=950,justify='left');self.info.pack(fill='x',pady=8)
        table=ttk.Frame(frame);table.pack(fill='both',expand=True)
        self.table=ttk.Treeview(table,columns=('scope','metric','value','unit'),show='headings',height=13)
        for key,title,width in (('scope','Scope',145),('metric','Metric',300),('value','Value',200),('unit','Unit',150)):
            self.table.heading(key,text=title);self.table.column(key,width=width,anchor='w')
        vertical=ttk.Scrollbar(table,command=self.table.yview)
        horizontal=ttk.Scrollbar(table,orient='horizontal',command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.table.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns')
        horizontal.grid(row=1,column=0,sticky='ew');table.rowconfigure(0,weight=1);table.columnconfigure(0,weight=1)
        self.table.bind('<<TreeviewSelect>>',self._metric_reason)
        self.reason=ttk.Label(frame,text='',wraplength=950);self.reason.pack(fill='x')
        self.status=ttk.Label(frame,wraplength=950,justify='left');self.status.pack(fill='x',pady=5)
        self.retry=ttk.Button(frame,text='Retry saving selected evaluation',command=self.retry_save)
        self.retry.pack(anchor='e')
        ttk.Label(frame,text='DHE counts include retained history; shared boundaries count once per incident cell. No composite quality score.').pack(anchor='w',pady=(8,0))

    def refresh(self,select_id=None):
        if self.closed:return
        if select_id is None and self.record:select_id=self.record['evaluation_id']
        self.records,warnings=self.workspace.evaluation_journal.review(self.workspace.variables['evaluation_folder'].get())
        labels=[f"{r['created_utc'][:19]} | {r['operation']} {r['selected']} | step {r['history_step']} | {Path(r['dataset']['source']).name} | {r['evaluation_id'][:8]}" for r in self.records]
        self.menu.configure(values=labels)
        index=next((i for i,r in enumerate(self.records) if r['evaluation_id']==select_id),0)
        if self.records:self.menu.current(index);self.show(self.records[index])
        else:self.clear()
        if warnings:self.status.configure(text=self.status.cget('text')+f'\nSkipped {len(warnings)} unreadable/incomplete report(s): '+warnings[0])

    def clear(self):
        self.record=None;self.menu.set('');self.table.delete(*self.table.get_children())
        self.info.configure(text='No evaluation for this history state. Choose a saved result above.')
        self.status.configure(text='Evaluations are generated only after a successful Apply.')
        self.reason.configure(text='');self.retry.configure(state='disabled')

    def show(self,record):
        self.record=record;self.table.delete(*self.table.get_children());self.reason.configure(text='')
        self.info.configure(text=f"{record['operation']} | step {record['history_step']} | selected {record['selected']} | affected {record['affected']}\n"
            f"Parameters: {json.dumps(record['parameters'],sort_keys=True)}; winner: {record.get('winner')}\n"
            f"Zs={record['levels']['z_s']:g}, Zt={record['levels']['z_t']:g} | evaluation {record['evaluation_id']}")
        for i,row in enumerate(record['metrics']):
            value='Unavailable' if row['value'] is None else f"{row['value']:.8g}"
            if row.get('before') is not None:value=f"{row['before']} → {row['after']} (Δ {row['delta']:+})"
            self.table.insert('','end',iid=str(i),values=(row['scope'],row['label'],value,row['unit']))
        saved=record.get('_path','Not saved')
        self.status.configure(text=f"Evaluation: {record['status']} | {saved}"+
            ('\n'+record['error'] if record.get('error') else '')+
            ('\nSave failed: '+record['_save_error'] if record.get('_save_error') else ''))
        self.retry.configure(state='normal' if record.get('_save_error') else 'disabled')

    def _metric_reason(self,event=None):
        selection=self.table.selection()
        if selection and self.record:self.reason.configure(text=self.record['metrics'][int(selection[0])]['reason'])

    def retry_save(self):
        if self.record:self.workspace.retry_evaluation(self.record)

    def close(self):
        if not self.closed:self.closed=True;self.root.destroy()
