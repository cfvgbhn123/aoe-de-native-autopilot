"""Run one verified match with bounded recovery from watchdog expiry."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from .backend import BridgeError, Controller, ControlError, ROOT
from .console import ConsoleLock
from game_common.win32 import Hotkeys

IDENTITY_FIELDS = ('pid', 'creation_time', 'world', 'player', 'scenario', 'player_id')
BEIJING = timezone(timedelta(hours=8))


class JsonRecorder:
    """Keep an atomic latest state and an append-only sequence of events."""

    def __init__(self, status_path: Path, event_path: Path):
        self.status_path = Path(status_path)
        self.event_path = Path(event_path)
        for path in (self.status_path, self.event_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, record):
        line = json.dumps(record, ensure_ascii=False)
        temporary = self.status_path.with_name(self.status_path.name + f'.{os.getpid()}.tmp')
        temporary.write_text(line + '\n', encoding='utf-8')
        temporary.replace(self.status_path)
        with self.event_path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')


class MatchSupervisor:
    """Only recover our expired AI in the original, still-active match."""

    def __init__(self, controller, recorder, *, sample_interval=15.0,
                 max_recoveries=3, stop_file=None, clock=time.monotonic):
        if sample_interval <= 0:
            raise ValueError('采样间隔必须大于零')
        if not 0 <= max_recoveries <= 3:
            raise ValueError('自动恢复次数必须在 0 到 3 之间')
        self.controller = controller
        self.recorder = recorder
        self.sample_interval = sample_interval
        self.max_recoveries = max_recoveries
        self.clock = clock
        self.stop_file = Path(stop_file) if stop_file is not None else None
        self.session_id = uuid.uuid4().hex
        self.identity = None
        self.desired_takeover = False
        self.recoveries = 0
        self.stopped = False
        self.reason = None
        self.requested_stop = None
        self.last_sample = None
        self.last_state = None
        self.close_error = None
        self.waiting_for_cleanup = False

    def _read_state(self):
        state = self.controller.status()
        native = state.get('native')
        bridge = self.controller.bridge
        if native is not None and bridge is not None:
            native['heartbeat_age_ms'] = (
                bridge.kernel.GetTickCount() - bridge.header(9)
            ) & 0xffffffff
        self.last_state = state
        return state

    def _record(self, event, state=None, **extra):
        self.recorder.record({
            'recorded_at': datetime.now(BEIJING).isoformat(timespec='milliseconds'),
            'session_id': self.session_id,
            'supervisor_pid': os.getpid(),
            'stop_file': str(self.stop_file.resolve()) if self.stop_file is not None else None,
            'event': event,
            'desired_takeover': self.desired_takeover,
            'recoveries': self.recoveries,
            'max_recoveries': self.max_recoveries,
            'stopped': self.stopped,
            'reason': self.reason,
            'match_identity': self.identity,
            'state': state if state is not None else self.last_state,
            **extra,
        })

    def start(self):
        try:
            self.controller.connect()
            initial = self._read_state()
            if initial['player_state'] != 0:
                self._finish('player_ended', initial)
                return False
            self.identity = {key: initial[key] for key in IDENTITY_FIELDS}
            self.controller.on()
            state = self._read_state()
            if not self._same_match(state) or not self._active(state):
                raise ControlError('监督启动后的接管状态未通过校验')
            self.desired_takeover = True
            self.last_sample = self.clock()
            self._record('started', state)
            return True
        except (ControlError, BridgeError, OSError, ValueError) as exc:
            self._finish('startup_error', error=str(exc))
            return False

    def _same_match(self, state):
        return self.identity is not None and all(
            state.get(key) == value for key, value in self.identity.items()
        )

    @staticmethod
    def _active(state):
        native = state.get('native', {})
        return (state.get('type') == 3 and state.get('computer_ai_flag') == 1 and bool(state.get('ai'))
                and native.get('enabled') is True
                and native.get('session_valid') is True
                and state['ai'] == native.get('ai')
                and native.get('result') == 0)

    def request_stop(self, reason='requested_stop'):
        # Clear intent before any reads, so a queued stop never triggers recovery.
        self.desired_takeover = False
        self.requested_stop = reason

    def _poll_stop_file(self):
        if self.stop_file is None:
            return
        try:
            request = json.loads(self.stop_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            # An old, incomplete, or unreadable request cannot authorize a stop.
            return
        if isinstance(request, dict) and request.get('session_id') == self.session_id:
            self.request_stop('stop_file')

    def _finish(self, reason, state=None, **extra):
        if self.stopped:
            return
        self.desired_takeover = False
        self.stopped = True
        self.reason = reason
        if state is None:
            try:
                state = self._read_state()
            except (ControlError, BridgeError, OSError, ValueError) as exc:
                extra['final_snapshot_error'] = str(exc)
        try:
            self.controller.close()
        except (ControlError, BridgeError, OSError, ValueError) as exc:
            self.close_error = str(exc)
        self._record('stopped', state, state_phase='before_close',
                     close_completed=self.close_error is None,
                     close_error=self.close_error, **extra)

    def step(self):
        if self.stopped:
            return False
        self._poll_stop_file()
        if self.requested_stop or not self.desired_takeover:
            self._finish(self.requested_stop or 'takeover_intent_cleared')
            return False
        try:
            state = self._read_state()
            if not self._same_match(state):
                self._finish('match_changed', state)
                return False
            if state['player_state'] != 0:
                self._finish('player_ended', state)
                return False
            native = state.get('native', {})
            if native.get('session_valid') is not True:
                self._finish('session_invalid', state)
                return False
            self.controller.tick()
            if not self._active(state):
                manual = state.get('type') == 1 and state.get('ai') == 0
                expired = (manual and native.get('enabled') is False
                           and native.get('result') == 7
                           and native.get('pending') is False)
                if not expired:
                    reason = 'manual_control' if manual and native.get('result') == 0 else 'unsafe_ai_state'
                    self._finish(reason, state)
                    return False
                if native.get('cleanup_pending') is True:
                    if not self.waiting_for_cleanup:
                        self._record('awaiting_cleanup', state)
                        self.waiting_for_cleanup = True
                elif native.get('cleanup_pending') is False:
                    if self.recoveries >= self.max_recoveries:
                        self._finish('recovery_limit', state)
                        return False
                    self.recoveries += 1
                    self.waiting_for_cleanup = False
                    self._record('recovery_attempt', state)
                    self.controller.on()
                    state = self._read_state()
                    if (not self._same_match(state) or state['player_state'] != 0
                            or not self._active(state)):
                        raise ControlError('自动恢复后的接管状态未通过校验')
                    self._record('recovered', state)
                else:
                    self._finish('unsafe_ai_state', state)
                    return False
            now = self.clock()
            if now - self.last_sample >= self.sample_interval:
                self._record('sample', state)
                self.last_sample = now
            return True
        except (ControlError, BridgeError, OSError, ValueError) as exc:
            # Never reconnect or enable after identity, process, or bridge errors.
            # The game may end between status() and tick()'s stricter snapshot.
            try:
                final = self._read_state()
            except (ControlError, BridgeError, OSError, ValueError):
                final = None
            if final is not None and self._same_match(final) and final['player_state'] != 0:
                self._finish('player_ended', final, preceding_error=str(exc))
                return False
            self._finish('controller_error', error=str(exc))
            return False

    def run(self, *, tick_interval=0.2, events=None):
        if tick_interval <= 0:
            raise ValueError('更新间隔必须大于零')
        try:
            if not self.start():
                return
            while not self.stopped:
                if events is not None:
                    while True:
                        try:
                            action = events.get_nowait()
                        except queue.Empty:
                            break
                        if action in ('off', 'quit', 'toggle', 'emergency'):
                            self.request_stop('user_stop')
                        elif action == 'status':
                            self._record('requested_status', self._read_state())
                if not self.step():
                    break
                threading.Event().wait(tick_interval)
        except KeyboardInterrupt:
            self.request_stop('keyboard_interrupt')
        except (ControlError, BridgeError, OSError, ValueError) as exc:
            self._finish('controller_error', error=str(exc))
        finally:
            if not self.stopped:
                self._finish(self.requested_stop or 'supervisor_exit')


def main(argv=None):
    parser = argparse.ArgumentParser(description='持续监督本局帝国时代 AI；心跳过期最多自动恢复三次')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT / 'aoe_ai_config.json')
    parser.add_argument('--sample-interval', type=float, default=15.0)
    parser.add_argument('--tick-interval', type=float, default=0.2)
    parser.add_argument('--max-recoveries', type=int, choices=range(4), default=3)
    parser.add_argument('--status-file', type=Path, default=ROOT / 'aoe_ai_logs' / 'supervisor_status.json')
    parser.add_argument('--event-file', type=Path, default=ROOT / 'aoe_ai_logs' / 'supervisor_events.jsonl')
    parser.add_argument('--stop-file', type=Path, default=ROOT / 'aoe_ai_logs' / 'supervisor_stop.json')
    parser.add_argument('--no-hotkeys', action='store_true')
    args = parser.parse_args(argv)
    lock = None
    hotkeys = None
    try:
        if args.tick_interval <= 0 or args.sample_interval <= 0:
            raise ControlError('更新和采样间隔必须大于零')
        if len({path.resolve() for path in (args.status_file, args.event_file, args.stop_file)}) != 3:
            raise ControlError('状态文件、事件文件和停止请求必须使用不同路径')
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if config.get('difficulty', 'hardest') != 'hardest':
            raise ControlError('当前接管版本固定使用原版极难 AI')
        # Share the console mutex: there may be only one writing controller.
        lock = ConsoleLock()
        recorder = JsonRecorder(args.status_file, args.event_file)
        controller = Controller(args.game_dir or config.get('game_dir'), args.pid)
        supervisor = MatchSupervisor(controller, recorder,
                                     sample_interval=args.sample_interval,
                                     max_recoveries=args.max_recoveries,
                                     stop_file=args.stop_file)
        events = queue.Queue()
        if not args.no_hotkeys:
            hotkeys = Hotkeys(events, config.get('hotkeys'))
            conflicts = hotkeys.start()
            if conflicts:
                print('快捷键冲突：' + str(conflicts) + '；Ctrl+C 可恢复人工并退出。', flush=True)
        print(f'持续监督本局；状态：{args.status_file.resolve()}；事件：{args.event_file.resolve()}', flush=True)
        print('Ctrl+Alt+F10 / Ctrl+Alt+Pause / Ctrl+Alt+F8 或 Ctrl+C：恢复人工并停止。', flush=True)
        supervisor.run(tick_interval=args.tick_interval, events=events)
        print('监督停止：' + str(supervisor.reason), flush=True)
        if supervisor.close_error:
            print('恢复人工未通过确认：' + supervisor.close_error, flush=True)
        return 0 if supervisor.reason in ('player_ended', 'manual_control', 'user_stop', 'keyboard_interrupt', 'stop_file') and not supervisor.close_error else 1
    except (ControlError, BridgeError, OSError, ValueError) as exc:
        print('监督未完成：' + str(exc), flush=True)
        return 1
    finally:
        if hotkeys:
            hotkeys.stop()
        if lock:
            lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
