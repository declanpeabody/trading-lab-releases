"""User-approved signed updates. No broker credentials or automatic installation."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid
import zipfile
from version import VERSION
from mac_update import FILES

def version_tuple(value):
    if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d{1,2}\.\d{1,6}',value):raise ValueError('Invalid release version')
    return tuple(map(int,value.split('.')))

def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value));tmp.chmod(0o600);tmp.replace(path)

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        check_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def check_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or p.hostname not in (
        'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):
        raise ValueError('Release URL is outside permitted HTTPS hosts')

def download(url,limit):
    check_url(url)
    with build_opener(SafeRedirect()).open(Request(url,headers={'User-Agent':'DeclanTradingLab-Updater'}),timeout=30) as r:
        value=r.read(limit+1)
    if len(value)>limit:raise ValueError('Release download exceeds size limit')
    return value

def verify(stage,public_key,current=VERSION):
    stage=Path(stage)
    result=subprocess.run(['openssl','dgst','-sha256','-verify',str(public_key),'-signature',
        str(stage/'manifest.sig'),str(stage/'manifest.json')],capture_output=True,timeout=10)
    if result.returncode:raise ValueError('Release signature verification failed')
    manifest=json.loads((stage/'manifest.json').read_text())
    if version_tuple(manifest['version'])<=version_tuple(current):raise ValueError('Release is not newer than installed version')
    if manifest.get('format')!=1 or set(manifest.get('files',{}))!=set(FILES):raise ValueError('Release file set is incompatible with this updater')
    if not isinstance(manifest.get('notes'),str) or len(manifest['notes'])>10000:raise ValueError('Invalid release notes')
    return manifest

def unpack(stage,manifest):
    stage=Path(stage);package=stage/'app.zip'
    if hashlib.sha256(package.read_bytes()).hexdigest()!=manifest['archive_sha256']:raise ValueError('Release archive hash mismatch')
    target=stage/'app';target.mkdir(mode=0o700,exist_ok=True)
    with zipfile.ZipFile(package) as z:
        entries=z.infolist()
        if len(entries)!=len(FILES) or set(z.namelist())!=set(FILES):raise ValueError('Unexpected or unsafe ZIP entries')
        if sum(i.file_size for i in entries)>20_000_000:raise ValueError('Unpacked release too large')
        for entry in entries:
            if ((entry.external_attr>>16)&0o170000)==0o120000:raise ValueError('Symlinks forbidden in release')
            data=z.read(entry)
            if hashlib.sha256(data).hexdigest()!=manifest['files'][entry.filename]:raise ValueError('Release file hash mismatch')
            (target/entry.filename).write_bytes(data)
    return target

class Updates:
    def __init__(self,base):
        self.base=Path(base);self.root=self.base/'updates';self.root.mkdir(parents=True,exist_ok=True)
        self.config=self.root/'channel.json';self.key=self.root/'public.pem';self.status_path=self.root/'status.json'
        self.lock=threading.Lock();self.busy=False;self.last_check=0

    def state(self):
        info={}
        if self.status_path.exists():
            try:info=json.loads(self.status_path.read_text())
            except (OSError,ValueError):pass
        return info|{'installed_version':VERSION,'configured':self.config.exists() and self.key.exists(),'busy':self.busy}

    def configure(self,repo,key):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Use owner/repository')
        if len(key)>10000 or not key.startswith('-----BEGIN PUBLIC KEY-----'):raise ValueError('Paste the trusted release public key, not a private key')
        if 'PRIVATE' in key:raise ValueError('Private keys must never be installed in the bot')
        with self.lock:
            if self.busy:raise ValueError('An update operation is already running')
            if self.config.exists():raise ValueError('Channel already pinned; changing trust requires local manual setup')
            tmp=self.root/'public-new.pem';tmp.write_text(key);tmp.chmod(0o600)
            try:
                if subprocess.run(['openssl','rsa','-pubin','-in',str(tmp),'-noout'],capture_output=True,timeout=10).returncode:
                    raise ValueError('Invalid RSA public key')
                tmp.replace(self.key);save(self.config,{'repo':repo})
            finally:tmp.unlink(missing_ok=True)

    def check(self):
        with self.lock:
            if self.busy:raise ValueError('An update operation is already running')
            if not self.state()['configured']:raise ValueError('Release channel has not been configured')
            if self.state().get('installing'):raise ValueError('Installation is in progress; wait for the service to restart')
            if time.time()-self.last_check<60:raise ValueError('Wait a minute before checking again')
            self.busy=True;self.last_check=time.time()
        threading.Thread(target=self._check,daemon=True).start()

    def _check(self):
        try:
            repo=json.loads(self.config.read_text())['repo'];root=f'https://github.com/{repo}/releases/latest/download/'
            stage=self.root/uuid.uuid4().hex;stage.mkdir(mode=0o700)
            for name in ('manifest.json','manifest.sig'):(stage/name).write_bytes(download(root+name,100000))
            # Verify authenticity even when the release is the same version.
            result=subprocess.run(['openssl','dgst','-sha256','-verify',str(self.key),'-signature',str(stage/'manifest.sig'),str(stage/'manifest.json')],capture_output=True,timeout=10)
            if result.returncode:raise ValueError('Release signature verification failed')
            value=json.loads((stage/'manifest.json').read_text())
            if version_tuple(value['version'])<=version_tuple(VERSION):
                save(self.status_path,{'message':'No newer signed release available','available':False,'checked_at':time.time()});return
            manifest=verify(stage,self.key)
            (stage/'app.zip').write_bytes(download(root+'app.zip',10_000_000))
            unpack(stage,manifest)
            save(self.status_path,{'message':'Signed release ready for your review','available':True,
                'version':manifest['version'],'notes':manifest['notes'],'stage':stage.name,'checked_at':time.time()})
        except Exception as exc:
            save(self.status_path,{'available':False,'message':str(exc) if isinstance(exc,ValueError) else 'Release check failed; installed bot unchanged.'})
        finally:self.busy=False

    def install(self,version):
        import sys
        if sys.platform!='darwin':raise ValueError('Dashboard installation is supported on the Mac setup only')
        with self.lock:
            if self.busy:raise ValueError('An update operation is already running')
            state=self.state()
            if not state.get('available') or state.get('version')!=version:raise ValueError('Check and review the release first')
            stage=self.root/state['stage'];manifest=verify(stage,self.key);unpack(stage,manifest)
            config=json.loads((self.base/'config.json').read_text())
            # Independent launchd job survives stopping the trading service.
            label='uk.declan.tradinglab.update.'+uuid.uuid4().hex
            job=stage/'job.plist'
            job.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':[config['python'],str(self.base/'update_worker.py'),str(stage)],
                'WorkingDirectory':str(self.base),'RunAtLoad':True,'KeepAlive':False,'Umask':0o077,
                'StandardOutPath':str(stage/'update.log'),'StandardErrorPath':str(stage/'update.log')}));job.chmod(0o600)
            save(self.status_path,state|{'available':False,'installing':True,'message':'Update scheduled. The dashboard will briefly disconnect.'})
            result=subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(job)],capture_output=True,timeout=15)
            if result.returncode:
                save(self.status_path,state|{'message':'macOS could not launch the updater; bot unchanged.'})
                raise ValueError('macOS could not launch the independent updater')

    def worker(self,stop):
        while not stop.wait(60):
            if self.state()['configured'] and time.time()-self.last_check>86400:
                try:self.check()
                except ValueError:pass
