"""Replace app files only; preserve credentials and the complete paper account."""
from datetime import datetime
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3

FILES=('app.py','engine.py','market.py','dashboard.html','mac_runner.py','mac_setup.py',
       'backtest.py','research.py','research.html','mac_update.py','scanner.py','news.py','news.html',
       'strategy.py','ranked_scanner.py','ranked_backtest.py','version.py','updater.py','update_worker.py','updates.html')

def apply_update(base,source):
    base,source=Path(base),Path(source)
    if base.resolve()==source.resolve():raise ValueError('Run the updater from the newly downloaded folder.')
    if not (base/'config.json').is_file():raise ValueError('No installed Mac bot found. Choose Install instead.')
    for filename in FILES:
        if not (source/filename).is_file():raise ValueError('Incomplete update package: '+filename)
    lock=(base/'data'/'worker.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();raise ValueError('The old bot is still running. Stop it before updating.') from None
    backup=base/'backups'/datetime.now().strftime('%Y%m%dT%H%M%S%f')
    backup.mkdir(parents=True,mode=0o700)
    existed=[];changed=[]
    try:
        for filename in FILES:
            if (base/filename).exists():
                shutil.copy2(base/filename,backup/filename);existed.append(filename)
        db=base/'data'/'paper.sqlite3'
        if db.exists():
            with sqlite3.connect(db) as src,sqlite3.connect(backup/'paper.sqlite3') as dest:src.backup(dest)
        for filename in FILES:
            temp=base/(filename+'.new');shutil.copy2(source/filename,temp)
            os.replace(temp,base/filename);changed.append(filename)
    except Exception:
        for filename in changed:
            if filename in existed:shutil.copy2(backup/filename,base/filename)
            else:(base/filename).unlink(missing_ok=True)
        raise
    finally:lock.close()
    return backup
