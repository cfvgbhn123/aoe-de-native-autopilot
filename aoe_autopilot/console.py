from __future__ import annotations
import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading
import time
from .backend import Controller, ControlError, BridgeError, ROOT, SHA256
from game_common.win32 import Hotkeys

MENU='''帝国时代 I：决定版 · 原版极难 AI 接管（单机随机地图/死斗）
1 / connect 连接游戏    2 / on 开启接管    3 / off 恢复人工
4 / toggle 切换接管    5 / status 查看状态    7 / help 菜单
0 / quit 恢复人工并退出
Ctrl+Alt+F8 切换接管 | Ctrl+Alt+F9 状态 | Ctrl+Alt+F10 恢复人工
游戏暂停时开启接管需要等待游戏继续；恢复人工可立即执行并取消排队开启。
存档、读档、换局前请先恢复人工；再次开启会重新初始化原版 AI。'''

def print_status(state):
    native=state.get('native',{})
    enabled=state.get('type')==3 and state.get('ai') and not state.get('ai_stopped')
    print(f"PID {state['pid']} | {state.get('mode_name','单机')} | 玩家 {state['player_id']} | {'原版极难 AI 接管' if enabled else '人工控制'}")
    values=state.get('resources',[])
    if len(values)==4:print('食物 %.0f | 木材 %.0f | 石料 %.0f | 黄金 %.0f'%tuple(values))
    if native:
        print(f"村民 {native['villagers']} | 建筑 {native['buildings']} | 对象 {native['units']} | 玩家更新 {native['updates']}")
        print('原版建设脚本：'+native['build_list']+' | 战略：'+native['personality'])
        if native['pending']:print('启停命令正在等待游戏推进。')
        if native['result']:print('提示：'+native['message'])

class ConsoleLock:
    NAME='Local\\AoEDENativeConsole_v4'
    REPLACE_EVENT=NAME+'_replace'

    def __init__(self,replace=False,accept_replacement=False,timeout=15):
        self.handle=None;self.replace_event=None
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype=ctypes.c_void_p
        self.kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        self.kernel.CreateEventW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_wchar_p]
        self.kernel.CreateEventW.restype=ctypes.c_void_p
        self.kernel.OpenEventW.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_wchar_p]
        self.kernel.OpenEventW.restype=ctypes.c_void_p
        self.kernel.SetEvent.argtypes=[ctypes.c_void_p]
        self.kernel.ResetEvent.argtypes=[ctypes.c_void_p]
        self.kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        self.kernel.WaitForSingleObject.restype=ctypes.c_uint32
        deadline=time.monotonic()+timeout
        while True:
            ctypes.set_last_error(0)
            handle=self.kernel.CreateMutexW(None,False,self.NAME)
            if not handle:raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error()!=183:
                self.handle=handle
                break
            self.kernel.CloseHandle(handle)
            if not replace:
                raise ControlError('帝国时代 AI 控制台已经运行，请使用已有窗口。')
            event=self.kernel.OpenEventW(0x0002,False,self.REPLACE_EVENT)
            if event:
                try:
                    if not self.kernel.SetEvent(event):raise ctypes.WinError(ctypes.get_last_error())
                finally:self.kernel.CloseHandle(event)
            if time.monotonic()>=deadline:
                raise ControlError('旧控制台未完成退出；若是更新前的窗口或监督进程，请先手动退出后重试。')
            time.sleep(.1)
        if accept_replacement:
            self.replace_event=self.kernel.CreateEventW(None,True,False,self.REPLACE_EVENT)
            if not self.replace_event:
                error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)
            if not self.kernel.ResetEvent(self.replace_event):
                error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)

    def replacement_requested(self):
        return bool(self.replace_event and self.kernel.WaitForSingleObject(self.replace_event,0)==0)

    def close(self):
        if self.replace_event:self.kernel.CloseHandle(self.replace_event);self.replace_event=None
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

def disable_quick_edit():
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetStdHandle.argtypes=[ctypes.c_uint32];k.GetStdHandle.restype=ctypes.c_void_p
    k.GetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
    k.SetConsoleMode.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    handle=k.GetStdHandle(0xfffffff6);mode=ctypes.c_uint32()
    if k.GetConsoleMode(handle,ctypes.byref(mode)):k.SetConsoleMode(handle,(mode.value|0x80)&~0x40)

def main(argv=None):
    parser=argparse.ArgumentParser(description='AoE I DE 97381 原版极难 AI 接管')
    parser.add_argument('--pid',type=int)
    parser.add_argument('--status',action='store_true',help='只读状态，不加载桥接')
    parser.add_argument('--diagnose',action='store_true',help='只读版本与对局报告')
    parser.add_argument('--no-hotkeys',action='store_true')
    parser.add_argument('--replace',action='store_true',help='通知旧控制台退出，由当前窗口接替')
    parser.add_argument('--config',type=Path,default=ROOT/'aoe_ai_config.json')
    args=parser.parse_args(argv)
    config=json.loads(args.config.read_text(encoding='utf-8'))
    if config.get('difficulty','hardest')!='hardest':raise ControlError('当前接管版本固定使用原版极难 AI')
    c=Controller(config.get('game_dir'),args.pid)
    if args.status or args.diagnose:
        try:
            c.connect();state=c.status()
            if args.diagnose:state.update(executable=str(c.game_dir/'AoEDE_s.exe'),sha256=SHA256)
            print(json.dumps(state,ensure_ascii=False,indent=2));return 0
        except (ControlError,OSError) as exc:
            print(str(exc));return 1
        finally:c.close()
    lock=None;hotkeys=None
    try:
        lock=ConsoleLock(replace=args.replace,accept_replacement=True);disable_quick_edit()
        logs=ROOT/'aoe_ai_logs';logs.mkdir(exist_ok=True)
        handler=RotatingFileHandler(logs/'console.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log=logging.getLogger('aoe_autopilot');log.setLevel(logging.INFO);log.addHandler(handler)
        print(MENU,flush=True)
        try:c.connect();print_status(c.status())
        except (ControlError,OSError) as exc:print(str(exc))
        events=queue.Queue()
        if not args.no_hotkeys:
            hotkeys=Hotkeys(events,config.get('hotkeys'))
            conflicts=hotkeys.start()
            if conflicts:print('快捷键冲突：'+str(conflicts)+'；可使用菜单命令。')
        def read_input():
            while True:
                line=sys.stdin.readline()
                if not line:events.put('quit');return
                events.put(line.strip().lower())
        threading.Thread(target=read_input,daemon=True,name='aoe-input').start()
        aliases={'1':'connect','2':'on','3':'off','4':'toggle','5':'status','6':'diagnose','7':'help','0':'quit',
                 'emergency':'off','exit':'quit'}
        while True:
            if lock.replacement_requested():
                print('新窗口已启动，恢复人工控制并退出当前窗口。',flush=True)
                break
            try:c.tick()
            except (ControlError,OSError) as exc:
                print('连接状态改变：'+str(exc))
                try:c.close()
                except (ControlError,BridgeError,OSError):pass
            try:action=aliases.get(raw:=events.get(timeout=.2),raw)
            except queue.Empty:continue
            if not action:continue
            try:
                if action=='quit':break
                if action=='connect':c.connect();print_status(c.status())
                elif action in ('on','toggle'):
                    if c.memory is None:c.connect()
                    snap=c.snapshot()
                    if action=='toggle' and snap['type']==3 and not snap['ai_stopped']:c.off()
                    else:c.on()
                    print_status(c.status())
                elif action=='off':
                    if c.memory is None:c.connect()
                    c.off();print_status(c.status())
                elif action=='status':
                    if c.memory is None:c.connect()
                    print_status(c.status())
                elif action=='diagnose':print(json.dumps(c.status(),ensure_ascii=False,indent=2))
                elif action=='help':print(MENU)
                else:print('输入 7 查看菜单。')
                log.info('%s %s',action,c.status())
            except (ControlError,BridgeError,OSError,ValueError) as exc:
                print('操作未完成：'+str(exc));log.exception('%s failed',action)
            sys.stdout.flush()
    except (ControlError,BridgeError,OSError) as exc:
        print(str(exc));return 1
    except KeyboardInterrupt:pass
    finally:
        if hotkeys:hotkeys.stop()
        try:c.close()
        except (ControlError,BridgeError,OSError) as exc:print('恢复仍在等待游戏继续：'+str(exc))
        if lock:lock.close()
    return 0
