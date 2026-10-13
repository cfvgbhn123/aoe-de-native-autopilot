"""Validate decoded fields and Gaia target lookup without a game process."""
import struct
import unittest

from tools.inspect_economy import (
    AI_SIZE, INFO, TACTICAL, find_world_units, parse_tactical, read_known_resources,
)


class Memory:
    def __init__(self):
        self.blobs = {}

    def put(self, address, data):
        self.blobs[address] = bytearray(data)

    def read(self, address, size):
        for start, data in self.blobs.items():
            if start <= address and address + size <= start + len(data):
                return bytes(data[address - start:address - start + size])
        raise OSError('Unmapped fixture address')


class EconomyTests(unittest.TestCase):
    def test_gatherer_record_and_distribution_decode(self):
        ai = bytearray(AI_SIZE)
        struct.pack_into('<2i', ai, TACTICAL + 0x1794, 1, 12)
        struct.pack_into('<4i', ai, TACTICAL + 0x1b58, 3, 2, 0, 0)
        struct.pack_into('<4i', ai, TACTICAL + 0x1b68, 5, 7, 0, 0)
        struct.pack_into('<6i', ai, TACTICAL + 0x4d4, 111, 1, 75, 10, 999, -1)
        result = parse_tactical(ai)
        self.assertEqual(result['desired_gatherer_count'], 12)
        self.assertEqual(result['actual_distribution'], [3, 2, 0, 0])
        self.assertEqual(result['desired_distribution'], [5, 7, 0, 0])
        record = result['gatherers'][0]
        self.assertEqual((record['unit_id'], record['resource_name'], record['target_id']),
                         (111, 'wood', 999))
        self.assertEqual(record['task_tick_ms'], 0xffffffff)

    def test_corrupt_gatherer_count_is_rejected_before_table_walk(self):
        for count in (-1, 201):
            with self.subTest(count=count):
                ai = bytearray(AI_SIZE)
                struct.pack_into('<i', ai, TACTICAL + 0x1794, count)
                with self.assertRaisesRegex(ValueError, 'gatherer count'):
                    parse_tactical(ai)

    def test_resource_summary_counts_all_entries_even_when_details_are_limited(self):
        ai = bytearray(AI_SIZE)
        struct.pack_into('<Q', ai, INFO + 0x478 + 8, 0x10000)
        struct.pack_into('<i', ai, INFO + 0x498 + 4, 3)
        table = bytearray(3 * 0x1c)
        for index, unit_id in enumerate((10, 20, 30)):
            struct.pack_into('<i', table, index * 0x1c, unit_id)
        table[0x19] = 2
        table[0x1c + 0x1a] = 1
        memory = Memory()
        memory.put(0x10000, table)
        resources = read_known_resources(memory, ai, 1)
        wood = resources[1]
        self.assertEqual(wood['currently_selectable_count'], 1)
        self.assertEqual(wood['cooldown_count'], 1)
        self.assertEqual(wood['unavailable_count'], 1)
        self.assertTrue(wood['entries_truncated'])
        self.assertEqual([entry['unit_id'] for entry in wood['entries']], [10])
        self.assertEqual(resources[0]['currently_selectable_count'], 0)

    def test_gaia_tree_and_unfinished_own_target_keep_distinct_states(self):
        memory = Memory()
        players = (0x10000, 0x20000)
        for owner, player in enumerate(players):
            unit_list = 0x30000 + owner * 0x1000
            node = 0x40000 + owner * 0x1000
            unit = 0x50000 + owner * 0x1000
            template = 0x60000 + owner * 0x1000
            memory.put(player + 0x18, struct.pack('<Q', unit_list))
            memory.put(unit_list + 8, struct.pack('<Q', node))
            memory.put(node, struct.pack('<QQ', unit, 0))
            head = bytearray(0x90)
            struct.pack_into('<i', head, 8, 100 + owner)
            struct.pack_into('<Q', head, 0x18, template)
            struct.pack_into('<3f', head, 0x5c, 4, 5, 0)
            head[0x80] = 2 if owner == 0 else 0
            memory.put(unit, head)
            definition = bytearray(0x34)
            struct.pack_into('<h', definition, 0x2c, 123 if owner == 0 else 103)
            struct.pack_into('<h', definition, 0x32, 15 if owner == 0 else 3)
            memory.put(template, definition)
        units, errors = find_world_units(memory, players, {100, 101, 999})
        self.assertEqual(errors, [])
        self.assertEqual(units[100]['owner'], 0)
        self.assertEqual(units[100]['kind'], 15)
        self.assertTrue(units[100]['completed_or_active'])
        self.assertEqual(units[100]['position'], [4.0, 5.0, 0.0])
        self.assertFalse(units[101]['completed_or_active'])
        self.assertEqual(units[101]['state'], 0)
        self.assertNotIn(999, units)


if __name__ == '__main__':
    unittest.main()
