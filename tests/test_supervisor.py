"""Exercise supervision without opening or writing to a game process."""
import copy
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from aoe_autopilot.backend import ControlError
from aoe_autopilot.supervisor import JsonRecorder, MatchSupervisor, main


class Recorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(copy.deepcopy(record))


class FakeController:
    def __init__(self):
        self.connect_calls = self.on_calls = self.tick_calls = self.close_calls = 0
        self.status_error = self.tick_error = self.on_error = self.close_error = None
        self.bridge = Mock()
        self.bridge.kernel.GetTickCount.return_value = 1000
        self.bridge.header.return_value = 900
        self.state = {
            'pid': 1, 'creation_time': 2, 'world': 3, 'player': 4,
            'scenario': 5, 'player_id': 1, 'time_ms': 1000,
            'type': 1, 'ai': 0, 'computer_ai_flag': 0, 'player_state': 0, 'resources': [100, 100, 100, 100],
            'native': {'session_valid': True, 'enabled': False, 'ai': 0,
                       'result': 0, 'pending': False, 'cleanup_pending': False},
        }

    def connect(self):
        self.connect_calls += 1

    def status(self):
        if self.status_error:
            raise self.status_error
        return copy.deepcopy(self.state)

    def on(self):
        self.on_calls += 1
        if self.on_error:
            raise self.on_error
        self.state.update(type=3, ai=100, computer_ai_flag=1)
        self.state['native'].update(enabled=True, ai=100, result=0, cleanup_pending=False)

    def tick(self):
        self.tick_calls += 1
        if self.tick_error:
            raise self.tick_error

    def close(self):
        self.close_calls += 1
        self.state.update(type=1, ai=0, computer_ai_flag=0)
        if self.close_error:
            raise self.close_error

    def expire(self, *, cleanup_pending=False):
        self.state.update(type=1, ai=0)
        self.state['native'].update(enabled=False, ai=0, result=7,
                                    cleanup_pending=cleanup_pending)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.recorder = Recorder()
        self.clock = Mock(return_value=10.0)
        self.supervisor = MatchSupervisor(self.controller, self.recorder, clock=self.clock)
        self.assertTrue(self.supervisor.start())

    def events(self):
        return [record['event'] for record in self.recorder.records]

    def test_watchdog_expiry_reopens_same_active_match_and_records_it(self):
        self.controller.expire()
        self.assertTrue(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 2)
        self.assertEqual(self.supervisor.recoveries, 1)
        self.assertTrue(self.supervisor.desired_takeover)
        self.assertEqual(self.events(), ['started', 'recovery_attempt', 'recovered'])
        self.assertEqual(self.recorder.records[-1]['state']['native']['heartbeat_age_ms'], 100)

    def test_waits_for_simulation_cleanup_without_repeated_enable(self):
        self.controller.expire(cleanup_pending=True)
        self.assertTrue(self.supervisor.step())
        self.assertTrue(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 1)
        self.assertEqual(self.events().count('awaiting_cleanup'), 1)
        self.controller.state['native']['cleanup_pending'] = False
        self.assertTrue(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 2)

    def test_three_recoveries_are_a_total_limit(self):
        for _ in range(3):
            self.controller.expire()
            self.assertTrue(self.supervisor.step())
        self.controller.expire()
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 4)
        self.assertEqual(self.supervisor.reason, 'recovery_limit')
        self.assertEqual(self.controller.close_calls, 1)
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.controller.close_calls, 1)

    def test_requested_stop_clears_intent_before_expiry_recovery(self):
        self.controller.expire()
        self.supervisor.request_stop('user_stop')
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 1)
        self.assertEqual(self.controller.tick_calls, 0)
        self.assertEqual(self.supervisor.reason, 'user_stop')
        self.assertFalse(self.recorder.records[-1]['desired_takeover'])

    def test_manual_off_is_not_automatically_reopened(self):
        self.controller.expire()
        self.controller.state['native']['result'] = 0
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'manual_control')
        self.assertEqual(self.controller.on_calls, 1)

    def test_foreign_ai_is_not_replaced(self):
        self.controller.state['ai'] = 999
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'unsafe_ai_state')
        self.assertEqual(self.controller.on_calls, 1)

    def test_missing_computer_flag_stops_instead_of_claiming_healthy_ai(self):
        self.controller.state['computer_ai_flag'] = 0
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'unsafe_ai_state')
        self.assertEqual(self.controller.on_calls, 1)
        self.assertEqual(self.controller.close_calls, 1)

    def test_changed_match_cannot_be_reenabled_even_with_watchdog_result(self):
        self.controller.expire()
        self.controller.state['scenario'] = 999
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'match_changed')
        self.assertEqual(self.controller.on_calls, 1)
        self.assertEqual(self.controller.tick_calls, 0)

    def test_unsafe_native_states_never_trigger_recovery(self):
        for change in ({'session_valid': False}, {'pending': True},
                       {'cleanup_pending': None}, {'result': 5}, {'result': 2}):
            with self.subTest(change=change):
                controller = FakeController()
                supervisor = MatchSupervisor(controller, Recorder())
                supervisor.start()
                controller.expire()
                controller.state['native'].update(change)
                self.assertFalse(supervisor.step())
                self.assertEqual(controller.on_calls, 1)
                self.assertEqual(controller.close_calls, 1)

    def test_load_rewind_exception_stops_without_reconnect(self):
        self.controller.status_error = ControlError('对局时间倒退，可能已读档；请重新连接')
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'controller_error')
        self.assertEqual(self.controller.connect_calls, 1)
        self.assertEqual(self.controller.on_calls, 1)
        self.assertIn('时间倒退', self.recorder.records[-1]['error'])

    def test_terminal_state_is_recorded_raw_before_close(self):
        self.controller.state['player_state'] = 2
        self.controller.state['resources'] = [17, 23, 31, 47]
        self.assertFalse(self.supervisor.step())
        final = self.recorder.records[-1]
        self.assertEqual(self.supervisor.reason, 'player_ended')
        self.assertEqual(final['state']['player_state'], 2)
        self.assertEqual(final['state']['resources'], [17, 23, 31, 47])
        self.assertEqual(final['state_phase'], 'before_close')
        self.assertEqual(self.controller.tick_calls, 0)
        self.assertEqual(self.controller.close_calls, 1)

    def test_recovery_failure_stops_instead_of_repeating_enable(self):
        self.controller.expire()
        self.controller.on_error = ControlError('桥接异常')
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.recoveries, 1)
        self.assertEqual(self.supervisor.reason, 'controller_error')
        self.assertEqual(self.controller.on_calls, 2)
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.controller.on_calls, 2)

    def test_terminal_transition_between_status_and_tick_is_still_recorded_as_end(self):
        def end_during_tick():
            self.controller.state['player_state'] = 1
            raise ControlError('玩家已结束比赛，不能开启接管')
        self.controller.tick = end_during_tick
        self.assertFalse(self.supervisor.step())
        self.assertEqual(self.supervisor.reason, 'player_ended')
        self.assertEqual(self.recorder.records[-1]['state']['player_state'], 1)
        self.assertEqual(self.controller.on_calls, 1)
        self.assertEqual(self.controller.close_calls, 1)

    def test_close_failure_is_recorded_without_claiming_success(self):
        self.controller.close_error = ControlError('恢复人工失败')
        self.supervisor.request_stop()
        self.supervisor.step()
        final = self.recorder.records[-1]
        self.assertFalse(final['close_completed'])
        self.assertEqual(final['close_error'], '恢复人工失败')

    def test_fifteen_second_samples_include_native_health(self):
        self.clock.return_value = 24.9
        self.supervisor.step()
        self.assertEqual(self.events(), ['started'])
        self.clock.return_value = 25.0
        self.supervisor.step()
        self.assertEqual(self.events(), ['started', 'sample'])
        self.assertEqual(self.recorder.records[-1]['state']['native']['heartbeat_age_ms'], 100)

    def test_current_session_stop_file_clears_intent_before_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / 'stop.json'
            self.supervisor.stop_file = request
            self.controller.expire()
            request.write_text(json.dumps({'session_id': self.supervisor.session_id}), encoding='utf-8')
            self.assertFalse(self.supervisor.step())
            self.assertEqual(self.supervisor.reason, 'stop_file')
            self.assertEqual(self.controller.on_calls, 1)
            self.assertEqual(self.controller.tick_calls, 0)
            self.assertEqual(self.controller.close_calls, 1)
            final = self.recorder.records[-1]
            self.assertEqual(final['supervisor_pid'], os.getpid())
            self.assertFalse(final['desired_takeover'])
            self.assertEqual(final['stop_file'], str(request.resolve()))

    def test_old_other_session_and_invalid_stop_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / 'stop.json'
            self.supervisor.stop_file = request
            self.assertTrue(self.supervisor.step())  # Missing request.
            for payload in ('{', 'null', '[]', '{}', '{"session_id":"old-session"}',
                            '{"session_id":17}'):
                with self.subTest(payload=payload):
                    request.write_text(payload, encoding='utf-8')
                    self.assertTrue(self.supervisor.step())
                    self.assertFalse(self.supervisor.stopped)
            self.assertEqual(self.controller.close_calls, 0)


class EntryTests(unittest.TestCase):
    def test_already_ended_match_is_not_enabled(self):
        controller = FakeController()
        controller.state['player_state'] = 1
        supervisor = MatchSupervisor(controller, Recorder())
        self.assertFalse(supervisor.start())
        self.assertEqual(controller.on_calls, 0)
        self.assertEqual(supervisor.reason, 'player_ended')
        self.assertEqual(controller.close_calls, 1)

    def test_queued_hotkey_stop_ends_run_before_any_tick(self):
        controller = FakeController()
        events = queue.Queue()
        events.put('off')
        supervisor = MatchSupervisor(controller, Recorder())
        supervisor.run(events=events)
        self.assertEqual(supervisor.reason, 'user_stop')
        self.assertEqual(controller.tick_calls, 0)
        self.assertEqual(controller.close_calls, 1)

    def test_keyboard_interrupt_restores_manual_control(self):
        controller = FakeController()
        controller.tick_error = KeyboardInterrupt()
        supervisor = MatchSupervisor(controller, Recorder())
        supervisor.run()
        self.assertEqual(supervisor.reason, 'keyboard_interrupt')
        self.assertEqual(controller.close_calls, 1)

    def test_duplicate_console_mutex_prevents_any_controller_creation(self):
        with (patch('aoe_autopilot.supervisor.ConsoleLock', side_effect=ControlError('已经运行')),
              patch('aoe_autopilot.supervisor.Controller') as controller,
              patch('builtins.print')):
            self.assertEqual(main(['--no-hotkeys']), 1)
            controller.assert_not_called()

    def test_atomic_status_and_jsonl_history_preserve_latest_and_events(self):
        with tempfile.TemporaryDirectory() as folder:
            status = Path(folder) / 'status.json'
            events = Path(folder) / 'events.jsonl'
            recorder = JsonRecorder(status, events)
            recorder.record({'event': 'started', '汉字': '接管'})
            recorder.record({'event': 'stopped'})
            self.assertEqual(json.loads(status.read_text(encoding='utf-8'))['event'], 'stopped')
            rows = [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['event'] for row in rows], ['started', 'stopped'])
            self.assertEqual(rows[0]['汉字'], '接管')
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
