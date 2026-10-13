"""Read-only own-player observation; never attach or control the AI bridge."""
from pathlib import Path
import sys, json, struct, time, collections, argparse, ctypes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aoe_autopilot.backend import Controller, PROTOCOL

def observe(controller, all_players=False):
    snap = controller.status()
    m = controller.memory
    def q(p, o=0): return struct.unpack('<Q', m.read(p+o, 8))[0]
    rows = []
    node = q(q(snap['player'], 0x18), 8)
    for _ in range(100000):
        if not node: break
        u = q(node); t = q(u, 0x18)
        head = m.read(u, 0x90)
        if head[0x80] in (0, 2):
            rows.append({'address': hex(u), 'state': head[0x80],
                'kind': struct.unpack('<h', m.read(t+0x32, 2))[0],
                'type_id': struct.unpack('<h', m.read(t+0x2c, 2))[0]})
        node = q(node, 8)
    result = {'status': snap, 'units': rows,
        'counts': dict(collections.Counter(f"{r['kind']}:{r['type_id']}:{r['state']}" for r in rows))}
    ai = snap['ai']
    if ai:
        result['scripts'] = [m.read(ai+off, 256).split(b'\x00')[0].decode('utf-8', errors='replace') for off in (0x130, 0x10df0)]
    if all_players:
        player_count = struct.unpack('<i', m.read(snap['world']+0x70, 4))[0]
        table = q(snap['world'], 0x40)
        others = []
        for index in range(1, player_count):
            p = q(table, index*8)
            a = q(p, 0x3c0)
            counts = collections.Counter()
            node = q(q(p, 0x18), 8)
            for _ in range(100000):
                if not node: break
                u = q(node); t = q(u, 0x18)
                state = m.read(u+0x80, 1)[0]
                if state in (0, 2):
                    kind = struct.unpack('<h', m.read(t+0x32, 2))[0]
                    type_id = struct.unpack('<h', m.read(t+0x2c, 2))[0]
                    counts[f'{kind}:{type_id}:{state}'] += 1
                node = q(node, 8)
            others.append({'player_id': index,
                'name': m.read(q(p, 0x50), 128).split(b'\x00')[0].decode('utf-8', errors='replace'),
                'control_type': m.read(p+0x68, 1)[0], 'player_state': m.read(p+0xc0, 1)[0],
                'resources': list(struct.unpack('<4f', m.read(q(p, 0x80), 16))),
                'ai': hex(a), 'counts': dict(counts),
                'build_script': m.read(a+0x130, 256).split(b'\x00')[0].decode('utf-8', errors='replace') if a else None})
        result['players'] = others
    # OpenFileMapping (rather than mmap creation) avoids creating an empty bridge.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenFileMappingW.restype = ctypes.c_void_p
    kernel.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    kernel.MapViewOfFile.restype = ctypes.c_void_p
    kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenFileMappingW(4, False, f'Local\\AoENative_v{PROTOCOL}_{m.pid}')
    if handle:
        view = kernel.MapViewOfFile(handle, 4, 0, 0, 4096)
        try:
            if view:
                raw = ctypes.string_at(view, 4096)
                h = struct.unpack_from('<32I', raw)
                result['bridge'] = {'enabled': h[8], 'updates': h[13], 'units': h[14], 'villagers': h[15], 'buildings': h[16],
                    'sequence': h[4], 'ack': h[6], 'owner': h[10], 'session_valid': h[20],
                    'world': struct.unpack_from('<Q', raw, 128)[0], 'player': struct.unpack_from('<Q', raw, 136)[0],
                    'message': raw[688:944].split(b'\x00')[0].decode('utf-8', errors='replace')}
                kernel.GetTickCount.restype = ctypes.c_uint32
                result['bridge']['heartbeat_age_ms'] = (kernel.GetTickCount()-h[9]) & 0xffffffff
                kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                kernel.OpenProcess.restype = ctypes.c_void_p
                owner = kernel.OpenProcess(0x1000, False, h[10])
                if owner:
                    code = ctypes.c_uint32()
                    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
                    kernel.GetExitCodeProcess(owner, ctypes.byref(code))
                    result['bridge']['owner_exit_code'] = code.value
                    kernel.CloseHandle(owner)
                else: result['bridge']['owner_open_error'] = ctypes.get_last_error()
        finally:
            if view: kernel.UnmapViewOfFile(view)
            kernel.CloseHandle(handle)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--interval', type=float, default=15)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--all-players', action='store_true')
    args = parser.parse_args()
    c = Controller(); c.connect()
    result = []
    for index in range(args.samples):
        sample = observe(c, args.all_players); result.append(sample)
        print(json.dumps({k:v for k,v in sample.items() if k!='units'}, ensure_ascii=False), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if index+1 < args.samples: time.sleep(args.interval)
    c.memory.close()
