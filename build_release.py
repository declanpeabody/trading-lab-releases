"""Build only audited source files; the signing key is never put in an asset."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'bot'
sys.path.insert(0,str(SOURCE))
from mac_update import FILES
from updater import verify,unpack

def check_js():
    for page in SOURCE.glob('*.html'):
        for script in re.findall(r'<script[^>]*>(.*?)</script>',page.read_text(),re.S):
            with tempfile.NamedTemporaryFile(mode='w',suffix='.js') as f:
                f.write(script);f.flush();subprocess.run(['node','--check',f.name],check=True)

def build():
    key=os.environ.pop('RELEASE_SIGNING_KEY','')
    if not key.strip():raise SystemExit('Add the RELEASE_SIGNING_KEY repository Actions secret before publishing.')
    os.umask(0o077)
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        private=Path(temporary)/'private.pem';private.write_text(key);private.chmod(0o600);key=''
        subprocess.run(['openssl','rsa','-in',str(private),'-check','-noout'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['openssl','rsa','-in',str(private),'-pubout','-out',str(output/'public.pem')],check=True)
        subprocess.run([sys.executable,str(SOURCE/'release_tools.py'),'build',str(output),
            '--source',str(SOURCE),'--key',str(private),'--notes',str(ROOT/'release-notes.md')],check=True)
        manifest=verify(output,output/'public.pem',current='2000.1.1.0');unpack(output,manifest)
    extras=('Mac_Setup.command','README.md','START_HERE_MAC.md','VALIDATION.md',
            'RELEASE_PUBLISHING.md','release_tools.py','broker_check.py','Dockerfile','compose.yaml')
    with zipfile.ZipFile(output/'Trading_Bot_Mac_Setup.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in dict.fromkeys((*FILES,*extras)):z.write(SOURCE/name,'Trading_Bot_Mac/'+name)
        for test in sorted((SOURCE/'tests').glob('test_*.py')):z.write(test,'Trading_Bot_Mac/tests/'+test.name)
    print('Signed application verified. Public assets ready; private key excluded.')

if __name__=='__main__':
    if sys.argv[1:]==['--check-js']:check_js()
    elif sys.argv[1:]:raise SystemExit('Usage: build_release.py [--check-js]')
    else:build()
