"""Offline publisher utility. Keep private.pem outside the repository and downloads."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
from mac_update import FILES

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['init','build']);p.add_argument('directory',type=Path)
    p.add_argument('--source',type=Path);p.add_argument('--key',type=Path);p.add_argument('--notes',type=Path)
    a=p.parse_args();os.umask(0o077);a.directory.mkdir(parents=True,exist_ok=True)
    if a.action=='init':
        private=a.directory/'private.pem';public=a.directory/'public.pem'
        if private.exists() or public.exists():raise SystemExit('Refusing to overwrite signing keys')
        subprocess.run(['openssl','genrsa','-out',str(private),'3072'],check=True)
        subprocess.run(['openssl','rsa','-in',str(private),'-pubout','-out',str(public)],check=True)
        print('Keys created. Keep private.pem private; only public.pem is installed in the bot.')
    else:
        if not all((a.source,a.key,a.notes)):raise SystemExit('Build requires --source --key --notes')
        tree=ast.parse((a.source/'version.py').read_text())
        version=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
        from updater import version_tuple
        version_tuple(version)
        archive=a.directory/'app.zip';hashes={}
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for name in FILES:
                data=(a.source/name).read_bytes();hashes[name]=hashlib.sha256(data).hexdigest();z.writestr(name,data)
        manifest={'format':1,'version':version,'notes':a.notes.read_text(),'files':hashes,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        (a.directory/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
        subprocess.run(['openssl','dgst','-sha256','-sign',str(a.key),'-out',str(a.directory/'manifest.sig'),str(a.directory/'manifest.json')],check=True)
        print('Publish only app.zip, manifest.json and manifest.sig as GitHub release assets.')

if __name__=='__main__':main()
