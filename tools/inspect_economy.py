"""Read-only economy and target inspection for the verified AoE DE build.

Offsets come from the local build's TacticalAI updateGathererDistribution,
highLevelTaskGatherer, and InfoAI gameIDOfResObj routines. No bridge is attached.
"""
from pathlib import Path
import argparse
from datetime import datetime, timedelta, timezone
import json
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aoe_autopilot.backend import Controller, ControlError

TACTICAL = 0x10ef8
INFO = 0x938
AI_SIZE = 0x12d08
RESOURCE_NAMES = ('food', 'wood', 'stone', 'gold')


def q(data, offset=0):
    return struct.unpack_from('<Q', data, offset)[0]


def i(data, offset=0):
    return struct.unpack_from('<i', data, offset)[0]


def parse_tactical(ai_bytes):
    count = i(ai_bytes, TACTICAL + 0x1794)
    if not 0 <= count <= 200:
        raise ValueError(f'Invalid gatherer count: {count}')
    records = []
    for index in range(count):
        fields = struct.unpack_from('<6i', ai_bytes, TACTICAL + 0x4d4 + index * 0x18)
        records.append({
            'unit_id': fields[0], 'resource': fields[1],
            'resource_name': RESOURCE_NAMES[fields[1]] if 0 <= fields[1] < 4 else None,
            # Raw quotas/counters: their complete meaning is not yet verified.
            'raw_d2': fields[2], 'raw_d3': fields[3],
            'target_id': fields[4], 'task_tick_ms': fields[5] & 0xffffffff,
        })
    return {
        'gatherer_count': count,
        'desired_gatherer_count': i(ai_bytes, TACTICAL + 0x1798),
        'actual_distribution': list(struct.unpack_from('<4i', ai_bytes, TACTICAL + 0x1b58)),
        'desired_distribution': list(struct.unpack_from('<4i', ai_bytes, TACTICAL + 0x1b68)),
        'priority_indices': list(struct.unpack_from('<4i', ai_bytes, TACTICAL + 0x1b78)),
        'resource_weights': list(struct.unpack_from('<4i', ai_bytes, TACTICAL + 0x1b88)),
        'gatherers': records,
    }


def read_known_resources(memory, ai_bytes, entry_limit):
    resources = []
    for resource, name in enumerate(RESOURCE_NAMES):
        pointer = q(ai_bytes, INFO + 0x478 + resource * 8)
        count = i(ai_bytes, INFO + 0x498 + resource * 4)
        row = {'resource': resource, 'resource_name': name,
               'pointer': pointer, 'count': count, 'entries': []}
        if not 0 <= count <= 100000 or count and not pointer:
            row['error'] = 'Invalid known resource table'
        elif not count:
            row.update(unavailable_count=0, cooldown_count=0, currently_selectable_count=0,
                       entries_truncated=False)
        elif count:
            try:
                data = memory.read(pointer, count * 0x1c)
                entries = []
                for index in range(count):
                    offset = index * 0x1c
                    entries.append({
                        'unit_id': i(data, offset),
                        'cooldown_counter': data[offset + 0x19],
                        'unavailable_flag': data[offset + 0x1a],
                    })
                row['unavailable_count'] = sum(e['unavailable_flag'] == 1 for e in entries)
                row['cooldown_count'] = sum(e['cooldown_counter'] != 0 for e in entries)
                row['currently_selectable_count'] = sum(
                    e['unavailable_flag'] != 1 and e['cooldown_counter'] == 0 for e in entries
                )
                row['entries'] = entries[:entry_limit]
                row['entries_truncated'] = len(entries) > entry_limit
            except OSError as exc:
                row['error'] = str(exc)
        resources.append(row)
    return resources


def find_world_units(memory, players, wanted_ids):
    """Resolve only requested IDs, including Gaia's trees, mines, and animals."""
    found = {}
    errors = []
    for owner, player in enumerate(players):
        try:
            unit_list = q(memory.read(player + 0x18, 8))
            node = q(memory.read(unit_list + 8, 8)) if unit_list else 0
            seen = set()
            for _ in range(100000):
                if not node:
                    break
                if node in seen:
                    raise ValueError('Unit list cycle')
                seen.add(node)
                link = memory.read(node, 16)
                unit = q(link)
                head = memory.read(unit, 0x90)
                unit_id = i(head, 8)
                if unit_id in wanted_ids:
                    template = memory.read(q(head, 0x18), 0x34)
                    found[unit_id] = {
                        'unit_id': unit_id, 'address': unit, 'owner': owner,
                        'state': head[0x80],
                        'completed_or_active': head[0x80] == 2,
                        'kind': struct.unpack_from('<h', template, 0x32)[0],
                        'type_id': struct.unpack_from('<h', template, 0x2c)[0],
                        'position': list(struct.unpack_from('<3f', head, 0x5c)),
                    }
                node = q(link, 8)
            else:
                raise ValueError('Unit list limit reached')
        except (OSError, ValueError) as exc:
            errors.append({'owner': owner, 'error': str(exc)})
    return found, errors


def inspect(controller, *, resource_limit=100):
    if not 0 <= resource_limit <= 100000:
        raise ValueError('resource_limit must be between 0 and 100000')
    snap = controller.status()
    memory = controller.memory
    world_head = memory.read(snap['world'], 0x80)
    count = i(world_head, 0x70)
    if not 2 <= count <= 9:
        raise ValueError('Invalid world player count')
    players = struct.unpack(f'<{count}Q', memory.read(q(world_head, 0x40), count * 8))
    rows = []
    wanted = set()
    for player_id, player in enumerate(players[1:], 1):
        row = {'player_id': player_id, 'player': player}
        try:
            header = memory.read(player, 0x400)
            ai = q(header, 0x3c0)
            row.update(ai=ai, player_state=header[0xc0], control_type=header[0x68],
                       computer_ai_flag_354=i(header, 0x354))
            if ai:
                data = memory.read(ai, AI_SIZE)
                row['tactical'] = parse_tactical(data)
                row['known_resources'] = read_known_resources(memory, data, resource_limit)
                for entry in row['tactical']['gatherers']:
                    wanted.add(entry['unit_id'])
                    if entry['target_id'] >= 0:
                        wanted.add(entry['target_id'])
                for resource in row['known_resources']:
                    wanted.update(e['unit_id'] for e in resource['entries'] if e['unit_id'] >= 0)
        except (OSError, ValueError) as exc:
            row['error'] = str(exc)
        rows.append(row)
    targets, errors = find_world_units(memory, players, wanted)
    for row in rows:
        for entry in row.get('tactical', {}).get('gatherers', []):
            entry['unit'] = targets.get(entry['unit_id'])
            entry['target'] = targets.get(entry['target_id'])
            entry['target_found_in_world_lists'] = entry['target'] is not None
        for resource in row.get('known_resources', []):
            for entry in resource['entries']:
                entry['unit'] = targets.get(entry['unit_id'])
    return {
        'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'status': snap, 'resource_order': list(RESOURCE_NAMES), 'players': rows,
        'world_scan_errors': errors, 'snapshot_atomic': False,
        'remaining_resource_field': None,
        'note': 'Target existence/state and position are read directly; remaining resource quantity is not interpreted.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读检查帝国时代采集分配、已知资源及当前目标（包括Gaia）')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resource-limit', type=int, default=100,
                        help='每种资源保留多少个已知目标详情；汇总仍覆盖整个表')
    args = parser.parse_args(argv)
    controller = Controller(args.game_dir, args.pid)
    try:
        controller.connect()
        result = inspect(controller, resource_limit=args.resource_limit)
        content = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content + '\n', encoding='utf-8')
        if args.output:
            print(json.dumps({'output':str(args.output),'time_ms':result['status']['time_ms'],
                'computer_ai_flag':result['status']['computer_ai_flag'],
                'players':[{'player_id':p['player_id'], 'tactical':{k:v for k,v in (p.get('tactical') or {}).items() if k!='gatherers'}}
                           for p in result['players']]},ensure_ascii=False),flush=True)
        else:
            print(content, flush=True)
        return 0
    except (ControlError, OSError, ValueError) as exc:
        print(str(exc), flush=True)
        return 1
    finally:
        controller.close()


if __name__ == '__main__':
    raise SystemExit(main())
