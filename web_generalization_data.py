"""Web-only import adapter: union overlapping inputs without editing source data."""
from __future__ import annotations

from pathlib import Path,PurePosixPath
import shutil
import stat
import tempfile
import threading
from uuid import uuid4
from zipfile import ZipFile,BadZipFile

import geopandas as gpd
import numpy as np
import pyogrio
from pyproj import CRS
from shapely import force_2d
from shapely.ops import unary_union

from generalization_geometry import LoadedData,TOL,overlap_pairs,overlap_groups,download_osm

DEFAULT_WEB_DATA='C:/University/PhD/Data/Kleczkow/osm_data4.shp'
MAX_UPLOAD_BYTES=100*1024**2
OVERPASS_ENDPOINTS=('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter')


def json_value(value):
    """Convert input attributes to finite, browser-safe JSON values."""
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,(float,np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value,np.integer):return int(value)
    if isinstance(value,dict):return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_value(v) for v in value]
    return str(value)


def source_id(attributes,position):
    names={str(k).lower():k for k in attributes}
    key=next((names[n] for n in ('osm_id','id','fid','objectid') if n in names),None)
    return str(attributes[key]) if key is not None else str(position)


def crs_metadata(crs,bounds,count):
    declared=CRS.from_user_input(crs) if crs else None
    if bounds is None or len(bounds)!=4 or not np.isfinite(bounds).all():raise ValueError('The layer has no finite spatial bounds. Choose a nonempty polygon layer.')
    bounds=[float(v) for v in bounds]
    suspicious=bool(declared and declared.is_projected and np.isfinite(bounds).all()
        and -180<=bounds[0]<=bounds[2]<=180 and -90<=bounds[1]<=bounds[3]<=90)
    return dict(crs=declared.to_string() if declared else None,bounds=bounds,count=int(count),
        suspicious=suspicious,suggested_crs='EPSG:4326' if suspicious else None,
        warning='Projected CRS with longitude/latitude-sized coordinates. Confirm the declared CRS or explicitly override it; coordinate ranges alone do not prove a CRS error.' if suspicious else '')


def prepared_preview(data):
    frame=data.frame.to_crs(4326)
    return dict(type='FeatureCollection',features=[dict(type='Feature',geometry=g.__geo_interface__,
        properties=dict(index=int(k))) for k,g in zip(frame['_index'],frame.geometry)])


def prepare_web_frame(frame,source='Upload',*,crs=None,override_crs=None,notes=()):
    notes=list(notes)
    if override_crs:
        notes.append(f'Explicit CRS interpretation override: {frame.crs or "missing"} -> {override_crs}; source files unchanged.')
        frame=frame.set_crs(override_crs,allow_override=True)
    if frame.crs is None:
        if not crs:raise ValueError('This dataset has no CRS. Supply its EPSG code before loading.')
        frame=frame.set_crs(crs)
    frame=frame.copy()
    axes=frame.crs.axis_info
    if not frame.crs.is_projected or not axes or any(abs(a.unit_conversion_factor-1)>1e-10 for a in axes[:2]):
        frame=frame.to_crs(frame.estimate_utm_crs())
    records=[];rejected=list(notes);extra=len(frame)+1
    for position,(_,row) in enumerate(frame.iterrows(),1):
        geometry=row[frame.geometry.name]
        if geometry is None or geometry.is_empty or geometry.geom_type not in ('Polygon','MultiPolygon'):
            rejected.append(f'Input {position}: empty or non-polygonal geometry.');continue
        if not geometry.is_valid or not np.isfinite(np.asarray(list(geometry.bounds))).all():
            rejected.append(f'Input {position}: invalid geometry.');continue
        attrs={str(k):json_value(v) for k,v in row.items() if k not in (frame.geometry.name,'_index')}
        pieces=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
        for part,g in enumerate(pieces,1):
            key=position if part==1 else extra
            if part>1:extra+=1
            if g.area<=TOL*TOL:rejected.append(f'Input {position}, component {part}: zero area.');continue
            member=dict(row=position,component=part,id=source_id(attrs,position),attributes=attrs)
            records.append(dict(_index=key,geometry=force_2d(g),attributes=attrs,members=[member]))
    if not records:raise ValueError('No valid polygonal input. '+' '.join(rejected[:5]))
    candidates=gpd.GeoDataFrame(records,geometry='geometry',crs=frame.crs)
    conflicts=overlap_pairs(candidates);groups=overlap_groups(conflicts)
    covered=set().union(*groups) if groups else set()
    groups.extend({int(k)} for k in candidates['_index'] if int(k) not in covered)
    lookup={int(r['_index']):r for _,r in candidates.iterrows()};output=[]
    for group in groups:
        representative=min(group,key=lambda k:(-lookup[k].geometry.area,k))
        merged=unary_union([lookup[k].geometry for k in sorted(group)])
        members=[m for k in sorted(group) for m in lookup[k]['members']]
        if merged.geom_type!='Polygon' or merged.interiors or not merged.is_valid:
            rejected.append(f'Inputs {sorted(group)}: union is holed or unsupported; excluded without filling holes.');continue
        attrs=dict(lookup[representative]['attributes']);attrs['source_members']=members
        attrs['original_id']=source_id(attrs,members[0]['row'])
        if len(group)>1:rejected.append(f'Auto-union {sorted(group)} → feature {representative}; all source attributes retained in source_members.')
        output.append(dict(**{k:v for k,v in attrs.items() if k not in ('geometry','_index')},_index=representative,geometry=merged))
    if not output:raise ValueError('No supported hole-free polygons after union. '+' '.join(rejected[:5]))
    result=gpd.GeoDataFrame(sorted(output,key=lambda r:r['_index']),geometry='geometry',crs=frame.crs)
    if overlap_pairs(result):raise ValueError('Import union did not remove all positive-area overlaps.')
    return LoadedData(result,rejected,str(source))


def read_web_dataset(path,*,layer=None,crs=None,override_crs=None,source=None):
    return prepare_web_frame(gpd.read_file(path,layer=layer),source or Path(path).name,crs=crs,override_crs=override_crs)


def fetch_web_osm(bounds):
    # Request unresolved overlaps; the desktop's removal policy is not used.
    failure=None
    for i,endpoint in enumerate(OVERPASS_ENDPOINTS):
        try:data=download_osm(*bounds,endpoint=endpoint,overlap_policy='review')
        except ValueError as exc:
            if not str(exc).startswith('OSM download failed:'):raise
            failure=exc;continue
        notes=[*data.rejected]
        if i:notes.append('Downloaded using the fallback Overpass instance: '+endpoint)
        return prepare_web_frame(data.frame,data.source,notes=notes)
    raise ValueError('Both Overpass download services are unavailable. Retry later or upload a polygon dataset. '+str(failure))


class UploadStore:
    """Bounded temporary files; archive names never become arbitrary disk paths."""
    def __init__(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='dhe-web-');self.root=Path(self.temporary.name);self.items={};self.lock=threading.RLock()

    def add(self,name,payload):
        with self.lock:return self._add(name,payload)

    def add_files(self,files):
        if len(files)==1 and Path(files[0][0]).suffix.lower()!='.shp':return self.add(*files[0])
        if sum(len(p) for _,p in files)>MAX_UPLOAD_BYTES:raise ValueError('Upload exceeds 100 MiB combined.')
        names=[Path(n).name for n,_ in files]
        if len({n.casefold() for n in names})!=len(names):raise ValueError('Duplicate upload filenames.')
        shapes=[n for n in names if Path(n).suffix.lower()=='.shp']
        if len(shapes)!=1:raise ValueError('Select one .shp together with its .shx and .dbf files (optional .prj and .cpg).')
        stem=Path(shapes[0]).stem.casefold();extensions={Path(n).suffix.lower() for n in names if Path(n).stem.casefold()==stem}
        missing={'.shx','.dbf'}-extensions
        if missing:raise ValueError('Missing Shapefile companions: '+', '.join(sorted(missing))+'. Select them together with the .shp.')
        if any(Path(n).stem.casefold()!=stem or Path(n).suffix.lower() not in ('.shp','.shx','.dbf','.prj','.cpg','.qix','.sbn','.sbx') for n in names):
            raise ValueError('Select only the companion files for one Shapefile.')
        from io import BytesIO
        stream=BytesIO()
        with ZipFile(stream,'w') as archive:
            for name,(_,payload) in zip(names,files):archive.writestr(name,payload)
        return self.add(stem+'.zip',stream.getvalue())

    def _add(self,name,payload):
        if not payload or len(payload)>MAX_UPLOAD_BYTES:raise ValueError('Upload a nonempty file of at most 100 MiB.')
        if len(self.items)>=10:raise ValueError('Ten uploads are already staged. Restart the server to clear them.')
        suffix=Path(name).suffix.lower()
        if suffix not in ('.geojson','.json','.gpkg','.zip'):raise ValueError('Use GeoJSON, GeoPackage, or a zipped Shapefile.')
        token=uuid4().hex;folder=self.root/token;folder.mkdir();path=folder/('input'+suffix)
        choices=[]
        try:
            path.write_bytes(payload)
            if suffix=='.zip':
                with ZipFile(path) as archive:
                    entries=archive.infolist()
                    if len(entries)>500 or sum(i.file_size for i in entries)>512*1024**2:raise ValueError('Archive exceeds 500 entries or 512 MiB expanded size.')
                    seen=set()
                    for info in entries:
                        item=PurePosixPath(info.filename.replace('\\','/'))
                        if item.is_absolute() or '..' in item.parts or any(':' in p for p in item.parts) or stat.S_ISLNK(info.external_attr>>16):
                            raise ValueError('Unsafe archive entry.')
                        normalized=str(item).casefold()
                        if normalized in seen:raise ValueError('Duplicate archive paths.')
                        seen.add(normalized)
                    archive.extractall(folder/'files')
                for shape in sorted((folder/'files').rglob('*')):
                    if shape.suffix.lower()=='.shp':choices.append(dict(name=str(shape.relative_to(folder/'files')),path=shape,layer=None))
                if not choices:raise ValueError('The ZIP contains no Shapefile (.shp and accompanying files).')
            else:
                for layer,kind in pyogrio.list_layers(path):
                    if kind is not None and ('Polygon' in str(kind) or str(kind)=='Unknown'):
                        choices.append(dict(name=str(layer),path=path,layer=str(layer)))
                if not choices:raise ValueError('No polygonal layers found.')
        except (BadZipFile,pyogrio.errors.DataSourceError) as exc:
            self._remove_failed(folder);raise ValueError(f'Cannot read polygon dataset: {exc}') from exc
        except Exception:
            self._remove_failed(folder);raise
        try:
            metadata={}
            for c in choices:
                if c['path'].suffix.lower()=='.shp':
                    companions={p.suffix.lower() for p in c['path'].parent.iterdir() if p.stem.casefold()==c['path'].stem.casefold()}
                    missing={'.shx','.dbf'}-companions
                    if missing:raise ValueError('Missing Shapefile companions: '+', '.join(sorted(missing)))
                info=pyogrio.read_info(c['path'],layer=c['layer'])
                metadata[c['name']]=crs_metadata(info['crs'],info['total_bounds'],info['features'])
        except Exception:
            self._remove_failed(folder);raise
        self.items[token]=dict(name=Path(name).name,choices=choices,metadata=metadata)
        return dict(upload_id=token,name=Path(name).name,layers=[c['name'] for c in choices],metadata=metadata)

    def _remove_failed(self,folder):
        target=folder.resolve()
        if target.parent!=self.root.resolve():raise ValueError('Upload cleanup target is outside its temporary directory.')
        shutil.rmtree(target)

    def read(self,token,layer,crs=None,override_crs=None,accept_declared_crs=False):
        if token not in self.items:raise ValueError('Upload is missing or expired. Upload it again.')
        item=self.items[token]
        choice=next((c for c in item['choices'] if c['name']==layer),None)
        if choice is None:raise ValueError('Choose one of the uploaded polygon layers.')
        if item['metadata'][layer]['suspicious'] and not override_crs and not accept_declared_crs:
            raise ValueError('Suspicious declared CRS: explicitly confirm it or choose a CRS override before loading.')
        return read_web_dataset(choice['path'],layer=choice['layer'],crs=crs,override_crs=override_crs,source=item['name']+' / '+layer)

    def close(self):
        with self.lock:self.temporary.cleanup()
