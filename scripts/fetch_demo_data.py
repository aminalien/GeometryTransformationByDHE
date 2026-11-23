"""Verify and install the versioned public default data during a cloud build."""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]


def install(payload,manifest,destination):
    if hashlib.sha256(payload).hexdigest()!=manifest['sha256']:
        raise ValueError('Default data archive checksum mismatch.')
    expected=manifest['files'];files={}
    with ZipFile(BytesIO(payload)) as archive:
        names=archive.namelist()
        if len(names)!=len(expected) or set(names)!=set(expected):
            raise ValueError('Default data archive has unexpected or missing components.')
        for name in names:
            if Path(name).name!=name or '/' in name or '\\' in name:
                raise ValueError('Default data component must be a plain filename.')
            data=archive.read(name)
            if hashlib.sha256(data).hexdigest()!=expected[name]:
                raise ValueError('Default data component checksum mismatch: '+name)
            files[name]=data
    destination.mkdir(parents=True,exist_ok=True)
    for name,data in files.items():(destination/name).write_bytes(data)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,help='Use a local package for validation instead of downloading.')
    parser.add_argument('--manifest',type=Path,default=ROOT/'demo-data.json')
    parser.add_argument('--destination',type=Path,default=Path(os.environ.get('DHE_DEMO_DATA_DIR',ROOT/'artifacts'/'demo-data')))
    args=parser.parse_args();manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    if args.archive:payload=args.archive.read_bytes()
    else:
        with urlopen(Request(manifest['url'],headers={'User-Agent':'DHE-Transition-Atlas/1.0'}),timeout=60) as response:
            payload=response.read(20*1024**2+1)
        if len(payload)>20*1024**2:raise ValueError('Default data archive exceeds 20 MiB.')
    install(payload,manifest,args.destination)
    print(f'Installed verified demo data: {args.destination}')
