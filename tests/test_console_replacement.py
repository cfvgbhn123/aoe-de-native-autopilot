import subprocess
import sys
import unittest
import uuid

from aoe_autopilot.console import ConsoleLock, ControlError


@unittest.skipUnless(sys.platform == 'win32', 'Windows named events and mutexes')
class ConsoleReplacementTests(unittest.TestCase):
    def setUp(self):
        name = 'Local\\AoEReplacementTest_' + uuid.uuid4().hex
        self.lock_type = type('TestLock', (ConsoleLock,), {
            'NAME': name, 'REPLACE_EVENT': name + '_replace',
        })

    def test_duplicate_without_replace_is_rejected(self):
        owner = self.lock_type(accept_replacement=True)
        try:
            with self.assertRaises(ControlError):
                self.lock_type()
            self.assertFalse(owner.replacement_requested())
        finally:
            owner.close()

    def test_new_process_waits_for_old_process_to_release(self):
        code = '''
import sys, time
from aoe_autopilot.console import ConsoleLock
ConsoleLock.NAME, ConsoleLock.REPLACE_EVENT = sys.argv[1:]
lock = ConsoleLock(accept_replacement=True)
print('ready', flush=True)
try:
    deadline = time.monotonic() + 10
    while not lock.replacement_requested():
        if time.monotonic() > deadline: raise RuntimeError('no replacement request')
        time.sleep(.01)
    time.sleep(.2)  # Model controller and hotkey cleanup before releasing the lock.
    print('cleanup complete', flush=True)
finally:
    lock.close()
'''
        old = subprocess.Popen([sys.executable, '-u', '-c', code,
                                self.lock_type.NAME, self.lock_type.REPLACE_EVENT],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(old.stdout.readline().strip(), 'ready')
            new = self.lock_type(replace=True, accept_replacement=True, timeout=5)
            try:
                output, errors = old.communicate(timeout=5)
                self.assertEqual(old.returncode, 0, errors)
                self.assertIn('cleanup complete', output)
                self.assertFalse(new.replacement_requested())
                with self.assertRaises(ControlError):
                    self.lock_type()
            finally:
                new.close()
        finally:
            if old.poll() is None:
                old.kill()
                old.communicate()
            old.stdout.close()
            old.stderr.close()

    def test_legacy_owner_timeout_keeps_owner_lock(self):
        owner = self.lock_type()
        try:
            with self.assertRaisesRegex(ControlError, '旧控制台未完成退出'):
                self.lock_type(replace=True, timeout=.1)
            with self.assertRaises(ControlError):
                self.lock_type()
        finally:
            owner.close()
        new = self.lock_type()
        new.close()
