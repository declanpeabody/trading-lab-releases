"""Replace app files only; preserve credentials and the complete paper account."""
from datetime import datetime
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3

def tree_size(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file() and not p.is_symlink()) if Path(path).exists() else 0

def require_space(base, extra=0):
    """Allow a complete snapshot, failed-data preservation and temporary app copies."""
    base=Path(base)
    required=2*tree_size(base/'data')+3*sum((base/n).stat().st_size for n in FILES if (base/n).is_file())+extra+100_000_000
    if shutil.disk_usage(base).free<required:
        raise ValueError('Insufficient free disk space for a safe update and rollback. Free disk space, then retry; existing bot unchanged.')

def prune_files(directory, pattern, keep, max_bytes):
    files=sorted((p for p in Path(directory).glob(pattern) if p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
    used=0
    for i,p in enumerate(files):
        used+=p.stat().st_size
        if i>=keep or used>max_bytes:p.unlink()

def cleanup_updates(base, protected=()):
    """Only discard resolved snapshots or unused stages; never unresolved recovery."""
    import re
    base=Path(base);protected=set(protected)
    candidates=[]
    for root in (base/'updates',base/'backups'):
        if not root.exists():continue
        for p in root.iterdir():
            if not p.is_dir() or p.is_symlink() or p.name in protected:continue
            if root.name=='updates':
                if not re.fullmatch(r'(manual_)?[0-9a-f]{32}',p.name):continue
                if (p/'rollback').exists() and not (p/'RESOLVED').exists():continue
                if (p/'job.plist').exists() and not (p/'RESOLVED').exists():continue
            elif not (p/'RESOLVED').exists():continue
            candidates.append(p)
    candidates.sort(key=lambda p:p.stat().st_mtime,reverse=True)
    for p in candidates[3:]:shutil.rmtree(p)

FILES=('app.py','engine.py','market.py','dashboard.html','mac_runner.py','mac_setup.py',
       'backtest.py','research.py','research.html','mac_update.py','scanner.py','news.py','news.html',
       'strategy.py','ranked_scanner.py','ranked_backtest.py','version.py','updater.py','update_worker.py','updates.html')

def apply_update(base,source):
    base,source=Path(base),Path(source)
    if base.resolve()==source.resolve():raise ValueError('Run the updater from the newly downloaded folder.')
    if not (base/'config.json').is_file():raise ValueError('No installed Mac bot found. Choose Install instead.')
    for filename in FILES:
        if not (source/filename).is_file():raise ValueError('Incomplete update package: '+filename)
    require_space(base)
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
    (backup/'RESOLVED').touch(mode=0o600)
    return backup
