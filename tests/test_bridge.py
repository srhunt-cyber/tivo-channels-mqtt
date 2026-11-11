import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tivo_channels_mqtt import storage
from tivo_channels_mqtt.bridge import Poller, RecoveryPolicy
from tivo_channels_mqtt.config import load
from tivo_channels_mqtt.model import normalize
from tivo_channels_mqtt.mqtt import discovery, Publisher

ROOT = Path(__file__).resolve().parents[1]


def config(directory):
    data = json.loads((ROOT / 'config.example.json').read_text())
    data['mqtt']['host'] = 'localhost'
    # Tests outside the accessibility suite isolate their own ADB behavior.
    data['accessibility_watchdog'] = False
    data['state_dir'] = directory
    return data


def live(number='7.1'):
    return {'status':'playing','channel':{'number':number,'name':'ABC'},
            'now_playing':{'title':'Program','episode_title':'Episode'}}


class ModelTests(unittest.TestCase):
    def test_live_metadata(self):
        result = normalize(live())
        self.assertEqual(result['channel'],'ABC 7.1')
        self.assertEqual(result['display'],'▶ ABC 7.1 · Program')
        self.assertEqual(result['tv_power'],'unknown')

    def test_stopped_clears_old_title(self):
        raw = live(); raw['status'] = 'stopped'
        result = normalize(raw)
        self.assertEqual(result['program_title'],'')
        self.assertEqual(result['channel'],'')

    def test_recording_is_displayed_but_not_reset_eligible(self):
        raw = live(); del raw['channel']
        self.assertEqual(normalize(raw)['program_title'],'Program')
        self.assertFalse(storage.live_playing(raw))

    def test_null_fields_and_long_titles(self):
        self.assertEqual(normalize({'status':'paused','channel':None})['display'],'⏸ Paused')
        raw = live(); raw['now_playing']['title'] = 'x'*500
        self.assertLessEqual(len(normalize(raw)['display']),255)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = config(self.temp.name)
        self.device = self.config['devices'][0]
        self.policy = RecoveryPolicy(self.device,self.config['recovery'],self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_two_low_readings_same_channel(self):
        self.assertFalse(self.policy.observe(live(),500,10000))
        self.assertTrue(self.policy.observe(live(),500,10015))

    def test_channel_change_restarts_counter(self):
        self.policy.observe(live(),500,10000)
        self.assertFalse(self.policy.observe(live('2.1'),500,10015))

    def test_paused_and_unknown_space_never_recover(self):
        raw = live(); raw['status']='paused'
        for _ in range(3):
            self.assertFalse(self.policy.observe(raw,300,10000))
            self.assertFalse(self.policy.observe(live(),None,10000))

    def test_failed_recovery_latch_survives_restart(self):
        self.policy.begin(10000)
        self.policy.finish(True,510)
        p = RecoveryPolicy(self.device,self.config['recovery'],self.temp.name)
        for now in (11000,11015):self.assertFalse(p.observe(live(),500,now))
        p.observe(live(),1500,11030)
        self.assertFalse(p.observe(live(),500,11045))
        self.assertTrue(p.observe(live(),500,11060))

    def test_cooldown_and_hourly_limit(self):
        p = self.policy
        p.begin(10000);p.finish(True,1500)
        p.observe(live(),500,10015)
        self.assertFalse(p.observe(live(),500,10030))
        self.assertTrue(p.observe(live(),500,10301))
        p.begin(10301);p.finish(True,1500)
        p.observe(live(),500,10602);self.assertTrue(p.observe(live(),500,10617))
        p.begin(10617);p.finish(True,1500)
        p.observe(live(),500,11000);self.assertFalse(p.observe(live(),500,11015))


class ConfigTests(unittest.TestCase):
    def test_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            data = config(directory)
            path = Path(directory)/'config.json'
            data['devices'][1]['ip']=data['devices'][0]['ip']
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):load(path)

    def test_relative_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            data=config(directory);data['state_dir']='state'
            path=Path(directory)/'config.json';path.write_text(json.dumps(data))
            self.assertEqual(load(path)['state_dir'],str(Path(directory)/'state'))


class DiscoveryTests(unittest.TestCase):
    def test_health_and_identity(self):
        c=config('/tmp');d=c['devices'][0]
        status=discovery(c,d,'playback');disk=discovery(c,d,'free_mib')
        self.assertNotEqual(status['availability'][1]['topic'],disk['availability'][1]['topic'])
        self.assertEqual(status['default_entity_id'],'sensor.kitchen_tivo_mqtt_playback')
        self.assertGreaterEqual(status['expire_after'],90)
        self.assertNotIn('command_topic',status)

    def test_publisher_can_be_constructed_without_network(self):
        p=Publisher(config('/tmp'))
        self.assertFalse(p.connected.is_set())
        p.client.disconnect()


class PollerTests(unittest.TestCase):
    def test_adb_failure_preserves_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(directory);publisher=Mock();p=Poller(c,publisher)
            with patch.object(storage,'api',return_value=live()),patch.object(storage,'free_mib',side_effect=RuntimeError('offline')),patch.object(storage,'connect'):
                p.poll(c['devices'][0])
            _,state,api_ok,adb_ok=publisher.publish.call_args.args
            self.assertEqual(state['channel_number'],'7.1')
            self.assertTrue(api_ok);self.assertFalse(adb_ok)

    def test_monitor_mode_never_changes_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(directory);publisher=Mock();p=Poller(c,publisher)
            with patch.object(storage,'api',return_value=live()),patch.object(storage,'free_mib',return_value=400),patch.object(storage,'recover') as recover:
                for _ in range(3):p.poll(c['devices'][0])
                recover.assert_not_called()

    def test_api_failure_clears_previous_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(directory);publisher=Mock();p=Poller(c,publisher)
            with patch.object(storage,'api',side_effect=RuntimeError('offline')),patch.object(storage,'free_mib',return_value=1000):
                p.poll(c['devices'][0])
            _,state,api_ok,adb_ok=publisher.publish.call_args.args
            self.assertEqual(state['program_title'],'')
            self.assertFalse(api_ok);self.assertTrue(adb_ok)


class RecoveryTests(unittest.TestCase):
    def test_cleanup_reclaims_space_and_retunes_exact_channel(self):
        responses=[live(),None,{'status':'stopped'},{'status':'stopped'},None,live()]
        args=SimpleNamespace(threshold=600,rearm=900,restart_fallback=True)
        with patch.object(storage,'api',side_effect=responses) as api,patch.object(storage,'free_mib',side_effect=[500,1800]),patch.object(storage,'restart_app') as restart,patch.object(storage.time,'sleep'):
            self.assertEqual(storage.recover('x','test','7.1',args),(True,1800))
            restart.assert_not_called()
            self.assertTrue(any(c.args==('x','play/channel/7.1') and c.kwargs=={'post':True} for c in api.call_args_list))

    def test_changed_channel_cancels_reset(self):
        args=SimpleNamespace(threshold=600,rearm=900,restart_fallback=True)
        with patch.object(storage,'api',return_value=live('2.1')) as api,patch.object(storage,'free_mib',return_value=500):
            self.assertEqual(storage.recover('x','test','7.1',args),(False,None))
            self.assertEqual(api.call_count,1)

    def test_missing_disk_reading_never_stops(self):
        args=SimpleNamespace(threshold=600,rearm=900,restart_fallback=True)
        with patch.object(storage,'api',return_value=live()) as api,patch.object(storage,'free_mib',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):storage.recover('x','test','7.1',args)
            self.assertEqual(api.call_count,1)


if __name__=='__main__':unittest.main()

