"""Idempotent publication gate. Never overwrite a release or publish a downgrade."""
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parent/'bot'))
from version import VERSION
from updater import version_tuple

REPO='declanpeabody/trading-lab-releases'
ASSETS={'app.zip','manifest.json','manifest.sig','public.pem','Trading_Bot_Mac_Setup.zip'}

def get(path):
    request=Request('https://api.github.com/repos/'+REPO+path,
        headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
    try:
        with urlopen(request,timeout=30) as response:return json.load(response)
    except HTTPError as exc:
        if exc.code==404:return None
        raise

def needed(version=VERSION,fetch=get):
    version_tuple(version)
    release=fetch('/releases/tags/v'+version)
    if release is not None:
        assets={a['name'] for a in release.get('assets',[]) if a.get('size',0)>0}
        if release.get('draft') or release.get('prerelease') or not ASSETS.issubset(assets):
            raise ValueError('Existing version has an incomplete or non-stable release; review it instead of overwriting assets.')
        return False
    latest=fetch('/releases/latest')
    if latest and version_tuple(latest['tag_name'].removeprefix('v'))>=version_tuple(version):
        raise ValueError('Increase bot/version.py before publishing; downgrades are refused.')
    return True

if __name__=='__main__':
    publish=needed()
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('needed='+str(publish).lower()+'\n')
    print('New version will be signed and published.' if publish else 'Complete release already exists; nothing to publish.')
