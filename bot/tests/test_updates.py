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

class AutomaticUpdates(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.u=updater.Updates(self.base)
        updater.save(self.u.config,{'repo':'owner/repo'});self.u.key.write_text('test')
        updater.save(self.base/'config.json',{'python':'python3'})
        self.stage=self.u.root/'ready';self.stage.mkdir()
        updater.save(self.u.status_path,{'available':True,'version':'2099.1.1.1','stage':'ready'})
    def test_opt_in_persists_and_requires_boolean(self):
        self.assertFalse(self.u.state()['automatic'])
        self.u.set_automatic(True)
        self.assertTrue(updater.Updates(self.base).state()['automatic'])
        with self.assertRaises(ValueError):self.u.set_automatic('true')
        self.u.set_automatic(False)
        self.assertFalse(updater.Updates(self.base).state()['automatic'])
    def test_window_boundaries_and_dst(self):
        from datetime import datetime
        for stamp,expected in [('2026-10-12T11:59:00+00:00',True),('2026-10-12T12:00:00+00:00',False),('2026-10-12T20:59:00+00:00',False),('2026-10-12T21:00:00+00:00',True),('2026-10-17T15:00:00+00:00',True),('2026-11-02T21:59:00+00:00',False),('2026-11-02T22:00:00+00:00',True)]:
            self.assertEqual(updater.automatic_window(datetime.fromisoformat(stamp)),expected,stamp)
    def test_tick_waits_for_opt_in_and_window(self):
        self.u.last_check=updater.time.time()
        with patch.object(self.u,'install') as install,patch.object(updater,'automatic_window',return_value=True):
            self.u.tick();install.assert_not_called()
            self.u.set_automatic(True);self.u.tick()
            install.assert_called_once_with('2099.1.1.1',automatic=True)
        with patch.object(self.u,'install') as install,patch.object(updater,'automatic_window',return_value=False):
            self.u.tick();install.assert_not_called()
    def test_interrupted_attempt_persists_and_newer_release_can_proceed(self):
        self.u.set_automatic(True)
        updater.save(self.u.attempt_path,{'version':'2099.1.1.1'})
        u=updater.Updates(self.base);u.last_check=updater.time.time()
        with patch.object(u,'install') as install,patch.object(updater,'automatic_window',return_value=True):
            u.tick();install.assert_not_called()
            updater.save(u.status_path,{'available':True,'version':'2099.1.1.2','stage':'ready'})
            u.tick();install.assert_called_once_with('2099.1.1.2',automatic=True)
    def test_hold_and_installing_prevent_background_work(self):
        self.u.set_automatic(True);self.u.last_check=updater.time.time()
        (self.base/'data').mkdir();(self.base/'data'/'update_hold').touch()
        with patch.object(self.u,'install') as install,patch.object(updater,'automatic_window',return_value=True):
            self.u.tick();install.assert_not_called()
        updater.save(self.u.status_path,{'installing':True})
        with patch.object(self.u,'check') as check:
            self.u.last_check=0;self.u.tick();check.assert_not_called()
    def launch(self,automatic=True,result=0,exception=None,version='2099.1.1.1'):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch('sys.platform','darwin'))
            stack.enter_context(patch.object(updater,'automatic_window',return_value=True))
            stack.enter_context(patch.object(updater,'verify',return_value={'version':version}))
            stack.enter_context(patch.object(updater,'unpack'))
            run=stack.enter_context(patch.object(updater.subprocess,'run',side_effect=exception,return_value=subprocess.CompletedProcess([],result)))
            self.u.install('2099.1.1.1',automatic=automatic)
            return run
    def test_launch_failure_is_latched_but_manual_retry_works(self):
        self.u.set_automatic(True)
        with self.assertRaisesRegex(ValueError,'launch'):self.launch(result=1)
        self.assertEqual(updater.Updates(self.base).state()['automatic_attempted_version'],'2099.1.1.1')
        with self.assertRaisesRegex(ValueError,'already had'):self.launch()
        self.launch(automatic=False)
        self.assertTrue(self.u.state()['installing'])
    def test_launch_timeout_holds_installation(self):
        self.u.set_automatic(True)
        with self.assertRaisesRegex(ValueError,'confirmed'):self.launch(exception=subprocess.TimeoutExpired('launchctl',15))
        self.assertTrue(self.u.state()['installing'])
        self.assertFalse(self.u.state()['available'])
    def test_install_rechecks_preference_and_exact_signed_version(self):
        with self.assertRaisesRegex(ValueError,'permitted'):self.launch()
        self.u.set_automatic(True)
        with self.assertRaisesRegex(ValueError,'changed'):self.launch(version='2099.1.1.2')
        self.assertFalse(self.u.attempt_path.exists())
    def test_monitor_checks_on_startup_even_in_manual_mode(self):
        updater.save(self.u.status_path,{})
        with patch.object(self.u,'check') as check:self.u.tick();check.assert_called_once()
