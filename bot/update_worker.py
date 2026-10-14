"""Independent launchd update job; restart failure restores app and data snapshots."""
import fcntl
import hashlib
import sqlite3
import os
import uuid
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.request import urlopen
import mac_setup
from mac_update import FILES, apply_update, require_space, cleanup_updates
from updater import verify, unpack, save

def restore(base,backup):
    for name in FILES:
        if (backup/name).exists():shutil.copy2(backup/name,base/name)
    if (base/'data').exists():(base/'data').rename(backup/('failed-data-'+uuid.uuid4().hex))
    shutil.copytree(backup/'data',base/'data')

def durable_record(path,value):
    temporary=path.with_suffix('.tmp')
    with open(temporary,'w',opener=lambda p,f:os.open(p,f,0o600)) as handle:
        json.dump(value,handle);handle.flush();os.fsync(handle.fileno())
    temporary.replace(path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def snapshot_hashes(backup):
    files=[p for p in backup.rglob('*') if p.is_file() and p.name!='COMPLETE']
    if any(p.is_symlink() for p in backup.rglob('*')):raise ValueError('Symlink in recovery snapshot')
    return {str(p.relative_to(backup)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def recover_interrupted(base):
    """Explicit local recovery, never automatically discard current account data."""
    base=Path(base);root=base/'updates'
    with (root/'install.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('An updater is still running; wait for it to finish.') from None
        record=json.loads((root/'transaction.json').read_text())
        if not isinstance(record,dict) or not isinstance(record.get('stage'),str) or record.get('phase') not in ('backed_up','replacing','verifying','verified','recovering'):
            raise ValueError('No valid interrupted transaction record; existing files preserved.')
        if not (base/'data'/'update_hold').exists() and record.get('phase')!='recovering':
            raise ValueError('No interrupted trading hold found. Do not roll back an active account; use option 3 to start the service if needed.')
        stage=(root/record['stage']).resolve()
        if stage.parent!=root.resolve():raise ValueError('Invalid recovery path')
        backup=stage/'rollback';hashes=record.get('backup_hashes',{})
        if not (backup/'COMPLETE').is_file() or not hashes or not set(FILES).issubset(hashes) or 'data/paper.sqlite3' not in hashes:
            raise ValueError('Complete verified recovery snapshot unavailable; existing files preserved.')
        for name,digest in hashes.items():
            path=backup/name
            if path.resolve().is_relative_to(backup.resolve()) is False or path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Recovery snapshot failed integrity validation; existing files preserved.')
        with sqlite3.connect((backup/'data/paper.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Recovery account database failed validation')
        require_space(base)
        mac_setup.stop()
        durable_record(root/'transaction.json',record|{'phase':'recovering'})
        restore(base,backup)
        mac_setup.start()
        durable_record(root/'transaction.json',record|{'phase':'recovered'})
        (stage/'RESOLVED').touch(mode=0o600)
        save(root/'status.json',{'available':False,'installing':False,'message':'Interrupted update recovered: verified previous application and account restored.'})
        return True


def transaction(base,source,stage,expected):
    base=Path(base);stage=Path(stage);root=base/'updates'
    backup=None;stopped=False;backup_complete=False
    try:
        require_space(base)
        durable_record(root/'transaction.json',{'stage':stage.name,'expected':expected,'phase':'stopping'})
        mac_setup.stop();stopped=True
        backup=stage/'rollback';backup.mkdir(mode=0o700)
        for name in FILES:
            if (base/name).exists():shutil.copy2(base/name,backup/name)
        shutil.copytree(base/'data',backup/'data')
        (backup/'COMPLETE').touch(mode=0o600)
        record={'stage':stage.name,'expected':expected,'phase':'backed_up','backup_hashes':snapshot_hashes(backup)}
        durable_record(root/'transaction.json',record)
        backup_complete=True
        durable_record(base/'data'/'update_hold',{'stage':stage.name})
        durable_record(root/'transaction.json',record|{'phase':'replacing'})
        apply_update(base,source)
        durable_record(root/'transaction.json',record|{'phase':'verifying'})
        mac_setup.start()
        with urlopen(mac_setup.URL+'/healthz',timeout=5) as response:health=json.load(response)
        if not health.get('running') or health.get('version')!=expected:
            raise ValueError('New service version or health check failed')
        durable_record(root/'transaction.json',record|{'phase':'verified'})
        (base/'data'/'update_hold').unlink(missing_ok=True)
        durable_record(root/'transaction.json',record|{'phase':'complete'})
        save(root/'status.json',{'available':False,'message':'Update installed successfully','completed_at':time.time()})
        (stage/'RESOLVED').touch(mode=0o600)
        try:cleanup_updates(base,protected=(stage.name,))
        except OSError:pass
        return True
    except BaseException:
        try:
            if backup_complete:
                mac_setup.stop()
                durable_record(root/'transaction.json',record|{'phase':'recovering'})
                restore(base,backup);mac_setup.start()
                durable_record(root/'transaction.json',record|{'phase':'recovered'})
                message='Update failed; previous application and data restored.'
            elif stopped:
                mac_setup.start();message='Update failed before replacement; previous service restarted.'
            else:message='Update aborted before replacement; existing bot unchanged. Check available disk space and package files.'
            (stage/'RESOLVED').touch(mode=0o600)
        except BaseException:
            message='Update/recovery failed. Use Mac_Setup.command option 11 for verified recovery and retain the updates folder for recovery.'
        save(root/'status.json',{'available':False,'message':message,'completed_at':time.time()})
        return False


def manual_update(base,source):
    import ast
    import uuid
    base=Path(base);source=Path(source)
    # Read the literal version without importing the incoming application.
    values=[n.value.value for n in ast.parse((source/'version.py').read_text()).body
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets)
            and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)]
    if len(values)!=1:raise ValueError('Update package has no valid version')
    for name in FILES:
        if not (source/name).is_file():raise ValueError('Incomplete update package: '+name)
    require_space(base)
    root=base/'updates';root.mkdir(exist_ok=True)
    with (root/'install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (base/'data'/'update_hold').exists():raise ValueError('Resolve the previous update before installing another.')
        stage=root/('manual_'+uuid.uuid4().hex);stage.mkdir(mode=0o700)
        if not transaction(base,source,stage,values[0]):
            raise ValueError(json.loads((root/'status.json').read_text())['message'])
        return stage/'rollback'


def main():
    base=Path(__file__).resolve().parent;root=base/'updates';stage=Path(sys.argv[1]).resolve()
    if stage.parent!=root.resolve():raise ValueError('Invalid staged release location')
    with (root/'install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            require_space(base)
            manifest=verify(stage,root/'public.pem');source=unpack(stage,manifest)
        except Exception:
            save(root/'status.json',{'available':False,'message':'Update verification or disk-space check failed; existing bot unchanged.'})
            return
        transaction(base,source,stage,manifest['version'])

if __name__=='__main__':main()
