import struct
import unittest
from unittest.mock import patch
from aoe_autopilot.backend import Controller,ControlError,Bridge,MAGIC,SIZE

class Memory:
    pid=1;creation_time=2
    def __init__(self):self.blobs={}
    def put(self,address,data):self.blobs[address]=bytearray(data)
    def read(self,address,size):
        for start,data in self.blobs.items():
            if start<=address and address+size<=start+len(data):return bytes(data[address-start:address-start+size])
        raise OSError('bad pointer')
    def is_alive(self):return True

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.c=Controller();self.m=Memory();self.c.memory=self.m;self.c.base=0x100000000
        self.game=0x10000;self.world=0x20000;self.comm=0x30000;self.player=0x40000
        self.m.put(self.c.base+0xf96b48,struct.pack('<Q',self.game))
        self.m.put(self.c.base+0xf96ab8,struct.pack('<Q',self.comm))
        g=bytearray(0xcb0);struct.pack_into('<Q',g,0x660,self.world);struct.pack_into('<i',g,0xc14,-1)
        self.m.put(self.game,g)
        comm=bytearray(24);struct.pack_into('<i',comm,12,4);self.m.put(self.comm,comm)
        world=bytearray(0x200);struct.pack_into('<Q',world,0,self.c.base+0xbc8958)
        struct.pack_into('<Q',world,0x40,0x50000);struct.pack_into('<Q',world,0x60,0x60000)
        struct.pack_into('<i',world,0x70,2);struct.pack_into('<i',world,0x7c,1000)
        self.m.put(self.world,world);self.m.put(0x50000,struct.pack('<QQ',0x70000,self.player))
        p=bytearray(0x400);struct.pack_into('<Q',p,0,self.c.base+0xbc3630)
        p[0x68]=1;struct.pack_into('<h',p,0x6a,1);struct.pack_into('<Q',p,0x80,0x80000)
        self.m.put(self.player,p);self.m.put(0x80000,struct.pack('<4f',200,100,150,0))
    def test_identifies_unique_human_in_offline_random_map(self):
        snap=self.c.snapshot();self.assertEqual(snap['player_id'],1);self.assertEqual(snap['ai'],0)
    def test_random_map_with_conquest_victory_is_not_deathmatch(self):
        struct.pack_into('<i',self.m.blobs[self.game],0xc48,1)
        snap=self.c.snapshot()
        self.assertEqual(snap['mode'],1)
        self.assertEqual(snap['victory_condition'],1)
        self.assertEqual(snap['victory_condition_name'],'征服')
        self.assertEqual(snap['game_type'],'random_map')
        self.assertEqual(snap['mode_name'],'随机地图')

    def test_deathmatch_with_standard_victory_is_not_random_map(self):
        self.m.blobs[self.game][0xcac]=1
        snap=self.c.snapshot()
        self.assertEqual(snap['mode'],0)
        self.assertEqual(snap['victory_condition'],0)
        self.assertEqual(snap['victory_condition_name'],'标准')
        self.assertEqual(snap['game_type'],'deathmatch')
        self.assertEqual(snap['mode_name'],'死斗')

    def test_rewind_with_same_pointers_invalidates_session(self):
        snap=self.c.snapshot();self.c.identity={k:snap[k] for k in ('world','player','scenario','player_id')}
        struct.pack_into('<i',self.m.blobs[self.world],0x7c,999)
        with self.assertRaisesRegex(ControlError,'时间倒退'):self.c.snapshot()

    def test_same_process_id_with_different_creation_time_is_rejected(self):
        snap=self.c.snapshot();self.c.identity={k:snap[k] for k in ('world','player','scenario','player_id','creation_time')}
        self.m.creation_time=3
        with self.assertRaisesRegex(ControlError,'进程身份'):self.c.snapshot()

    def test_human_with_an_ai_pointer_is_not_reported_as_released(self):
        struct.pack_into('<Q',self.m.blobs[self.player],0x3c0,0x123456)
        with patch('aoe_autopilot.backend.Bridge') as cls:
            cls.return_value.command.return_value={'enabled':False,'message':'claimed success'}
            with self.assertRaisesRegex(ControlError,'回读校验'):self.c.off()
            cls.return_value.attach.assert_called_once()

    def test_manual_recovery_is_allowed_after_victory_defeat(self):
        self.m.blobs[self.player][0xc0]=1
        self.assertFalse(self.c.off()['enabled'])

    def test_off_does_not_call_remote_bridge_after_match_change(self):
        snap=self.c.snapshot();self.c.identity={k:snap[k] for k in ('world','player','scenario','player_id')}
        from unittest.mock import Mock
        self.c.bridge=Mock();self.c.bridge.native_status.return_value={'session_valid':True}
        struct.pack_into('<Q',self.m.blobs[self.world],0x60,0x61000)
        with self.assertRaises(ControlError):self.c.off()
        self.c.bridge.command.assert_not_called()

    def test_padding_byte_is_not_treated_as_player_state(self):
        self.m.blobs[self.player][0x69]=0xff
        self.assertEqual(self.c.snapshot()['player_id'],1)
    def test_multiplayer_campaign_replay_and_editor_are_rejected(self):
        for offset,values in [(0xc10,b'\1'),(0xc11,b'\1'),(0xc48,struct.pack('<i',7)),(0xc14,struct.pack('<i',0))]:
            original=self.m.blobs[self.game][offset:offset+len(values)]
            self.m.blobs[self.game][offset:offset+len(values)]=values
            with self.assertRaises(ControlError):self.c.snapshot()
            self.m.blobs[self.game][offset:offset+len(values)]=original
        self.m.blobs[self.comm][20]=1
        with self.assertRaises(ControlError):self.c.snapshot()
    def test_changed_match_and_player_are_rejected(self):
        snap=self.c.snapshot();self.c.identity={k:snap[k] for k in ('world','player','scenario','player_id')}
        struct.pack_into('<Q',self.m.blobs[self.world],0x60,0x61000)
        with self.assertRaises(ControlError):self.c.snapshot()
    def test_wrong_player_vtable_is_rejected(self):
        struct.pack_into('<Q',self.m.blobs[self.player],0,0xdead)
        with self.assertRaises(ControlError):self.c.snapshot()
    def test_victory_defeat_state_is_never_treated_as_pause(self):
        self.m.blobs[self.player][0xc0]=1
        with self.assertRaises(ControlError):self.c.snapshot()

class ProtocolTests(unittest.TestCase):
    def test_native_snapshot_layout_and_pending_command(self):
        b=object.__new__(Bridge);b.shared=bytearray(SIZE)
        struct.pack_into('<3I',b.shared,0,MAGIC,4,SIZE)
        struct.pack_into('<I',b.shared,4*4,3);struct.pack_into('<I',b.shared,6*4,2)
        struct.pack_into('<I',b.shared,8*4,1);struct.pack_into('<Q',b.shared,152,0x123456789)
        struct.pack_into('<4f',b.shared,160,10,20,30,40)
        b.shared[176:185]=b'Greek.ai\0';b.shared[432:447]=b'RANDOM MAP.PER\0'
        result=b.native_status()
        self.assertTrue(result['pending']);self.assertTrue(result['enabled'])
        self.assertEqual(result['ai'],0x123456789);self.assertEqual(result['resources'],[10,20,30,40])
        self.assertEqual(result['build_list'],'Greek.ai')
    def test_release_cancels_pending_enable_without_waiting_for_simulation(self):
        from unittest.mock import Mock
        b=object.__new__(Bridge);b.shared=bytearray(SIZE)
        b.set_header(4,3);b.set_header(6,2);b.set_header(5,1)
        b.export=Mock(return_value=123)
        def release(address):
            b.set_header(5,2);b.set_header(6,b.header(4));return 1
        b._call=Mock(side_effect=release)
        result=b.command(2)
        self.assertFalse(result['pending']);self.assertEqual(b.header(5),2)
        b._call.assert_called_once_with(123)

    def test_pending_command_is_never_overwritten(self):
        b=object.__new__(Bridge);b.shared=bytearray(SIZE)
        struct.pack_into('<I',b.shared,4*4,3);struct.pack_into('<I',b.shared,6*4,2)
        before=bytes(b.shared)
        with self.assertRaises(ControlError):b.command(1)
        self.assertEqual(bytes(b.shared),before)


class BridgeCloseTests(unittest.TestCase):
    class Buffer(bytearray):
        closed=False
        def close(self):self.closed=True
    def bridge(self):
        from unittest.mock import Mock
        import os
        b=object.__new__(Bridge);b.shared=self.Buffer(SIZE);b._heartbeat_pump=None
        b.handle=123;b.kernel=Mock();b.set_header(10,os.getpid());b.started=True
        return b
    def test_normal_close_releases_controller_lease(self):
        b=self.bridge();buf=b.shared;b.close()
        self.assertEqual(struct.unpack_from('<I',buf,40)[0],0)
        self.assertTrue(buf.closed);self.assertFalse(b.started)
    def test_active_close_preserves_watchdog_lease_until_expiry(self):
        b=self.bridge();b.set_header(8,1);buf=b.shared;b.close()
        self.assertNotEqual(struct.unpack_from('<I',buf,40)[0],0)

if __name__=='__main__':unittest.main()
