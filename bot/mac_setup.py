"""Per-user Mac setup. Paper trading only; no broker execution."""
import fcntl
import getpass
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

LABEL = 'uk.declan.tradinglab'
BASE = Path.home() / 'Library' / 'Application Support' / 'DeclanTradingLab'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
SOURCE = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8765'
from mac_update import FILES, apply_update
from market import validate_key

def write_private(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle: handle.write(data)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)

def manifest(base, python):
    return dict(Label=LABEL, ProgramArguments=[str(python),str(base/'mac_runner.py')],
        WorkingDirectory=str(base), RunAtLoad=True, KeepAlive=True,
        ThrottleInterval=30, Umask=0o077,
        StandardOutPath=str(base/'service.log'),StandardErrorPath=str(base/'errors.log'))

def stop(timeout=30):
    target = f'gui/{os.getuid()}/{LABEL}'
    result = subprocess.run(['/bin/launchctl','bootout',target],
                            capture_output=True, text=True)
    deadline = time.monotonic() + timeout
    while True:
        registered = subprocess.run(['/bin/launchctl','print',target],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        released = True
        lock_path = BASE/'data'/'worker.lock'
        if lock_path.exists():
            with lock_path.open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    released = False
        if registered.returncode != 0 and released:
            return
        if time.monotonic() >= deadline:
            detail = ' macOS rejected the stop request.' if result.returncode else ''
            raise RuntimeError('The bot did not finish stopping within 30 seconds.' + detail +
                               ' No update files were replaced. Close this setup window, '
                               'restart your Mac, then retry option 8 from the new download.')
        time.sleep(0.25)

def start():
    config = json.loads((BASE/'config.json').read_text())
    if not Path(config['python']).is_file():
        raise SystemExit('The installed Python has moved. Restore it before starting the bot.')
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(manifest(BASE,config['python'])))
    PLIST.chmod(0o600)
    stop()
    result = subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(PLIST)],
                            capture_output=True,text=True)
    if result.returncode:
        raise SystemExit('macOS could not start the login service. Check Login Items settings and the guide.')
    for _ in range(20):
        try:
            with urllib.request.urlopen(URL+'/healthz', timeout=1) as response:
                if json.load(response).get('running'):
                    print('Local service is running:',URL)
                    subprocess.run(['/usr/bin/open',URL],check=False)
                    return
        except Exception: pass
        time.sleep(.5)
    raise SystemExit('Startup not confirmed. Check errors.log in '+str(BASE))

def install():
    if BASE.exists():
        raise SystemExit('Already installed, or a previous install needs review. Existing files preserved; use Start or Configure.')
    print('Installing a PAPER-ONLY bot. It will start when you log in.')
    key = validate_key(getpass.getpass('Twelve Data API key (hidden; Enter to add later): '),allow_empty=True)
    BASE.mkdir(parents=True,mode=0o700)
    BASE.chmod(0o700)
    (BASE/'data').mkdir(mode=0o700)
    for filename in FILES: shutil.copyfile(SOURCE/filename,BASE/filename)
    password = secrets.token_urlsafe(24)
    write_private(BASE/'config.json',json.dumps(dict(
        python=str(Path(sys.executable).resolve()),DASHBOARD_USER='declan',
        DASHBOARD_PASSWORD=password,TWELVE_DATA_API_KEY=key),indent=2))
    print('\nDashboard username: declan\nDashboard password:',password)
    print('Save this privately. Do not send a screenshot containing the password.\n')
    start()
    print('The paper strategy starts PAUSED. Add a data key before starting paper trades.')

def configure():
    path=BASE/'config.json'
    config=json.loads(path.read_text())
    key=getpass.getpass('New Twelve Data API key (hidden; Enter keeps current key): ').strip()
    if key: config['TWELVE_DATA_API_KEY']=validate_key(key)
    write_private(path,json.dumps(config,indent=2))
    start()

def main():
    if sys.platform != 'darwin': raise SystemExit('Run this installer on your Mac.')
    if sys.version_info < (3,12): raise SystemExit('Install Python 3.12 or newer from python.org.')
    os.umask(0o077)
    print('\nDeclan Trading Lab — Mac setup (simulated trades only)')
    print('1. Install\n2. Open dashboard\n3. Start / restore login startup\n4. Stop until next login\n5. Change market-data key\n6. Disable login startup and stop\n7. Show local dashboard password')
    print('8. Upgrade installed bot (preserve settings and history)')
    action=input('Choose 1–8: ').strip()
    if action=='1': install()
    elif action=='2': subprocess.run(['/usr/bin/open',URL],check=False)
    elif action=='3': start()
    elif action=='4':
        stop()
        print('Stopped. It will start at your next login.')
    elif action=='5': configure()
    elif action=='6':
        stop()
        PLIST.unlink(missing_ok=True)
        print('Login startup disabled. Code, credentials and paper history were kept.')
    elif action=='7':
        c=json.loads((BASE/'config.json').read_text())
        print('Username:',c['DASHBOARD_USER'],'\nPassword:',c['DASHBOARD_PASSWORD'])
        print('Keep this private; do not send a screenshot of it.')
    elif action=='8':
        from update_worker import manual_update
        backup=manual_update(BASE,SOURCE)
        print('Updated. Settings and paper history preserved. Backup:',backup)
        subprocess.run(['/usr/bin/open',URL+'/research'],check=False)
    else: print('No changes made.')

if __name__=='__main__':
    try: main()
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from None
    except FileNotFoundError:
        raise SystemExit('Installation or Python file missing. Run Install first; preserve any existing data.') from None
