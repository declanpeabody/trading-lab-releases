import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import updater
import update_worker
from mac_update import FILES

class SignedUpdates(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.stage=self.root/'stage';self.stage.mkdir()
        self.private=self.root/'private.pem';self.public=self.root/'public.pem'
        subprocess.run(['openssl','genrsa','-out',str(self.private),'2048'],check=True,capture_output=True)
        subprocess.run(['openssl','rsa','-in',str(self.private),'-pubout','-out',str(self.public)],check=True,capture_output=True)
        self.files={name:b'test contents' for name in FILES}
        self.build()
    def build(self):
        with zipfile.ZipFile(self.stage/'app.zip','w') as z:
            for name,data in self.files.items():z.writestr(name,data)
        self.manifest={'format':1,'version':'2099.1.1.1','notes':'Test release','files':{n:hashlib.sha256(d).hexdigest() for n,d in self.files.items()},'archive_sha256':hashlib.sha256((self.stage/'app.zip').read_bytes()).hexdigest()}
        self.sign()
    def sign(self):
        (self.stage/'manifest.json').write_text(json.dumps(self.manifest))
        subprocess.run(['openssl','dgst','-sha256','-sign',str(self.private),'-out',str(self.stage/'manifest.sig'),str(self.stage/'manifest.json')],check=True,capture_output=True)
    def test_verified_release(self):
        m=updater.verify(self.stage,self.public);target=updater.unpack(self.stage,m)
        self.assertEqual((target/'app.py').read_bytes(),b'test contents')
    def test_tampered_manifest(self):
        (self.stage/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'signature'):updater.verify(self.stage,self.public)
    def test_downgrade(self):
        with self.assertRaisesRegex(ValueError,'newer'):updater.verify(self.stage,self.public,current='2099.1.1.1')
    def test_archive_tampering(self):
        (self.stage/'app.zip').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'hash'):updater.unpack(self.stage,self.manifest)
    def test_traversal(self):
        self.files['../outside']=b'bad';self.build()
        with self.assertRaisesRegex(ValueError,'unsafe'):updater.unpack(self.stage,self.manifest)
    def test_per_file_hash(self):
        self.manifest['files']['app.py']='0'*64
        with self.assertRaisesRegex(ValueError,'file hash'):updater.unpack(self.stage,self.manifest)
    def test_pinned_channel(self):
        u=updater.Updates(self.root/'bot');u.configure('owner/repo',self.public.read_text())
        with self.assertRaisesRegex(ValueError,'pinned'):u.configure('owner/other',self.public.read_text())
    def test_private_key_refused(self):
        u=updater.Updates(self.root/'bot')
        with self.assertRaises(ValueError):u.configure('owner/repo',self.private.read_text())
    def test_url_boundary(self):
        for url in ['http://github.com/a','https://github.com.evil/a','https://user:pass@github.com/a','https://github.com:444/a']:
            with self.assertRaises(ValueError):updater.check_url(url)

class Rollback(unittest.TestCase):
    def run_update(self,partial=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);base=Path(tmp.name)
        stage=base/'updates'/'stage';stage.mkdir(parents=True)
        (base/'data').mkdir();(base/'data'/'account').write_text('original account')
        for name in FILES:(base/name).write_text('old '+name)
        original_copy=shutil.copytree
        def copy(src,dst,*args,**kw):
            if partial and Path(dst)==stage/'rollback'/'data':
                Path(dst).mkdir();raise OSError('disk full')
            return original_copy(src,dst,*args,**kw)
        def apply(*args):
            (base/'app.py').write_text('new broken app');(base/'data'/'account').write_text('changed')
        with patch.object(update_worker,'__file__',str(base/'update_worker.py')),patch('sys.argv',['worker',str(stage)]),patch.object(update_worker,'verify',return_value={'version':'2099.1.1.1'}),patch.object(update_worker,'unpack',return_value=stage),patch.object(update_worker.mac_setup,'stop'),patch.object(update_worker.mac_setup,'start',side_effect=[SystemExit('failed startup'),None] if not partial else [None]),patch.object(update_worker,'apply_update',side_effect=apply),patch.object(update_worker.shutil,'copytree',side_effect=copy):
            update_worker.main()
        self.assertEqual((base/'app.py').read_text(),'old app.py')
        self.assertEqual((base/'data'/'account').read_text(),'original account')
        self.assertFalse((base/'data'/'update_hold').exists())
        return json.loads((base/'updates'/'status.json').read_text())
    def test_startup_failure_restores_code_and_data(self):
        self.assertIn('restored',self.run_update()['message'])
    def test_partial_backup_never_replaces_original_data(self):
        self.assertIn('before replacement',self.run_update(partial=True)['message'])
