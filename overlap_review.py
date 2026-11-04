"""Review complete input-polygon survivors before allocating DHE topology."""
from generalization_geometry import largest_first_survivors,overlap_groups,resolve_overlaps


class OverlapReview:
    def __init__(self,parent,data,on_accept,on_cancel):
        import tkinter as tk
        from tkinter import ttk
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
        self.data=data;self.on_accept=on_accept;self.on_cancel=on_cancel;self.closed=False
        self.groups=overlap_groups(data.conflicts);self.kept=largest_first_survivors(data)
        self.rows={int(row['_index']):row for _,row in data.frame.iterrows()}
        self.root=tk.Toplevel(parent);self.root.title('Resolve overlapping input polygons')
        self.root.geometry('1100x740');self.root.protocol('WM_DELETE_WINDOW',self.cancel)
        self.root.transient(parent)
        body=ttk.Frame(self.root,padding=10);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Keep complete polygons. Checking a polygon removes its overlapping neighbours from the survivors. Original files are unchanged.',wraplength=1050).pack(fill='x')
        self.group=ttk.Combobox(body,state='readonly',values=[f'Group {i+1}: '+', '.join(map(str,sorted(g))) for i,g in enumerate(self.groups)])
        self.group.pack(fill='x',pady=6);self.group.current(0);self.group.bind('<<ComboboxSelected>>',lambda e:self.display())
        middle=ttk.Panedwindow(body,orient='horizontal');middle.pack(fill='both',expand=True)
        side=ttk.Frame(middle);middle.add(side,weight=0)
        self.table=ttk.Treeview(side,columns=('keep','index','id','area'),show='headings',selectmode='extended',height=16)
        for key,title,width in (('keep','Keep?',55),('index','Index',55),('id','Original ID',115),('area','Area (m²)',115)):
            self.table.heading(key,text=title);self.table.column(key,width=width,stretch=False)
        bar=ttk.Scrollbar(side,command=self.table.yview);self.table.configure(yscrollcommand=bar.set)
        self.table.pack(side='left',fill='both',expand=True);bar.pack(side='right',fill='y')
        self.table.bind('<Button-1>',self._click);self.table.bind('<space>',self._toggle_selected)
        pane=ttk.Frame(middle);middle.add(pane,weight=1)
        self.figure=Figure(figsize=(6,5),layout='constrained');self.ax=self.figure.add_subplot()
        self.canvas=FigureCanvasTkAgg(self.figure,master=pane);self.canvas.get_tk_widget().pack(fill='both',expand=True)
        NavigationToolbar2Tk(self.canvas,pane)
        row=ttk.Frame(body);row.pack(fill='x',pady=6)
        ttk.Button(row,text='Keep only highlighted in this group',command=self.keep_only_selected).pack(side='left')
        ttk.Button(row,text='Largest first in this group',command=self.reset_group).pack(side='left',padx=5)
        ttk.Button(row,text='Load chosen survivors',command=self.confirm).pack(side='right')
        ttk.Button(row,text='Cancel',command=self.cancel).pack(side='right',padx=5)
        self.status=ttk.Label(body,wraplength=1050);self.status.pack(fill='x')
        self.display();self.root.grab_set()

    @property
    def current_group(self):return self.groups[self.group.current()]

    def choose(self,index,keep):
        if index not in self.current_group:raise ValueError('Choose a polygon in the displayed group.')
        if keep:
            neighbours={b if a==index else a for a,b in self.data.conflicts if index in (a,b)}
            self.kept.difference_update(neighbours);self.kept.add(index)
        else:self.kept.discard(index)
        self.display()

    def _click(self,event):
        if self.table.identify_region(event.x,event.y)=='cell' and self.table.identify_column(event.x)=='#1':
            row=self.table.identify_row(event.y)
            if row:self.choose(int(row),int(row) not in self.kept);return 'break'

    def _toggle_selected(self,event=None):
        for key in tuple(self.table.selection()):self.choose(int(key),int(key) not in self.kept)
        return 'break'

    def keep_only_selected(self):
        selected=set(map(int,self.table.selection()))
        if not selected:self.status.configure(text='Highlight one or more rows first.');return
        if any(a in selected and b in selected for a,b in self.data.conflicts):
            self.status.configure(text='The highlighted polygons overlap each other. Choose a compatible set, such as A+C.');return
        self.kept.difference_update(self.current_group);self.kept.update(selected);self.display()

    def reset_group(self):
        self.kept.difference_update(self.current_group)
        self.kept.update(largest_first_survivors(self.data)&self.current_group);self.display()

    def display(self):
        from generalization_session import original_id
        selected=self.table.selection();self.table.delete(*self.table.get_children());self.ax.clear()
        for key in sorted(self.current_group):
            row=self.rows[key];g=row.geometry;keep=key in self.kept
            self.table.insert('','end',iid=str(key),values=('Yes' if keep else 'No',key,original_id(row),f'{g.area:.8g}'))
            color='#287c4b' if keep else '#b64b49'
            self.ax.fill(*g.exterior.xy,color=color,alpha=.22 if keep else .07)
            self.ax.plot(*g.exterior.xy,color=color,ls='-' if keep else '--',lw=1.8)
            point=g.representative_point();self.ax.annotate(str(key),(point.x,point.y),ha='center',va='center')
        self.table.selection_set([key for key in selected if int(key) in self.current_group])
        self.ax.set_aspect('equal',adjustable='box');self.ax.set(xlabel='Projected X (m)',ylabel='Projected Y (m)',title='Green: keep   Red dashed: remove')
        self.canvas.draw_idle()
        self.status.configure(text=f'{len(self.kept)} of {len(self.rows)} polygons retained across {len(self.groups)} overlap group(s). Click Keep? to change a survivor, or highlight compatible rows and keep only those in this group.')

    def confirm(self):
        try:data=resolve_overlaps(self.data,self.kept)
        except ValueError as exc:self.status.configure(text=str(exc));return
        self._destroy();self.on_accept(data)

    def _destroy(self):
        if not self.closed:
            self.closed=True;self.root.grab_release()
            if self.canvas._idle_draw_id is not None:
                self.canvas.get_tk_widget().after_cancel(self.canvas._idle_draw_id)
                self.canvas._idle_draw_id=None
            self.figure.clear();self.root.destroy()

    def cancel(self):
        self._destroy();self.on_cancel()
