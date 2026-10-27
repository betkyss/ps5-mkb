import hashlib
import tempfile
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from dashboard.adapters import Adapters, Native, ROOT
from dashboard.schema import Problem


class Focus(unittest.TestCase):
    def setUp(self):
        self.native = Native('/unused/native.sock')
        self.expected = dict(pid=123, processId='process', sessionId='session', digest='digest')
        self.binary = str(ROOT / 'build/client/gui/chiaki.app/Contents/MacOS/chiaki')

    def test_focus_precedes_native_arm_and_shares_deadline(self):
        order = []
        def run(argv, timeout):
            order.append(argv[0])
            self.assertLessEqual(timeout, 2)
            return self.binary if argv[0] == 'ps' else ''
        def request(action, expected, **kwargs):
            order.append(action)
            self.assertIs(expected, self.expected)
            self.assertGreater(kwargs['deadline'], time.monotonic())
            self.assertLessEqual(kwargs['deadline'] - time.monotonic(), 7)
            return {'capture': 'active'}
        with patch('dashboard.adapters.run', side_effect=run) as calls, patch.object(self.native, 'request', side_effect=request):
            self.assertEqual(self.native.control('arm', self.expected)['capture'], 'active')
            self.assertEqual(order, ['ps', 'osascript', 'arm'])
            self.assertIn('runningApplicationWithProcessIdentifier(123)', calls.call_args_list[1].args[0][-1])

    def test_wrong_pid_never_activates_or_arms(self):
        with patch('dashboard.adapters.run', return_value='/another/app') as run, patch.object(self.native, 'request') as request:
            with self.assertRaises(Problem):self.native.control('arm', self.expected)
            self.assertEqual(run.call_count, 1)
            request.assert_not_called()
        with patch('dashboard.adapters.run') as run:
            with self.assertRaises(Problem):self.native.control('arm', dict(self.expected, pid=True))
            run.assert_not_called()

    def test_cancel_during_focus_does_not_arm(self):
        cancelled = threading.Event()
        def run(argv, timeout):
            if argv[0] == 'osascript':cancelled.set()
            return self.binary
        with patch('dashboard.adapters.run', side_effect=run), patch.object(self.native, 'request') as request:
            with self.assertRaises(Problem):self.native.control('arm', self.expected, cancelled=cancelled)
            request.assert_not_called()

    def test_cancel_during_pid_check_does_not_focus(self):
        cancelled = threading.Event()
        def run(argv, timeout):
            self.assertEqual(argv[0], 'ps')
            cancelled.set()
            return self.binary
        with patch('dashboard.adapters.run', side_effect=run) as calls, patch.object(self.native, 'request') as request:
            with self.assertRaises(Problem):self.native.control('arm', self.expected, cancelled=cancelled)
            self.assertEqual(calls.call_count, 1)
            request.assert_not_called()

    def test_exhausted_deadline_does_not_focus(self):
        with patch('dashboard.adapters.time.monotonic', side_effect=[0, 0, 8]), patch('dashboard.adapters.run', return_value=self.binary) as run, patch.object(self.native, 'request') as request:
            with self.assertRaisesRegex(Problem, 'Истекло'):
                self.native.control('arm', self.expected)
            self.assertEqual(run.call_count, 1)
            request.assert_not_called()

    def test_focus_denied_does_not_arm(self):
        with patch('dashboard.adapters.run', side_effect=[self.binary, Problem('denied')]), patch.object(self.native, 'request') as request:
            with self.assertRaisesRegex(Problem, 'активировать'):self.native.control('arm', self.expected)
            request.assert_not_called()

    def test_release_never_focuses_even_when_cancelled(self):
        cancelled = threading.Event();cancelled.set()
        with patch('dashboard.adapters.run') as run, patch.object(self.native, 'request', return_value={'capture': 'off'}) as request:
            self.native.control('release', self.expected, cancelled=cancelled)
            run.assert_not_called();self.assertEqual(request.call_args.args[0], 'release')


class FailedLaunchRecovery(unittest.TestCase):
    def test_absent_client_recovers_but_live_or_failed_probe_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Adapters(directory)
            binary = ROOT / 'build/client/gui/chiaki.app/Contents/MacOS/chiaki'
            data = b'profile'
            digest = hashlib.sha256(data).hexdigest()
            profile = Path(directory) / ('launch-' + digest + '.json')
            profile.write_bytes(data)
            plan = dict(binary=str(binary), binaryDigest=hashlib.sha256(data).hexdigest(),
                        profilePath=str(profile), profileDigest=digest)
            original = Path.read_bytes
            def read(path):return data if path == binary else original(path)
            for code in [0, 1, 2]:
                with self.subTest(code=code), patch.object(Path, 'read_bytes', read), patch('dashboard.adapters.subprocess.run', return_value=subprocess.CompletedProcess([], code)), patch.object(adapter.native, 'request') as request:
                    if code == 1:
                        result = adapter.recover_launch(plan)
                        self.assertFalse(result['processAlive'])
                        self.assertEqual(result['releaseProof'], 'no-client')
                    else:
                        with self.assertRaises(Problem):adapter.recover_launch(plan)
                    request.assert_not_called()


if __name__ == '__main__':unittest.main()
