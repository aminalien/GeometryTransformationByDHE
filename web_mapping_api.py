"""Additive Mapping API; model jobs share the generalization worker."""
from fastapi import HTTPException,UploadFile,File
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


def attach_mapping_routes(app,workspace):
    m=workspace.mapping
    def check(revision):
        if revision!=m.revision:raise HTTPException(409,'Mapping changed in another tab. Refresh before continuing.')

    @app.get('/api/mapping/state')
    def state():return m.state()

    @app.get('/api/mapping/scene')
    def scene(preview_id:str|None=None):
        s=m.scene(preview_id)
        return {**s.payload,'revision':m.revision,'built_revision':s.revision,'stale':s.revision!=m.revision}

    @app.get('/api/mapping/report')
    def report(preview_id:str|None=None):
        s=m.scene(preview_id)
        return dict(**s.result.report,cell_metadata=s.result.metadata,levels=s.result.levels,
                    crs=s.result.crs.to_string(),origin=s.result.origin)

    @app.post('/api/mapping/slice')
    def section(p:MappingSlice):
        check(p.revision);snapshot=m.scene(p.preview_id)
        result=snapshot.section(p.orientation,p.value,p.tilt,p.azimuth,include_sections=p.include_sections,clamp_position=p.clamp_position)
        return {**result,'revision':m.revision}

    @app.post('/api/mapping/uploads')
    async def upload(file:list[UploadFile]=File(...)):
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
    def command(action:str,p:MappingRequest):
        if action not in ('load','overlap','analyze','review','build','discard-preview'):raise HTTPException(404,'Unknown Mapping command.')
        allowed=dict(load={'side','upload_id','layer','crs','override_crs','accept_declared_crs'},
                     overlap={'side','ids','resolution','keeper'},analyze={'settings'},review={'groups','settings'},
                     build={'preview'},**{'discard-preview':set()})
        required=dict(load={'side'},overlap={'side','ids','resolution'},review={'groups'})
        if set(p.parameters)-allowed[action] or required.get(action,set())-set(p.parameters):
            raise HTTPException(422,'Invalid or missing Mapping command parameters.')
        check(p.revision)
        return workspace.submit('mapping:'+action,p.revision,**p.parameters)
