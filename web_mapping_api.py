"""Additive Mapping API; model jobs share the generalization worker."""
from fastapi import HTTPException,UploadFile,File,Depends
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool

from web_generalization_data import MAX_UPLOAD_BYTES


class MappingRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    revision:int=Field(ge=0)
    parameters:dict=Field(default_factory=dict)


class MappingSlice(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    revision:int=Field(ge=0)
    preview_id:str|None=None
    orientation:str='Horizontal'
    value:float
    tilt:float=45.
    azimuth:float=0.
    include_sections:bool=True
    clamp_position:bool=False


def attach_mapping_routes(app,workspace,*,workspace_dependency=None):
    dependency=workspace_dependency or (lambda:workspace)
    def check(m,revision):
        if revision!=m.revision:raise HTTPException(409,'Mapping changed in another tab. Refresh before continuing.')

    @app.get('/api/mapping/state')
    def state(w=Depends(dependency)):return w.mapping.state()

    @app.get('/api/mapping/scene')
    def scene(preview_id:str|None=None,w=Depends(dependency)):
        m=w.mapping
        s=m.scene(preview_id)
        return {**s.payload,'revision':m.revision,'built_revision':s.revision,'stale':s.revision!=m.revision}

    @app.get('/api/mapping/report')
    def report(preview_id:str|None=None,w=Depends(dependency)):
        m=w.mapping
        s=m.scene(preview_id)
        return dict(**s.result.report,cell_metadata=s.result.metadata,levels=s.result.levels,
                    crs=s.result.crs.to_string(),origin=s.result.origin)

    @app.post('/api/mapping/slice')
    def section(p:MappingSlice,w=Depends(dependency)):
        m=w.mapping
        check(m,p.revision);snapshot=m.scene(p.preview_id)
        result=snapshot.section(p.orientation,p.value,p.tilt,p.azimuth,include_sections=p.include_sections,clamp_position=p.clamp_position)
        return {**result,'revision':m.revision}

    @app.post('/api/mapping/uploads')
    async def upload(file:list[UploadFile]=File(...),w=Depends(dependency)):
        m=w.mapping
        files=[];total=0
        try:
            for item in file:
                content=bytearray()
                while chunk:=await item.read(1024**2):
                    content.extend(chunk);total+=len(chunk)
                    if total>MAX_UPLOAD_BYTES:raise ValueError('Upload exceeds 100 MiB combined.')
                files.append((item.filename or 'dataset',content))
            return await run_in_threadpool(m.uploads.add_files,files)
        finally:
            for item in file:await item.close()

    @app.post('/api/mapping/{action}')
    def command(action:str,p:MappingRequest,w=Depends(dependency)):
        if action not in ('load','osm','swap','overlap','analyze','review','build','discard-preview'):raise HTTPException(404,'Unknown Mapping command.')
        allowed=dict(load={'side','upload_id','layer','crs','override_crs','accept_declared_crs'},
                     overlap={'side','ids','resolution','keeper'},analyze={'settings'},review={'groups','settings'},
                     build={'preview'},osm={'side','bounds'},swap=set(),**{'discard-preview':set()})
        required=dict(load={'side'},osm={'side','bounds'},overlap={'side','ids','resolution'},review={'groups'})
        if set(p.parameters)-allowed[action] or required.get(action,set())-set(p.parameters):
            raise HTTPException(422,'Invalid or missing Mapping command parameters.')
        if action=='osm':
            from pydantic import TypeAdapter,ValidationError
            try:p.parameters['bounds']=TypeAdapter(tuple[float,float,float,float]).validate_python(p.parameters['bounds'])
            except ValidationError:raise HTTPException(422,'Supply four BBOX coordinates: west, south, east, north.') from None
        check(w.mapping,p.revision)
        return w.submit('mapping:'+action,p.revision,**p.parameters)
