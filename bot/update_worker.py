"""Independent launchd update job; restart failure restores app and data snapshots."""
import fcntl
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.request import urlopen
import mac_setup
from mac_update import FILES, apply_update
from updater import verify, unpack, save

def restore(base,backup):
    for name in FILES:
        if (backup/name).exists():shutil.copy2(backup/name,base/name)
    if (base/'data').exists():(base/'data').rename(backup/'failed-data')
    shutil.copytree(backup/'data',base/'data')

def main():
    base=Path(__file__).resolve().parent;root=base/'updates';stage=Path(sys.argv[1]).resolve()
    if stage.parent!=root.resolve():raise ValueError('Invalid staged release location')
    with (root/'install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        backup=None;stopped=False;backup_complete=False
        try:
            manifest=verify(stage,root/'public.pem');source=unpack(stage,manifest)
            mac_setup.stop();stopped=True
            backup=stage/'rollback';backup.mkdir(mode=0o700)
            for name in FILES:shutil.copy2(base/name,backup/name)
            shutil.copytree(base/'data',backup/'data')
            backup_complete=True
            (backup/'COMPLETE').touch(mode=0o600)
            (base/'data'/'update_hold').touch(mode=0o600)
            apply_update(base,source)
            mac_setup.start()
            with urlopen(mac_setup.URL+'/healthz',timeout=5) as response:health=json.load(response)
            if not health.get('running') or health.get('version')!=manifest['version']:
                raise ValueError('New service version or health check failed')
            (base/'data'/'update_hold').unlink(missing_ok=True)
            save(root/'status.json',{'available':False,'message':'Update installed successfully','completed_at':time.time()})
        except BaseException:
            try:
                if backup_complete:
                    mac_setup.stop();restore(base,backup);mac_setup.start()
                    message='Update failed; previous application and data restored.'
                elif stopped:
                    mac_setup.start();message='Update failed before replacement; previous service restarted.'
                else:message='Update aborted before replacement; existing bot unchanged.'
            except BaseException:
                message='Update/recovery failed. Use Mac_Setup.command option 3 and retain the updates folder for recovery.'
            save(root/'status.json',{'available':False,'message':message,'completed_at':time.time()})

if __name__=='__main__':main()
