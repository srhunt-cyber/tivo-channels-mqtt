import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tivo_channels_mqtt import storage
from tivo_channels_mqtt.commands import Commands
from tivo_channels_mqtt.bridge import Poller
from tivo_channels_mqtt.mqtt import Publisher, button_discovery
from test_bridge import config, live


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.temp.name)
        self.config['manual_control'] = {'enabled': True}
        self.publisher = Mock()
        self.commands = Commands(self.config, self.publisher)
        self.key = self.config['devices'][0]['id']
        self.ip = self.config['devices'][0]['ip']

    def test_rejects_retained_unknown_disabled_and_busy(self):
        c = self.commands
        with patch('threading.Thread') as thread:
            self.assertFalse(c.submit(self.key, 'reboot', retained=True))
            self.assertFalse(c.submit('bad', 'reboot'))
            self.assertFalse(c.submit(self.key, 'shell rm -rf'))
            c.config['manual_control']['enabled'] = False
            self.assertFalse(c.submit(self.key, 'reboot'))
            c.config['manual_control']['enabled'] = True
            c.locks[self.key].acquire()
            self.assertFalse(c.submit(self.key, 'reboot'))
            c.locks[self.key].release()
            thread.assert_not_called()

    def test_no_recording_or_stale_channel_reset(self):
        for raw in ({'status':'playing', 'now_playing':{'title':'Recording'}},
                    {'status':'stopped', 'channel':{'number':'7.1'}}):
            with patch.object(storage, 'api', return_value=raw) as api:
                with self.assertRaisesRegex(RuntimeError, 'No current live channel'):
                    self.commands.execute(self.key, 'restart_stream')
                self.assertEqual(api.call_count, 1)

    def test_restart_stream_retunes_decimal_and_verifies(self):
        with patch.object(storage, 'api', side_effect=[live('2.8'), None, {'status':'stopped'}, None, live('2.8')]) as api, \
                patch.object(storage, 'free_mib', side_effect=[350, 900]), patch.object(self.commands, 'pause'):
            self.commands.execute(self.key, 'restart_stream')
            api.assert_any_call(self.ip, 'play/channel/2.8', post=True)
        result = self.commands.results[self.key]
        self.assertEqual(result['status'], 'Verified playing channel 2.8')
        self.assertEqual(result['before_mib'], 350)
        self.assertEqual(result['after_mib'], 900)

    def test_changed_channel_not_overwritten(self):
        with patch.object(storage, 'api', side_effect=[live(), None, live('32.1')]) as api, \
                patch.object(storage, 'free_mib', return_value=350), patch.object(self.commands, 'pause'):
            with self.assertRaisesRegex(RuntimeError, 'Playback changed'):
                self.commands.execute(self.key, 'restart_stream')
            self.assertFalse(any('play/channel/' in str(c) for c in api.call_args_list))

    def test_api_down_app_restart_does_not_guess_channel(self):
        with patch.object(storage, 'api', side_effect=[RuntimeError('down'), {'status':'stopped'}]) as api, \
                patch.object(storage, 'free_mib', return_value=500), patch.object(storage, 'connect'), \
                patch.object(storage, 'restart_app') as restart:
            self.commands.execute(self.key, 'restart_app')
            restart.assert_called_once()
            self.assertFalse(any(c.kwargs.get('post') for c in api.call_args_list))
        self.assertIn('no live channel restored', self.commands.results[self.key]['status'])

    def test_app_restart_passes_global_and_device_opt_out(self):
        for global_on, device_on in [(True, True), (False, True), (True, False)]:
            self.config['accessibility_watchdog'] = global_on
            self.config['devices'][0]['button_detector_enabled'] = device_on
            with patch.object(storage, 'api', return_value={'status':'stopped'}), \
                    patch.object(storage, 'free_mib', return_value=500), patch.object(storage, 'connect'), \
                    patch.object(storage, 'restart_app') as restart:
                self.commands.execute(self.key, 'restart_app')
                restart.assert_called_once_with(self.ip, self.config['devices'][0]['name'],
                                                restore_button_detector=global_on and device_on)

    def test_reboot_waits_for_new_boot_then_restores(self):
        self.config['accessibility_watchdog'] = True
        with patch.object(storage, 'api', side_effect=[live(), {'status':'stopped'}, {'status':'stopped'}, None, live()]) as api, \
                patch.object(storage, 'free_mib', return_value=500), patch.object(storage, 'connect'), \
                patch.object(storage, 'launch_component', return_value='com.getchannels.dvr.app/.Main'), \
                patch.object(storage, 'adb', side_effect=['old', '', 'old', 'new', '1', '']) as adb, \
                patch.object(self.commands, 'pause'), \
                patch.object(storage, 'restore_button_detector_after_restart') as detector:
            self.commands.execute(self.key, 'reboot')
            detector.assert_called_once_with(self.ip, self.config['devices'][0]['name'])
            adb.assert_any_call(self.ip, 'reboot')
            api.assert_any_call(self.ip, 'play/channel/7.1', post=True)
            self.assertEqual(sum('boot_id' in str(c) for c in adb.call_args_list), 3)

    def test_failure_report_releases_lock_and_persists(self):
        c = self.commands
        c.locks[self.key].acquire()
        with patch.object(c, 'execute', side_effect=RuntimeError('test failure')), patch.object(c, 'free', return_value=400):
            c.run(self.key, 'reboot')
        self.assertFalse(c.locks[self.key].locked())
        loaded = Commands(self.config, self.publisher)
        self.assertEqual(loaded.results[self.key]['outcome'], 'failed')
        self.assertFalse(loaded.results[self.key]['busy'])

    def test_interrupted_job_not_replayed(self):
        self.commands.report(self.key, 'Rebooting', busy=True)
        c = Commands(self.config, self.publisher)
        self.assertIn('Interrupted', c.results[self.key]['status'])
        self.assertFalse(c.results[self.key]['busy'])

    def test_debounce_blocks_second_request(self):
        with patch('threading.Thread'):
            self.assertTrue(self.commands.submit(self.key, 'restart_app'))
            self.commands.locks[self.key].release()
            self.assertFalse(self.commands.submit(self.key, 'restart_app'))

    def test_poller_skips_device_under_maintenance(self):
        p = Poller(self.config, self.publisher, self.commands)
        self.commands.locks[self.key].acquire()
        with patch.object(storage, 'api') as api:
            p.poll(self.config['devices'][0])
            api.assert_not_called()
        self.commands.locks[self.key].release()

    def test_discovery_nonretained_and_separate_health(self):
        d = self.config['devices'][0]
        stream = button_discovery(self.config, d, 'restart_stream')
        reboot = button_discovery(self.config, d, 'reboot')
        self.assertFalse(reboot['retain'])
        self.assertEqual(reboot['qos'], 0)
        self.assertTrue(reboot['availability'][-1]['topic'].endswith('/adb_availability'))
        self.assertTrue(stream['availability'][-1]['topic'].endswith('/api_availability'))

    def test_message_routing_rejects_unrecognized_topics(self):
        p = Publisher(self.config)
        p.commands = Mock()
        p.on_message(None, None, SimpleNamespace(topic='other/command', payload=b'reboot', retain=False))
        p.commands.submit.assert_not_called()
        p.on_message(None, None, SimpleNamespace(topic=p.prefix + '/' + self.key + '/command', payload=b'reboot', retain=True))
        p.commands.submit.assert_called_once_with(self.key, 'reboot', retained=True)

    def test_reboot_resolution_failure_does_not_reboot(self):
        with patch.object(storage, 'api', return_value=live()), patch.object(storage, 'free_mib', return_value=500), patch.object(storage, 'connect'), patch.object(storage, 'launch_component', side_effect=RuntimeError('missing app')), patch.object(storage, 'adb') as adb:
            with self.assertRaisesRegex(RuntimeError, 'missing app'):
                self.commands.execute(self.key, 'reboot')
            adb.assert_not_called()

    def test_playback_verification_timeout_is_failure(self):
        with patch.object(storage, 'api', side_effect=[live(), None, {'status':'stopped'}, None] + [{'status':'stopped'}]*12), patch.object(storage, 'free_mib', return_value=500), patch.object(self.commands, 'pause'):
            with self.assertRaises(RuntimeError):
                self.commands.execute(self.key, 'restart_stream')
            self.assertNotEqual(self.commands.results[self.key].get('outcome'), 'success')

    def test_same_boot_id_never_launches_app(self):
        def adb(ip, *args):
            if args == ('reboot',):
                return ''
            return 'old'
        with patch.object(storage, 'api', return_value=live()), patch.object(storage, 'free_mib', return_value=500), patch.object(storage, 'connect'), patch.object(storage, 'launch_component', return_value='package/.Main'), patch.object(storage, 'adb', side_effect=adb) as calls, patch.object(self.commands, 'pause'):
            with self.assertRaises(RuntimeError):
                self.commands.execute(self.key, 'reboot')
            self.assertFalse(any('start' in c.args for c in calls.call_args_list))

    def test_another_room_still_polls(self):
        poller = Poller(self.config, self.publisher, self.commands)
        self.commands.locks[self.key].acquire()
        try:
            with patch.object(storage, 'api', return_value=live()), patch.object(storage, 'free_mib', return_value=900):
                poller.poll(self.config['devices'][1])
                self.publisher.publish.assert_called_once()
        finally:
            self.commands.locks[self.key].release()

if __name__ == '__main__':
    unittest.main()

