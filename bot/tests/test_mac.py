import json
import plistlib
from pathlib import Path
import stat
import tempfile
import unittest
from mac_setup import manifest, write_private

class MacSetupTests(unittest.TestCase):
    def test_login_manifest_handles_spaces_and_omits_secrets(self):
        base=Path('/Users/Test User/Library/Application Support/DeclanTradingLab')
        data=plistlib.loads(plistlib.dumps(manifest(base,'/Library/Python Test/python3')))
        self.assertEqual(data['ProgramArguments'],['/Library/Python Test/python3',str(base/'mac_runner.py')])
        self.assertTrue(data['RunAtLoad'])
        self.assertTrue(data['KeepAlive'])
        self.assertNotIn('EnvironmentVariables',data)

    def test_config_permissions_and_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.json'
            write_private(path,json.dumps({'key':'one'}))
            write_private(path,json.dumps({'key':'two'}))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(json.loads(path.read_text()),{'key':'two'})
            self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_mac_wrapper_forces_local_binding(self):
        source=(Path(__file__).resolve().parents[1]/'mac_runner.py').read_text()
        self.assertIn("BIND_HOST='127.0.0.1'",source)
        self.assertIn("PORT='8765'",source)

class ShutdownTests(unittest.TestCase):
    def test_waits_until_worker_releases_lock(self):
        import fcntl
        from unittest.mock import patch, Mock
        import mac_setup
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base/'data').mkdir()
            with (base/'data'/'worker.lock').open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(mac_setup, 'BASE', base), patch.object(mac_setup.subprocess, 'run', return_value=Mock(returncode=1)), patch.object(mac_setup.time, 'sleep', side_effect=lambda _: fcntl.flock(held, fcntl.LOCK_UN)) as sleep:
                    mac_setup.stop()
                    sleep.assert_called_once()

    def test_refuses_if_worker_stays_running(self):
        import fcntl
        from unittest.mock import patch, Mock
        import mac_setup
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base/'data').mkdir()
            with (base/'data'/'worker.lock').open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(mac_setup, 'BASE', base), patch.object(mac_setup.subprocess, 'run', return_value=Mock(returncode=1)):
                    with self.assertRaisesRegex(RuntimeError, 'No update files were replaced'):
                        mac_setup.stop(timeout=0)

    def test_waits_for_launch_service_to_unregister(self):
        from unittest.mock import patch, Mock
        import mac_setup
        with tempfile.TemporaryDirectory() as root:
            with patch.object(mac_setup, 'BASE', Path(root)), patch.object(mac_setup.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=0), Mock(returncode=1)]), patch.object(mac_setup.time, 'sleep') as sleep:
                mac_setup.stop()
                sleep.assert_called_once()

    def test_already_stopped_is_safe(self):
        from unittest.mock import patch, Mock
        import mac_setup
        with tempfile.TemporaryDirectory() as root:
            with patch.object(mac_setup, 'BASE', Path(root)), patch.object(mac_setup.subprocess, 'run', return_value=Mock(returncode=1)), patch.object(mac_setup.time, 'sleep') as sleep:
                mac_setup.stop()
                sleep.assert_not_called()
