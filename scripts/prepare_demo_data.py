"""Package the approved external defaults without adding shapefiles to Git."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
NAMES=('osm_data1','citygml_data1')
SUFFIXES=('.shp','.shx','.dbf','.prj','.cpg')


def prepare(source_dir,output,manifest):
    files={name+suffix:(source_dir/(name+suffix)).read_bytes() for name in NAMES for suffix in SUFFIXES}
    output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            info=ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED
            archive.writestr(info,data)
    specification=dict(version='demo-data-v1',
        url='https://github.com/aminalien/GeometryTransformationByDHE/releases/download/demo-data-v1/dhe-demo-data-v1.zip',
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        files={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())})
    manifest.write_text(json.dumps(specification,indent=2)+'\n',encoding='utf-8')
    return specification


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=Path('C:/University/PhD/Data/Kleczkow Updated'))
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'dhe-demo-data-v1.zip')
    parser.add_argument('--manifest',type=Path,default=ROOT/'demo-data.json')
    args=parser.parse_args();prepare(args.source_dir,args.output,args.manifest)
    print(f'Created {args.output}; manifest: {args.manifest}')
