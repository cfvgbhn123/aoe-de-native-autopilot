from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import mmap
from pathlib import Path
import struct
import subprocess
import time
import sys
import os
from game_common.win32 import ProcessMemory, processes, pe_architecture
from game_common.native import ProcessLink, BridgeError
from game_common.heartbeat import HeartbeatPump

ROOT=Path(__file__).resolve().parents[1]
DLL=ROOT/'build'/'aoe'/'aoe_native_v6.dll'
SHA256='06a3e142b526741a8c23390a2a4088060f41e34b5715cd0b4f2ad442e9c8506a'
PROTOCOL=6
MAGIC=0x414f4541
SIZE=4096
SIGNATURES={0x2c4450:'48895c2408574883ec208bda488bf9e8',0x303d00:'4c8bdc55498d6ba14881eca000000048',
            0x2fee00:'48895c2410488974241848897c242041',
            0x2c3fd0:'488bc44c894820448940184889480855',
            0x2b5bd0:'48895c2420565741544883ec20418bf8',
            0x353980:'4963c04c8d0576ccdf004863d2498b04'}

def u64(data,offset=0):return struct.unpack_from('<Q',data,offset)[0]
def i32(data,offset=0):return struct.unpack_from('<i',data,offset)[0]

class ControlError(RuntimeError):pass

def ensure_bridge_build():
    source=ROOT/'native'/'aoe_native_bridge.cpp'
    builder=ROOT/'native'/'build_aoe.py'
    digest=hashlib.sha256(source.read_bytes()+builder.read_bytes()).hexdigest()
    manifest=DLL.with_suffix('.json')
    try:
        record=json.loads(manifest.read_text(encoding='utf-8'))
        valid=(record['protocol']==PROTOCOL and record['source_sha256']==digest and
               record['dll_sha256']==hashlib.sha256(DLL.read_bytes()).hexdigest())
    except (OSError,ValueError,KeyError):valid=False
    if not valid:
        result=subprocess.run([sys.executable,'-X','utf8',str(builder)],cwd=ROOT,
            capture_output=True,text=True,errors='replace',timeout=120)
        if result.returncode:raise ControlError('桥接编译失败：'+(result.stdout+result.stderr)[-2400:])
        record=json.loads(manifest.read_text(encoding='utf-8'))
    return record

class Bridge(ProcessLink):
    """Reuse the toolkit's process APIs with a separate x64 protocol."""
    def header(self,index):return struct.unpack_from('<I',self.shared,index*4)[0]
    def set_header(self,index,value):struct.pack_into('<I',self.shared,index*4,value&0xffffffff)
    def heartbeat(self):
        if self._heartbeat_pump:self._heartbeat_pump.refresh()
        else:self._write_heartbeat()

    def export(self,module_name,name,depth=0):
        if depth>4:raise ControlError('导出转发过深')
        module_name=module_name.lower()
        if not module_name.endswith(('.dll','.exe')):module_name+='.dll'
        modules=self.modules()
        if module_name not in modules:raise ControlError(f'模块未载入：{module_name}')
        base,_=modules[module_name];read=self.memory.read
        dos=read(base,64)
        pe=base+struct.unpack_from('<I',dos,60)[0]
        head=read(pe,160)
        if dos[:2]!=b'MZ' or head[:4]!=b'PE\0\0' or struct.unpack_from('<H',head,24)[0]!=0x20b:
            raise ControlError('必须使用 64 位 PE 模块')
        rva,size=struct.unpack_from('<II',head,136)
        table=read(base+rva,40)
        first,nfunc,nname,funcs,names,ordinals=struct.unpack_from('<6I',table,16)
        if not 0<nfunc<=65536 or not 0<=nname<=65536:raise ControlError('导出表无效')
        addresses=struct.unpack(f'<{nfunc}I',read(base+funcs,nfunc*4))
        def cstring(address):
            result=bytearray()
            for off in range(512):
                char=read(address+off,1)
                if char==b'\0':return result.decode('ascii')
                result.extend(char)
            raise ControlError('导出名过长')
        ordinal=None
        if name.startswith('#'):ordinal=int(name[1:])-first
        else:
            namervas=struct.unpack(f'<{nname}I',read(base+names,nname*4))
            nums=struct.unpack(f'<{nname}H',read(base+ordinals,nname*2))
            for k,address in enumerate(namervas):
                if cstring(base+address)==name:ordinal=nums[k];break
        if ordinal is None or not 0<=ordinal<nfunc:raise ControlError(f'导出不存在：{name}')
        target=addresses[ordinal]
        if rva<=target<rva+size:
            mod,fn=cstring(base+target).split('.',1)
            return self.export(mod,fn,depth+1)
        return base+target

    def attach(self,snap):
        if ctypes.sizeof(ctypes.c_void_p)!=8:raise ControlError('需要 64 位 Python')
        record=ensure_bridge_build()
        self.handle=self.kernel.OpenProcess(0x043a,False,self.memory.pid)
        if not self.handle:raise self._error('OpenProcess(AoE bridge)')
        self.shared=mmap.mmap(-1,SIZE,tagname=f'Local\\AoENative_v{PROTOCOL}_{self.memory.pid}')
        current=struct.unpack_from('<3I',self.shared)
        if current not in ((0,0,0),(MAGIC,PROTOCOL,SIZE)):raise ControlError('已有桥接协议不兼容')
        if current[0]==0:
            self.shared[:]=b'\0'*SIZE
            struct.pack_into('<3I',self.shared,0,MAGIC,PROTOCOL,SIZE)
        if self.header(8) and struct.unpack_from('<QQQ',self.shared,128)!=(snap['world'],snap['player'],snap['scenario']):
            raise ControlError('已有接管属于其他对局')
        owner=self.header(10)
        age=(self.kernel.GetTickCount()-self.header(9))&0xffffffff
        if owner and owner!=os.getpid() and age<=5000:
            raise ControlError('已有其他控制台持有接管连接，请先退出旧控制台')
        self.set_header(11,snap['player_id'])
        struct.pack_into('<QQQ',self.shared,128,snap['world'],snap['player'],snap['scenario'])
        self.set_header(10, __import__('os').getpid())
        self._write_heartbeat()
        modules=self.modules()
        if DLL.name.lower() not in modules:
            payload=(str(DLL.resolve())+'\0').encode('utf-16le')
            remote=self.kernel.VirtualAllocEx(self.handle,None,len(payload),0x3000,4)
            if not remote:raise self._error('VirtualAllocEx(DLL path)')
            completed=False
            try:
                buf=ctypes.create_string_buffer(payload);written=ctypes.c_size_t()
                if not self.kernel.WriteProcessMemory(self.handle,remote,buf,len(payload),ctypes.byref(written)) or written.value!=len(payload):
                    raise self._error('WriteProcessMemory(DLL path)')
                self._call(self.export('kernel32.dll','LoadLibraryW'),remote)
                completed=True
                if DLL.name.lower() not in self.modules():raise ControlError('DLL 未载入')
            finally:
                if completed:self.kernel.VirtualFreeEx(self.handle,remote,0,0x8000)
        elif Path(modules[DLL.name.lower()][1]).resolve()!=DLL.resolve():
            raise ControlError('已加载的桥接来自其他目录')
        if self._call(self.export(DLL.name,'AoeBuildId'))!=record['build_id']:
            raise ControlError('游戏内已加载旧版桥接，请退出游戏后重新启动')
        if self._call(self.export(DLL.name,'AoeInitialize'))!=1:raise ControlError('桥接版本或对局校验失败')
        self.sequence=self.header(4)
        self._heartbeat_pump=HeartbeatPump(self._write_heartbeat,lease_seconds=10)
        self._heartbeat_pump.start()
        self.started=True

    def _write_heartbeat(self):
        if self.shared is not None:self.set_header(9,self.kernel.GetTickCount())

    def command(self,action,timeout=5):
        if action==2:
            if self._call(self.export(DLL.name,'AoeRelease'))!=1:
                raise ControlError('恢复人工失败：'+self.native_status()['message'])
            return self.native_status()
        if self.header(4)!=self.header(6):raise ControlError('上一条命令仍等待游戏推进，请取消暂停后重试')
        self.heartbeat()
        self.sequence=(self.header(4)+1)&0xffffffff
        self.set_header(5,action);self.set_header(4,self.sequence)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.heartbeat()
            if self.header(6)==self.sequence:
                if self.header(7):raise ControlError(self.native_status()['message'])
                return self.native_status()
            time.sleep(.05)
        raise ControlError('命令已排队，等待游戏推进。请取消暂停；不要重复开启接管。')

    def native_status(self):
        for _ in range(10):
            seq=self.header(18)
            if seq&1:continue
            data=self.shared[:960]
            if seq==self.header(18):break
        else:raise ControlError('游戏快照正在更新，请重试')
        h=struct.unpack_from('<32I',data)
        string=lambda off:data[off:off+256].split(b'\0',1)[0].decode('utf-8',errors='replace')
        return {'enabled':bool(h[8]),'result':h[7],'time_ms':h[12],'updates':h[13],
            'units':h[14],'villagers':h[15],'buildings':h[16],'stage':h[17],
            'ai':u64(data,152),'resources':list(struct.unpack_from('<4f',data,160)),
            'build_list':string(176),'personality':string(432),'message':string(688),
            'pending':h[4]!=h[6],'session_valid':bool(h[20]),'cleanup_pending':bool(h[21]),
            'repaired_builders':h[22],'seeded_resources':h[23],
            'seed_resource_counts':list(h[24:28]),'protocol':PROTOCOL}

    def close(self):
        if self._heartbeat_pump:self._heartbeat_pump.stop();self._heartbeat_pump=None
        if self.shared:
            if self.header(10)==os.getpid() and not self.header(8) and self.header(4)==self.header(6):
                self.set_header(10,0)
            self.shared.close();self.shared=None
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None
        self.started=False

class Controller:
    def __init__(self,game_dir=None,pid=None):
        self.game_dir=Path(game_dir or json.loads((ROOT/'aoe_ai_config.json').read_text(encoding='utf-8'))['game_dir'])
        self.pid=pid;self.memory=None;self.base=0;self.bridge=None;self.identity=None;self.last_time_ms=None
        self.journal=ROOT/'aoe_ai_logs'/'recovery.json'

    def connect(self):
        exe=(self.game_dir/'AoEDE_s.exe').resolve()
        if not exe.exists():raise ControlError('找不到 AoEDE_s.exe，请修改 aoe_ai_config.json 的 game_dir')
        if hashlib.sha256(exe.read_bytes()).hexdigest()!=SHA256:raise ControlError('游戏版本未适配，已禁止写入；当前仅支持 Build 97381')
        if pe_architecture(exe)!='x64' or ctypes.sizeof(ctypes.c_void_p)!=8:raise ControlError('游戏与 Python 均须为 64 位')
        candidates=[p for p in processes() if p['name'].lower()=='aoede_s.exe'
            and p.get('path') and Path(p['path']).resolve()==exe and (self.pid is None or p['pid']==self.pid)]
        if len(candidates)!=1:raise ControlError('未找到唯一的帝国时代 I 决定版进程，请先开局')
        if self.memory:self.close()
        self.identity=None;self.last_time_ms=None
        mem=ProcessMemory(candidates[0]['pid'])
        try:
            bridge=Bridge(mem)
            modules=bridge.modules()
            self.base=modules['aoede_s.exe'][0]
            for rva,sig in SIGNATURES.items():
                if mem.read(self.base+rva,16)!=bytes.fromhex(sig):raise ControlError('运行时函数签名不匹配，已禁止写入')
            self.memory=mem
            snap=self.snapshot(allow_ended=True)
            self.identity={k:snap[k] for k in ('world','player','scenario','player_id','creation_time')}
            self.last_time_ms=snap['time_ms']
        except BaseException:
            mem.close();self.memory=None;raise

    def snapshot(self, *, allow_ended=False):
        m=self.memory
        if m is None or not m.is_alive():raise ControlError('游戏未连接或已经退出')
        g=u64(m.read(self.base+0xf96b48,8));gh=m.read(g,0xcb0)
        comm=u64(m.read(self.base+0xf96ab8,8));ch=m.read(comm,24)
        if i32(ch,12)!=4 or ch[20] or gh[0xc10] or gh[0xc11] or i32(gh,0xc14)!=-1 or i32(gh,0xc48) not in (0,1):
            raise ControlError(f'当前只支持标准/征服胜利条件的单机随机地图/死斗；战役、联机、回放和编辑器测试不接管（胜利条件={i32(gh,0xc48)}，场景={i32(gh,0xc14)}）')
        world=u64(gh,0x660)
        wh=m.read(world,0x200)
        if u64(wh)!=self.base+0xbc8958 or not 2<=i32(wh,0x70)<=9:raise ControlError('对局尚未开始或世界对象无效')
        players=struct.unpack(f'<{i32(wh,0x70)}Q',m.read(u64(wh,0x40),i32(wh,0x70)*8))
        humans=[]
        for idx,p in enumerate(players[1:],1):
            if m.read(p+0x68,1)==b'\x01':humans.append((idx,p))
        if self.identity:
            if self.identity.get('creation_time',m.creation_time)!=m.creation_time:
                raise ControlError('游戏进程身份改变，请重新连接')
            if self.last_time_ms is not None and i32(wh,0x7c)<self.last_time_ms:
                raise ControlError('对局时间倒退，可能已读档；请重新连接')
            if self.bridge and not self.bridge.native_status()['session_valid']:
                raise ControlError('原生对局身份已失效，请重新连接')
            idx=self.identity['player_id'];p=players[idx] if idx<len(players) else 0
            if world!=self.identity['world'] or p!=self.identity['player'] or u64(wh,0x60)!=self.identity['scenario']:
                raise ControlError('已换局或读档，请关闭接管后重新连接')
        elif len(humans)==1:idx,p=humans[0]
        else:
            # A native watchdog may still be waiting for a paused game to tick.
            mapping=mmap.mmap(-1,SIZE,tagname=f'Local\\AoENative_v{PROTOCOL}_{m.pid}')
            try:
                h=struct.unpack_from('<32I',mapping)
                idx=h[11]
                if (h[:3]!=(MAGIC,PROTOCOL,SIZE) or not h[20] or not 0<idx<len(players) or
                    u64(mapping,128)!=world or u64(mapping,136)!=players[idx] or u64(mapping,144)!=u64(wh,0x60) or
                    i32(wh,0x7c)<h[12]):
                    raise ControlError('不能确定唯一的本机人工玩家')
                p=players[idx]
            finally:mapping.close()
        ph=m.read(p,0x400)
        if u64(ph)!=self.base+0xbc3630 or struct.unpack_from('<h',ph,0x6a)[0]!=idx:raise ControlError('玩家对象不匹配')
        if ph[0x68] not in (1,3):raise ControlError('玩家控制类型未适配')
        if ph[0xc0]!=0 and not allow_ended:raise ControlError('玩家已结束比赛，不能开启接管')
        resources=struct.unpack('<4f',m.read(u64(ph,0x80),16))
        self.last_time_ms=i32(wh,0x7c)
        victory_condition=i32(gh,0xc48)
        deathmatch=bool(gh[0xcac])
        # The legacy mode value is a victory condition. Native AI setup chooses
        # RANDOM MAP.PER / DEATH MATCH.PER from the separate +0xCAC flag.
        return {'mode':victory_condition,'mode_name':'死斗' if deathmatch else '随机地图',
            'game_type':'deathmatch' if deathmatch else 'random_map',
            'victory_condition':victory_condition,
            'victory_condition_name':'征服' if victory_condition==1 else '标准',
            'pid':m.pid,'creation_time':m.creation_time,'world':world,'player':p,
            'scenario':u64(wh,0x60),'player_id':idx,'time_ms':i32(wh,0x7c),
            'type':ph[0x68],'ai':u64(ph,0x3c0),'player_state':ph[0xc0],
            'computer_ai_flag':i32(ph,0x354),
            'ai_stopped':ph[0x68]==1,
            'difficulty':i32(gh,0xaa0),'resources':list(resources),'build':'97381'}

    def on(self):
        if self.memory is None:self.connect()
        snap=self.snapshot()
        if not self.bridge:
            bridge=Bridge(self.memory)
            try:bridge.attach(snap)
            except BaseException:bridge.close();raise
            self.bridge=bridge
        self.journal.parent.mkdir(exist_ok=True)
        self.journal.write_text(json.dumps({**snap,'state':'requested','sha256':SHA256},ensure_ascii=False,indent=2),encoding='utf-8')
        result=self.bridge.command(1)
        after=self.snapshot()
        if after['type']!=3 or after['computer_ai_flag']!=1 or not after['ai'] or after['ai']!=result.get('ai') or not result.get('enabled'):
            raise ControlError('开启接管后的玩家状态未通过回读校验')
        self.journal.write_text(json.dumps({**snap,'state':'enabled','sha256':SHA256,'native':result,'after':after},ensure_ascii=False,indent=2),encoding='utf-8')
        return result

    def off(self):
        snap=self.snapshot(allow_ended=True)
        if self.bridge:
            result=self.bridge.command(2)
        else:
            if snap['type']==1 and not snap['ai']:return {'enabled':False,'message':'人工控制'}
            bridge=Bridge(self.memory)
            try:bridge.attach(snap)
            except BaseException:bridge.close();raise
            self.bridge=bridge;result=bridge.command(2)
        after=self.snapshot(allow_ended=True)
        if after['type']!=1 or after['ai']!=0 or result.get('enabled'):
            raise ControlError('恢复入口返回后玩家仍有 AI，人工恢复未通过回读校验')
        result['verified_manual']=True
        if self.journal.exists():
            record=json.loads(self.journal.read_text(encoding='utf-8'))
            if any(record.get(k)!=after[k] for k in ('pid','creation_time','world','player','scenario')):
                record={**after,'sha256':SHA256}
            record['state']='released';record['after']=after
            self.journal.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        return result

    def status(self):
        result=self.snapshot(allow_ended=True)
        if self.bridge:result['native']=self.bridge.native_status()
        return result

    def tick(self):
        if self.bridge:
            self.snapshot() # Do not renew a bridge whose match identity changed.
            self.bridge.heartbeat()

    def close(self):
        try:
            if self.bridge and self.memory and self.memory.is_alive():self.off()
        finally:
            if self.bridge:self.bridge.close();self.bridge=None
            if self.memory:self.memory.close();self.memory=None
            self.identity=None;self.last_time_ms=None
