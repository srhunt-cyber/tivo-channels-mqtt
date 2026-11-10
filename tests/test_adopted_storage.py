import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from tivo_channels_mqtt import storage
from tivo_channels_mqtt.bridge import Poller
from tivo_channels_mqtt.mqtt import discovery
from test_bridge import config, live

VOLUME = '/mnt/expand/6399805d-9c92-45d5-972f-aef919b575b4'
ROW = '/dev/block/dm-9 119537664 1468006 116391936 2% ' + VOLUME

class AdoptedStorageTests(unittest.TestCase):
    def test_exact_volume_and_units(self):
        with patch.object(storage, 'adb', return_value=ROW):
            result = storage.adopted_stats('test', VOLUME)
        self.assertEqual(result['usb_free_gib'], 111)
        self.assertEqual(result['usb_total_gib'], 114)
        self.assertAlmostEqual(result['usb_used_percent'], 1.228, places=2)

    def test_missing_volume_and_parent_fallback_rejected(self):
        for output in ('', 'Permission denied', ROW.replace(VOLUME, '/mnt'), ROW+'\n'+ROW):
            with patch.object(storage, 'adb', return_value=output), self.assertRaises(RuntimeError):
                storage.free_mib('test', VOLUME)

    def test_path_validation(self):
        for path in ('/mnt/expand/x;reboot', '/data/../', '/storage/USB', '/mnt/expand/'):
            with self.assertRaises(ValueError):
                storage.validate_storage_path(path)

    def test_usb_selected_and_internal_still_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            c = config(directory); d = c['devices'][0]; d['storage_path'] = VOLUME
            pub = Mock(); p = Poller(c, pub)
            with patch.object(storage, 'free_mib', return_value=300), patch.object(storage, 'adb', return_value=ROW), patch.object(storage, 'api', return_value=live()):
                p.poll(d)
            state = pub.publish.call_args.args[1]
            self.assertEqual(state['internal_free_mib'], 300)
            self.assertEqual(state['free_mib'], 111*1024)
            self.assertFalse(state['low_storage'])
            self.assertEqual(state['storage_location'], 'USB')

    def test_missing_usb_does_not_trigger_recovery_or_reuse_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            c = config(directory); d = c['devices'][0]; d.update(storage_path=VOLUME, recovery_enabled=True)
            c['recovery']['enabled'] = True
            pub = Mock(); p = Poller(c, pub)
            with patch.object(storage, 'free_mib', return_value=300), patch.object(storage, 'adopted_stats', side_effect=RuntimeError('missing')), patch.object(storage, 'api', return_value=live()), patch.object(storage, 'recover') as recover:
                with patch.object(storage, 'adopted_stats', return_value={'free_mib': 113664, 'usb_free_gib': 111, 'usb_total_gib': 114, 'usb_used_percent': 2}):
                    p.poll(d)
                for _ in range(3): p.poll(d)
                recover.assert_not_called()
            state = pub.publish.call_args.args[1]
            self.assertIsNone(state['free_mib'])
            self.assertIsNone(state['usb_free_gib'])
            self.assertEqual(state['internal_free_mib'], 300)
            self.assertEqual(state['recovery_status'], 'Storage unavailable')

    def test_recovery_rechecks_same_volume(self):
        args = SimpleNamespace(threshold=450, rearm=500, restart_fallback=False, storage_path=VOLUME)
        with patch.object(storage, 'api', return_value=live()), patch.object(storage, 'free_mib', return_value=1000) as free:
            self.assertEqual(storage.recover('test', 'Kitchen', '7.1', args), (False, None))
            free.assert_called_once_with('test', VOLUME)

    def test_discovery_units_and_existing_identity(self):
        c = config('/tmp'); d = c['devices'][0]
        self.assertEqual(discovery(c, d, 'free_mib')['default_entity_id'], 'sensor.kitchen_tivo_mqtt_free_mib')
        for field, unit in [('internal_free_mib', 'MiB'), ('usb_free_gib', 'GiB'), ('usb_total_gib', 'GiB'), ('usb_used_percent', '%')]:
            self.assertEqual(discovery(c, d, field)['unit_of_measurement'], unit)
