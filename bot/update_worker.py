"""Independent launchd update job; restart failure restores app and data snapshots."""
import fcntl
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
    if (base/'data').exists():(base/'data').rename(backup/'failed-data')
    shutil.copytree(backup/'data',base/'data')

def transaction(base,source,stage,expected):
    base=Path(base);stage=Path(stage);root=base/'updates'
    backup=None;stopped=False;backup_complete=False
    try:
        require_space(base)
        mac_setup.stop();stopped=True
        backup=stage/'rollback';backup.mkdir(mode=0o700)
        for name in FILES:
            if (base/name).exists():shutil.copy2(base/name,backup/name)
        shutil.copytree(base/'data',backup/'data')
        backup_complete=True
        (backup/'COMPLETE').touch(mode=0o600)
        (base/'data'/'update_hold').touch(mode=0o600)
        apply_update(base,source)
        mac_setup.start()
        with urlopen(mac_setup.URL+'/healthz',timeout=5) as response:health=json.load(response)
        if not health.get('running') or health.get('version')!=expected:
            raise ValueError('New service version or health check failed')
        (base/'data'/'update_hold').unlink(missing_ok=True)
        save(root/'status.json',{'available':False,'message':'Update installed successfully','completed_at':time.time()})
        (stage/'RESOLVED').touch(mode=0o600)
        try:cleanup_updates(base,protected=(stage.name,))
        except OSError:pass
        return True
    except BaseException:
        try:
            if backup_complete:
                mac_setup.stop();restore(base,backup);mac_setup.start()
                message='Update failed; previous application and data restored.'
            elif stopped:
                mac_setup.start();message='Update failed before replacement; previous service restarted.'
            else:message='Update aborted before replacement; existing bot unchanged. Check available disk space and package files.'
            (stage/'RESOLVED').touch(mode=0o600)
        except BaseException:
            message='Update/recovery failed. Use Mac_Setup.command option 3 and retain the updates folder for recovery.'
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
