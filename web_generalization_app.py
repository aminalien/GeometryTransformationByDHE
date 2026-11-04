"""Run the local browser workspace: python web_generalization_app.py."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError
from uuid import uuid4
import webbrowser

try:
    from fastapi import FastAPI,HTTPException,UploadFile,File
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.responses import FileResponse,Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel,ConfigDict,Field
    from starlette.concurrency import run_in_threadpool
except ImportError as exc:
    raise SystemExit('Install optional web dependencies: python -m pip install -r requirements-web.txt') from exc

from generalization_model import GeneralizationSession,_core,_restore
from web_generalization_data import DEFAULT_WEB_DATA,MAX_UPLOAD_BYTES,UploadStore,read_web_dataset,fetch_web_osm,prepared_preview
from web_generalization_scene import WebScene


class RevisionConflict(ValueError):pass


class Workspace:
    """One application workspace; all DHE mutations occur on one executor thread."""
    def __init__(self,default_path=DEFAULT_WEB_DATA,initial_height=5.):
        self.default_path=str(default_path);self.initial_height=initial_height
        self.session=None;self.scene=None;self.revision=0;self.preview=None;self.preview_scene=None;self.preview_key=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='DHE web worker')
        self.lock=threading.RLock();self.jobs={};self.running=None;self.uploads=UploadStore()

    def check(self,revision):
        if revision!=self.revision:raise RevisionConflict('The workspace changed in another tab. Refresh the scene and try again.')

    def submit(self,action,revision,**parameters):
        with self.lock:
            self.check(revision)
            if self.running:raise RevisionConflict('Another construction job is running. Wait for it to finish.')
            token=uuid4().hex;self.running=token;self.jobs[token]=dict(id=token,status='running',action=action,stage='Queued')
            if len(self.jobs)>50:
                old=next(k for k,v in self.jobs.items() if v['status']!='running');del self.jobs[old]
        def work():
            outcome={}
            def progress(stage,data=None):
                with self.lock:
                    self.jobs[token]['stage']=stage
                    if data is not None:self.jobs[token]['input_preview']=data
            try:
                result=self.execute(action,revision,_progress=progress,**parameters)
                outcome=dict(status='complete',result=result)
            except Exception as exc:
                detail=getattr(self.session,'last_diagnostic',None) if self.session and action in ('preview','apply') else None
                outcome=dict(status='failed',error=str(exc),diagnostic=detail)
            finally:
                with self.lock:self.jobs[token].update(outcome);self.running=None
        self.pool.submit(work)
        return dict(job_id=token)

    def execute(self,action,revision,**p):
        self.check(revision)
        progress=p.pop('_progress',lambda *args:None)
        saved=_core()
        try:
            if self.session:_restore(self.session.core)
            if action in ('default','upload','osm'):
                progress('Downloading OSM footprints' if action=='osm' else 'Reading data, interpreting CRS and unioning overlaps')
                if action=='default':data=read_web_dataset(self.default_path,source=Path(self.default_path).name)
                elif action=='upload':data=self.uploads.read(p['upload_id'],p['layer'],p.get('crs'),p.get('override_crs'),p.get('accept_declared_crs',False))
                else:data=fetch_web_osm(p['bounds'])
                progress(f'Constructing and validating {len(data.frame)} DHE cells; large datasets can take several minutes',prepared_preview(data))
                candidate=GeneralizationSession(data,p.get('initial_height',self.initial_height))
                progress('Preparing geographic scene and topology inspector')
                snapshot=WebScene(candidate,self.revision+1)
                with self.lock:
                    self.session=candidate;self.scene=snapshot;self.revision+=1;self.clear_preview()
                return dict(revision=self.revision)
            if not self.session:raise ValueError('Load a polygon dataset first.')
            if action in ('preview','apply'):
                key=json.dumps(p,sort_keys=True);proposal=self.preview if self.preview_key==key else None
                if proposal is None:proposal=self.session.preview(**p)
                _restore(proposal.core)
                if action=='preview':
                    token=uuid4().hex;snapshot=WebScene(self.session,self.revision,proposal=proposal,preview_id=token)
                    with self.lock:self.preview=proposal;self.preview_scene=snapshot;self.preview_key=key
                    return dict(revision=self.revision,preview_id=token)
                # Serialize and validate display data before committing the application state.
                snapshot=WebScene(self.session,self.revision+1,proposal=proposal)
                next_history=[*self.session.history[:self.session.cursor+1],proposal.state]
                snapshot.payload.update(preview=False,preview_id=None,cursor=self.session.cursor+1,
                    history=[dict(index=i,label=s.description,step_id=s.step_id,z=s.z) for i,s in enumerate(next_history)])
                snapshot.preview_id=None
                self.session.apply(proposal)
                with self.lock:self.revision+=1;self.scene=snapshot;self.clear_preview()
                return dict(revision=self.revision)
            if action=='history':
                previous=self.session.cursor
                try:
                    self.session.go(p['index']);snapshot=WebScene(self.session,self.revision+1)
                except Exception:
                    if self.session.cursor!=previous:self.session.go(previous)
                    raise
                with self.lock:self.revision+=1;self.scene=snapshot;self.clear_preview()
                return dict(revision=self.revision)
            raise ValueError('Unknown workspace action.')
        except Exception:
            _restore(self.session.core if self.session else saved)
            raise
        finally:
            if self.session:_restore(self.session.core)

    def clear_preview(self):self.preview=None;self.preview_scene=None;self.preview_key=None

    def get_scene(self,preview_id=None):
        with self.lock:
            if preview_id:
                if not self.preview_scene or preview_id!=self.preview_scene.preview_id:raise RevisionConflict('Preview expired. Request a new preview.')
                return self.preview_scene
            return self.scene

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True);self.uploads.close()


class GoogleTiles:
    """Optional official tile adapter. Credentials never enter URLs sent to the browser."""
    def __init__(self,key):self.key=key;self.sessions={};self.lock=threading.Lock()

    def request(self,path,params,body=None):
        params=dict(params,key=self.key);data=json.dumps(body).encode() if body is not None else None
        request=Request('https://tile.googleapis.com/'+path+'?'+urlencode(params),data=data,headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=20) as response:return response.read(),dict(response.headers)
        except (URLError,HTTPError,TimeoutError) as exc:
            raise ValueError('Google Maps could not load. Check the configured Map Tiles API key and service availability.') from None

    def session(self,kind):
        if kind not in ('roadmap','satellite'):raise ValueError('Unknown Google map type.')
        if not self.key:raise ValueError('Set GOOGLE_MAPS_API_KEY with Map Tiles API enabled to use Google backgrounds.')
        with self.lock:
            if kind not in self.sessions or self.sessions[kind]['expiry']<=time.time()+60:
                body,_=self.request('v1/createSession',{},dict(mapType=kind,language='en-US',region='PL'))
                session=json.loads(body);session['expiry']=float(session['expiry']);self.sessions[kind]=session
            return self.sessions[kind]

    def tile(self,kind,z,x,y):
        if not 0<=z<=22 or not 0<=x<2**z or not 0<=y<2**z:raise ValueError('Invalid map tile coordinate.')
        session=self.session(kind)
        body,headers=self.request(f'v1/2dtiles/{z}/{x}/{y}',dict(session=session['session']))
        return body,{k.lower():v for k,v in headers.items() if k.lower() in ('content-type','cache-control','expires','etag')}

    def attribution(self,kind,zoom,north,south,east,west):
        session=self.session(kind)
        body,_=self.request('tile/v1/viewport',dict(session=session['session'],zoom=zoom,north=north,south=south,east=east,west=west))
        return json.loads(body)


class Payload(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    revision:int=Field(ge=0)


class LoadPayload(Payload):
    initial_height:float=Field(default=5.,gt=0)


class ImportPayload(LoadPayload):
    upload_id:str
    layer:str
    crs:str|None=None
    override_crs:str|None=None
    accept_declared_crs:bool=False


class OSMInput(LoadPayload):
    bounds:tuple[float,float,float,float]


class OperationInput(Payload):
    operation:str
    selected:list[int]
    dz:float=Field(default=10.,gt=0)
    tolerance:float=Field(default=1.,ge=0)
    iterations:int=Field(default=1,ge=1)
    ratio:float=Field(default=.25,gt=0,lt=.5)
    winner:int|None=None


class HistoryInput(Payload):index:int=Field(ge=0)


class SliceInput(Payload):
    orientation:str='Horizontal'
    value:float
    tilt:float=Field(default=45.,ge=0,le=90)
    azimuth:float=0.
    preview_id:str|None=None
    include_sections:bool=True
    clamp_position:bool=False


class BoxInput(Payload):bounds:tuple[float,float,float,float]


def create_app(*,default_path=DEFAULT_WEB_DATA,initial_height=5.,autoload=True,workspace=None):
    w=workspace or Workspace(default_path,initial_height)
    google=GoogleTiles(os.environ.get('GOOGLE_MAPS_API_KEY',''))
    @asynccontextmanager
    async def lifespan(app):
        if autoload:w.submit('default',0,initial_height=initial_height)
        yield
        w.close()
    app=FastAPI(title='DHE Transition Atlas',lifespan=lifespan)
    app.state.workspace=w;app.state.google=google
    app.add_middleware(GZipMiddleware,minimum_size=1000)
    assets=Path(__file__).with_name('web_generalization')
    app.mount('/assets',StaticFiles(directory=assets),name='assets')

    @app.exception_handler(RevisionConflict)
    async def stale(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409,content=dict(detail=str(exc)))

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400,content=dict(detail=str(exc)))

    @app.get('/')
    def index():return FileResponse(assets/'index.html')

    @app.get('/api/config')
    def config():return dict(default_dataset=Path(default_path).name,initial_height=initial_height,google_available=bool(google.key))

    @app.get('/api/state')
    def state():
        with w.lock:
            return dict(revision=w.revision,job_id=w.running,loaded=w.scene is not None,
                        recent_jobs=[{k:v for k,v in j.items() if k!='input_preview'} for j in list(w.jobs.values())[-1:]])

    @app.get('/api/scene')
    def scene(preview_id:str|None=None):
        snapshot=w.get_scene(preview_id)
        if snapshot is None:raise HTTPException(404,'No dataset is loaded yet.')
        return snapshot.payload

    @app.get('/api/jobs/{token}')
    def job(token:str):
        with w.lock:
            if token not in w.jobs:raise HTTPException(404,'Job is missing or expired.')
            return {**{k:v for k,v in w.jobs[token].items() if k!='input_preview'},'has_input_preview':'input_preview' in w.jobs[token]}

    @app.get('/api/jobs/{token}/input-preview')
    def input_preview(token:str):
        with w.lock:
            if token not in w.jobs or 'input_preview' not in w.jobs[token]:raise HTTPException(404,'Prepared input preview not available.')
            return w.jobs[token]['input_preview']

    def submit(action,p):
        values=p.model_dump();revision=values.pop('revision');return w.submit(action,revision,**values)

    @app.post('/api/load-default')
    def load_default(p:LoadPayload):return submit('default',p)

    @app.post('/api/load-upload')
    def load_upload(p:ImportPayload):return submit('upload',p)

    @app.post('/api/osm')
    def osm(p:OSMInput):return submit('osm',p)

    @app.post('/api/preview')
    def preview(p:OperationInput):return submit('preview',p)

    @app.post('/api/apply')
    def apply(p:OperationInput):return submit('apply',p)

    @app.post('/api/history')
    def history(p:HistoryInput):return submit('history',p)

    @app.post('/api/slice')
    def section(p:SliceInput):
        w.check(p.revision);snapshot=w.get_scene(p.preview_id)
        if snapshot is None:raise ValueError('Load a dataset first.')
        return snapshot.section(p.orientation,p.value,p.tilt,p.azimuth,include_sections=p.include_sections,clamp_position=p.clamp_position)

    @app.post('/api/select-box')
    def select_box(p:BoxInput):
        from shapely.geometry import box,shape
        w.check(p.revision);snapshot=w.get_scene()
        if snapshot is None:raise ValueError('Load a dataset first.')
        west,south,east,north=p.bounds
        if not -180<=west<east<=180 or not -90<=south<north<=90:raise ValueError('Invalid selection bounds.')
        region=box(*p.bounds)
        return dict(indices=[f['properties']['index'] for f in snapshot.payload['features']['features']
                             if f['properties']['kind']=='Polygon' and shape(f['geometry']).intersects(region)])

    @app.post('/api/uploads')
    async def upload(file:list[UploadFile]=File(...)):
        files=[];total=0
        try:
            for item in file:
                content=bytearray()
                while chunk:=await item.read(1024**2):
                    content.extend(chunk);total+=len(chunk)
                    if total>MAX_UPLOAD_BYTES:raise ValueError('Upload exceeds 100 MiB combined.')
                files.append((item.filename or 'dataset',content))
            return await run_in_threadpool(w.uploads.add_files,files)
        finally:
            for item in file:await item.close()

    @app.get('/api/google/tiles/{kind}/{z}/{x}/{y}')
    def google_tile(kind:str,z:int,x:int,y:int):
        data,headers=google.tile(kind,z,x,y);return Response(data,headers=headers)

    @app.get('/api/google/attribution')
    def attribution(kind:str,zoom:int,north:float,south:float,east:float,west:float):
        if not all(math.isfinite(v) for v in (north,south,east,west)):raise ValueError('Invalid map bounds.')
        return google.attribution(kind,zoom,north,south,east,west)

    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-shp',default=DEFAULT_WEB_DATA)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--initial-height',type=float,default=5.)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if not math.isfinite(args.initial_height) or args.initial_height<=0:parser.error('Initial height must be positive and finite.')
    import uvicorn
    url=f'http://127.0.0.1:{args.port}'
    print('DHE Transition Atlas: '+url,flush=True)
    if not args.no_browser:threading.Timer(1.2,lambda:webbrowser.open(url)).start()
    uvicorn.run(create_app(default_path=args.input_shp,initial_height=args.initial_height),host='127.0.0.1',port=args.port,workers=1)


if __name__=='__main__':main()
