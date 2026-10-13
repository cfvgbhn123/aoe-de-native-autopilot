"""Read-only villager orders, engine actions and tactical gatherer assignments.

Build 97381 only. Does not attach a bridge, send commands or write game memory.
python -X utf8 tools/inspect_villager_tasks.py --pid GAME_PID --out ...json
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game_common.win32 import ProcessMemory
from aoe_autopilot.backend import Bridge, SHA256

def q(b, o=0): return struct.unpack_from('<Q', b, o)[0]
def i(b, o=0): return struct.unpack_from('<i', b, o)[0]
def h(b, o=0): return struct.unpack_from('<h', b, o)[0]
def f(b, o=0): return struct.unpack_from('<f', b, o)[0]

ACTION_NAMES = {1: 'MoveTo', 4: 'Explore', 5: 'Gather', 8: 'Missile',
                9: 'Attack', 10: 'Bird', 20: 'RunAway', 21: 'Make',
                101: 'Build', 110: 'Hunt'}

def capture(mem, base):
    read = mem.read
    game = q(read(base + 0xf96b48, 8))
    world = q(read(game + 0x660, 8))
    wh = read(world, 0x200)
    n = i(wh, 0x70)
    if q(wh) != base + 0xbc8958 or not 2 <= n <= 9:
        raise RuntimeError('Unexpected world identity')
    rows, objects = [], {}
    def unit_ref(address):
        if not address: return None
        try:
            ub = read(address, 0x100)
            tb = read(q(ub, 0x18), 0x38)
            return {'address': address, 'id': i(ub, 8), 'type_id': h(tb, 0x2c),
                    'kind': h(tb, 0x32), 'state': ub[0x80], 'hp': f(ub, 0xc),
                    'x': f(ub, 0x5c), 'y': f(ub, 0x60)}
        except OSError as exc:
            return {'address': address, 'error': str(exc)}
    def action_ref(address, depth=0):
        if not address: return None
        if address in objects: return {'address': address, 'repeated': True}
        try:
            ab = read(address, 0x100)
            objects[address] = ab.hex()
            type_id = h(ab, 8)
            result = {'address': address, 'vtable_rva': q(ab) - base,
                      'type': type_id, 'type_name': ACTION_NAMES.get(type_id, 'Tribe/Unknown'),
                      'state': ab[0xa], 'owner': unit_ref(q(ab, 0x10)),
                      'target': unit_ref(q(ab, 0x18)), 'secondary_target': unit_ref(q(ab, 0x20)),
                      'target_ids': [i(ab, 0x28), i(ab, 0x2c)],
                      'timer_3c': f(ab, 0x3c),
                      'fields_60': [h(ab, o) for o in (0x60, 0x62, 0x64)],
                      'xyz_30': [f(ab, o) for o in (0x30, 0x34, 0x38)],
                      'raw': ab.hex()}
            # RGE_Action+0x48 owns its auxiliary ActionList (movement, return, etc.).
            sub = q(ab, 0x48)
            if sub and depth < 3: result['subactions'] = action_list(sub, depth + 1)
            return result
        except OSError as exc:
            return {'address': address, 'error': str(exc)}
    def action_list(address, depth=0):
        if not address: return None
        lb = read(address, 0x30)
        count = q(lb, 0x20)
        result = {'address': address, 'vtable_rva': q(lb) - base,
                  'owner': q(lb, 8), 'count': count, 'capacity': q(lb, 0x18)}
        if count > 64: return {**result, 'error': 'Unexpected action count'}
        addresses = struct.unpack(f'<{count}Q', read(q(lb, 0x10), count * 8)) if count else []
        result['actions'] = [action_ref(a, depth) for a in addresses]
        return result
    players = struct.unpack(f'<{n}Q', read(q(wh, 0x40), n * 8))
    for player_id, player in enumerate(players[1:], 1):
        pb = read(player, 0x400)
        ai = q(pb, 0x3c0)
        gatherers = []
        tactical = None
        if ai:
            ab = read(ai, 0x12d08)
            t = 0x10ef8
            gc = i(ab, t + 0x1794)
            if 0 <= gc <= 300:
                for k in range(gc):
                    off = t + 0x4d4 + k * 0x18
                    gatherers.append(dict(zip(('id', 'resource', 'desired_candidate', 'current_candidate',
                                                'target_id', 'last_task_tick'), struct.unpack_from('<6i', ab, off))))
            c = i(ab, 0x10f38)
            registered = list(struct.unpack(f'<{c}i', read(q(ab, 0x10f30), c * 4))) if 0 < c <= 300 else []
            tactical = {'registered_ids': registered, 'gatherer_count': gc,
                        'actual_distribution': list(struct.unpack_from('<4i', ab, t + 0x1b58)),
                        'desired_distribution': list(struct.unpack_from('<4i', ab, t + 0x1b68))}
        assignments = {g['id']: g for g in gatherers}
        row = {'player_id': player_id, 'player': player, 'ai': ai,
               'player_type': pb[0x68], 'computer_ai_flag_354': i(pb, 0x354),
               'resources': list(struct.unpack('<4f', read(q(pb, 0x80), 16))),
               'tactical': tactical, 'villagers': [], 'errors': []}
        lst = q(pb, 0x18)
        node = q(read(lst + 8, 8)) if lst else 0
        seen = set()
        while node and node not in seen and len(seen) < 100000:
            seen.add(node)
            nb = read(node, 16)
            address = q(nb)
            try:
                ub = read(address, 0x2e0)
                tb = read(q(ub, 0x18), 0x38)
                if h(tb, 0x32) == 4 and ub[0x80] == 2:
                    vr = unit_ref(address)
                    vr['assignment'] = assignments.get(vr['id'])
                    uai = q(ub, 0xd8)
                    if uai:
                        uh = read(uai, 0x200)
                        vr['unit_ai'] = {'address': uai, 'vtable_rva': q(uh) - base,
                                         'current_task': i(uh, 0x38), 'order': i(uh, 0x40),
                                         'target_id': i(uh, 0x44), 'target_kind': i(uh, 0x48),
                                         'target_xy': [f(uh, 0x50), f(uh, 0x54)],
                                         'last_fields': [i(uh, o) for o in (0x90, 0x94, 0x98, 0x9c, 0xa0)],
                                         'raw': uh.hex()}
                    vr['action_list'] = action_list(q(ub, 0x278))
                    row['villagers'].append(vr)
            except OSError as exc: row['errors'].append({'unit': address, 'error': str(exc)})
            node = q(nb, 8)
        row['summary'] = {'villagers': len(row['villagers']),
                          'action_counts': dict(Counter(v['action_list']['count'] if v['action_list'] else -1 for v in row['villagers'])),
                          'action_type_states': dict(Counter(f"{a['type']}:{a['state']}" for v in row['villagers']
                                                            for a in (v['action_list'] or {}).get('actions', []) if 'type' in a)),
                          'missing_assignment': sum(v['assignment'] is None for v in row['villagers']),
                          'targetless_wait_ids': [v['id'] for v in row['villagers']
                                                 for a in (v['action_list'] or {}).get('actions', [])
                                                 if a.get('state') == 16 and a.get('target') is None],
                          'build_wait_without_retry_ids': [v['id'] for v in row['villagers']
                                                          for a in (v['action_list'] or {}).get('actions', [])
                                                          if a.get('type') == 101 and a.get('state') == 16
                                                          and a.get('fields_60', [None])[0] == 0]}
        rows.append(row)
    return {'pid': mem.pid, 'creation_time': mem.creation_time, 'base': base,
            'world': world, 'time_ms': i(wh, 0x7c), 'players': rows}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pid', type=int, required=True)
    ap.add_argument('--base', type=lambda s: int(s, 0))
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    with ProcessMemory(args.pid, writable=False) as mem:
        if Path(mem.process_exe).name.lower() != 'aoede_s.exe': raise RuntimeError('Wrong executable')
        if hashlib.sha256(Path(mem.process_exe).read_bytes()).hexdigest() != SHA256: raise RuntimeError('Wrong game build')
        base = args.base or Bridge(mem).modules()['aoede_s.exe'][0]
        result = capture(mem, base)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(args.out), 'time_ms': result['time_ms'],
                      'players': [{'player_id': p['player_id'], **p['summary'], 'tactical': p['tactical']} for p in result['players']]}, ensure_ascii=False))

if __name__ == '__main__': main()
