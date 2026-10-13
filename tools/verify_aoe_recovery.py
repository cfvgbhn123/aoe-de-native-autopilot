"""Live acceptance: toggles, pause cancellation, real controller death, soak.
This changes the current offline match. Each case verifies live player memory.
"""
import argparse,ctypes,json,os,subprocess,sys,time,traceback
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aoe_autopilot.backend import Controller,ControlError,ROOT,PROTOCOL

def manual(c):
    state=c.snapshot()
    assert state['type']==1 and state['ai']==0 and state['player_state']==0 and state['computer_ai_flag']==0,state
    return state

def window(pid):
    u=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p)
    u.EnumWindows.argtypes=[callback,ctypes.c_void_p]
    u.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
    u.IsWindowVisible.argtypes=[ctypes.c_void_p]
    u.PostMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
    found=[]
    def visit(hwnd,_):
        value=ctypes.c_uint32();u.GetWindowThreadProcessId(hwnd,ctypes.byref(value))
        if value.value==pid and u.IsWindowVisible(hwnd):found.append(hwnd)
        return 1
    u.EnumWindows(callback(visit),None)
    if len(found)!=1:raise RuntimeError('Cannot locate unique game window')
    return u,found[0]

def key(c,vk):
    u,hwnd=window(c.memory.pid)
    if not u.PostMessageW(hwnd,0x100,vk,1) or not u.PostMessageW(hwnd,0x101,vk,0xc0000001):
        raise ctypes.WinError(ctypes.get_last_error())

def paused(c):
    a=c.snapshot()['time_ms'];time.sleep(.4);b=c.snapshot()['time_ms']
    return a==b

def wait_cleanup(c):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if not c.bridge.native_status()['cleanup_pending']:return
        time.sleep(.05)
    raise AssertionError('Simulation did not dispose detached AI')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cycles',type=int,default=10)
    parser.add_argument('--soak',type=float,default=30);parser.add_argument('--pause-tests',action='store_true')
    args=parser.parse_args();c=Controller();rows=[];changed_pause=False
    report={'timestamp':datetime.now(timezone(timedelta(hours=8))).isoformat(),'passed':False,'cases':rows}
    def case(name,**data):rows.append({'case':name,**data});print(name+' passed',flush=True)
    try:
        c.connect();report['before']=c.snapshot()
        for n in range(args.cycles):
            on=c.on();time.sleep(.2);c.tick();c.off();after=manual(c);wait_cleanup(c)
            case('toggle_'+str(n+1),on=on,after=after,cleanup=c.bridge.native_status())
        c.on();c.bridge._heartbeat_pump.stop();c.bridge._heartbeat_pump=None
        time.sleep(5.7);state=manual(c);assert c.bridge.native_status()['result']==7
        case('watchdog_running',after=state);c.off();wait_cleanup(c)
        if args.pause_tests:
            if paused(c):raise RuntimeError('Start the pause suite with simulation running')
            c.on();key(c,114);time.sleep(.3);changed_pause=True
            assert paused(c),'F3 was not processed as pause'
            start=time.monotonic();c.off();elapsed=time.monotonic()-start
            assert elapsed<2;case('release_while_paused',elapsed=elapsed,after=manual(c))
            try:c.bridge.command(1,timeout=.3)
            except ControlError:pass
            assert c.bridge.native_status()['pending']
            c.off();assert not c.bridge.native_status()['pending']
            case('cancel_queued_enable_while_paused',after=manual(c))
            key(c,114);time.sleep(.5);changed_pause=False
            assert not paused(c);wait_cleanup(c);manual(c)
            case('resume_does_not_execute_cancelled_enable',after=manual(c))
            c.close();c.connect()
            child_code="from aoe_autopilot.backend import Controller;import os,time;c=Controller();c.connect();c.on();print('READY',flush=True);time.sleep(1);os._exit(0)"
            child=subprocess.Popen([sys.executable,'-X','utf8','-u','-c',child_code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                line=child.stdout.readline()
                if line.strip()!='READY':raise RuntimeError(line+child.stderr.read())
                key(c,114);time.sleep(.3);changed_pause=True;assert paused(c)
                child.wait(timeout=10);time.sleep(5.7)
                case('real_controller_exit_while_paused',after=manual(c))
            finally:
                if child.poll() is None:child.kill();child.wait()
            c.off();manual(c)
            key(c,114);time.sleep(.5);changed_pause=False
            c.on();c.off();wait_cleanup(c)
            case('new_controller_reconnect_after_watchdog',after=c.status())
        time.sleep(3);c.on();start=c.snapshot();deadline=time.monotonic()+args.soak
        samples=[]
        while time.monotonic()<deadline:
            time.sleep(1);c.tick();samples.append(c.status())
        c.off();after=manual(c);wait_cleanup(c)
        assert samples and samples[-1]['time_ms']>start['time_ms'],'Simulation never advanced'
        case('soak',seconds=args.soak,samples=samples,after=after)
        report['passed']=True
    except BaseException as exc:
        report['error']=repr(exc);report['traceback']=traceback.format_exc();raise
    finally:
        try:
            if changed_pause:key(c,114);time.sleep(.4)
            c.close()
        finally:
            out=ROOT/'aoe_ai_logs'/f'v{PROTOCOL}_recovery_verification.json';out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Report: '+str(out),flush=True)

if __name__=='__main__':main()
