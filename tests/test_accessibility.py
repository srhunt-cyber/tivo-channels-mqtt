import unittest
from unittest.mock import patch

from tivo_channels_mqtt import storage

HEALTHY_DUMPSYS = """ACCESSIBILITY MANAGER (dumpsys accessibility)
User state[
     Bound services:{Service[label=Channels Button Detector, feedbackType[], capabilities=8]}
     Enabled services:{{com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService}}
     Crashed services:{}
     Client list info:{}
]"""

UNBOUND_DUMPSYS = """ACCESSIBILITY MANAGER (dumpsys accessibility)
User state[
     Bound services:{}
     Enabled services:{{com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService}}
     Crashed services:{}
     Client list info:{}
]"""


class AccessibilityWatchdogTests(unittest.TestCase):
    def test_already_healthy_makes_no_writes(self):
        calls = []

        def fake_adb(ip, *args):
            calls.append(args)
            if args == ('shell', 'settings', 'get', 'secure', 'accessibility_enabled'):
                return '1'
            if args == ('shell', 'settings', 'get', 'secure', 'enabled_accessibility_services'):
                return 'com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService'
            if args == ('shell', 'dumpsys', 'accessibility'):
                return HEALTHY_DUMPSYS
            raise AssertionError('Unexpected ADB call: ' + repr(args))

        with patch.object(storage, 'adb', side_effect=fake_adb):
            result = storage.ensure_button_detector('192.0.2.10')

        self.assertEqual(result, 'Enabled')
        self.assertEqual(len(calls), 3)

    def test_restores_when_missing_and_preserves_other_services(self):
        state = {
            'enabled': '0',
            'services': 'com.google.android.marvin.talkback/.TalkBackService',
            'dumpsys': UNBOUND_DUMPSYS,
        }
        puts = []

        def fake_adb(ip, *args):
            if args == ('shell', 'pm', 'list', 'packages'):
                return 'package:com.getchannels.dvr.app.beta\n'
            if args == ('shell', 'settings', 'get', 'secure', 'accessibility_enabled'):
                return state['enabled']
            if args == ('shell', 'settings', 'get', 'secure', 'enabled_accessibility_services'):
                return state['services']
            if args == ('shell', 'dumpsys', 'accessibility'):
                return state['dumpsys']
            if args[:4] == ('shell', 'settings', 'put', 'secure'):
                puts.append(args[4:])
                if args[4] == 'enabled_accessibility_services':
                    state['services'] = args[5]
                    state['dumpsys'] = HEALTHY_DUMPSYS
                elif args[4] == 'accessibility_enabled':
                    state['enabled'] = args[5]
                return ''
            raise AssertionError('Unexpected ADB call: ' + repr(args))

        with patch.object(storage, 'adb', side_effect=fake_adb):
            result = storage.ensure_button_detector('192.0.2.10')

        self.assertEqual(result, 'Restored')
        self.assertIn(
            (
                'enabled_accessibility_services',
                'com.google.android.marvin.talkback/.TalkBackService:'
                'com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService',
            ),
            puts,
        )
        self.assertIn(('accessibility_enabled', '1'), puts)

    def test_rebinds_when_in_settings_but_unbound(self):
        state = {
            'enabled': '1',
            'services': 'com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService',
            'dumpsys': UNBOUND_DUMPSYS,
        }
        actions = []

        def fake_adb(ip, *args):
            if args == ('shell', 'pm', 'list', 'packages'):
                return 'package:com.getchannels.dvr.app.beta\n'
            if args == ('shell', 'settings', 'get', 'secure', 'accessibility_enabled'):
                return state['enabled']
            if args == ('shell', 'settings', 'get', 'secure', 'enabled_accessibility_services'):
                return state['services']
            if args == ('shell', 'dumpsys', 'accessibility'):
                return state['dumpsys']
            if args[:4] == ('shell', 'settings', 'delete', 'secure'):
                actions.append(('delete', args[4]))
                state['services'] = 'null'
                return ''
            if args[:4] == ('shell', 'settings', 'put', 'secure'):
                actions.append(('put', args[4], args[5]))
                if args[4] == 'enabled_accessibility_services':
                    state['services'] = args[5]
                    state['dumpsys'] = HEALTHY_DUMPSYS
                return ''
            raise AssertionError('Unexpected ADB call: ' + repr(args))

        with patch.object(storage, 'adb', side_effect=fake_adb), patch('time.sleep'):
            result = storage.ensure_button_detector('192.0.2.10')

        self.assertEqual(result, 'Restored')
        self.assertEqual(actions[0], ('delete', 'enabled_accessibility_services'))
        self.assertIn(
            (
                'put',
                'enabled_accessibility_services',
                'com.getchannels.dvr.app.beta/com.getchannels.android.RemoteButtonAccessibilityService',
            ),
            actions,
        )



class AccessibilityRegressionTests(unittest.TestCase):
    def status(self, **changes):
        value = dict(healthy=False, accessibility_enabled=True, in_settings=True,
                     bound=False, crashed=False, services=[storage.A11Y_SERVICES[0]])
        value.update(changes)
        return value

    def test_label_outside_bound_section_is_not_running(self):
        dump = UNBOUND_DUMPSYS + '\nInstalled: Service[label=Channels Button Detector]'
        with patch.object(storage, 'adb', side_effect=['1', storage.A11Y_SERVICES[0], dump]):
            self.assertFalse(storage.check_button_detector('192.0.2.10')['healthy'])

    def test_nested_component_braces_and_crashed_section(self):
        dump = ('Bound services:{{' + storage.A11Y_SERVICES[0] + '}}\n'
                'Enabled services:{}\nCrashed services:{{' + storage.A11Y_SERVICES[0] + '}}')
        with patch.object(storage, 'adb', side_effect=['1', storage.A11Y_SERVICES[0], dump]):
            result = storage.check_button_detector('192.0.2.10')
        self.assertTrue(result['bound'])
        self.assertTrue(result['crashed'])
        self.assertFalse(result['healthy'])

    def test_restore_waits_for_binding(self):
        with patch.object(storage, 'check_button_detector', side_effect=[self.status(), self.status(), self.status(healthy=True, bound=True)]) as check, \
                patch.object(storage, 'button_detector_component', return_value=storage.A11Y_SERVICES[0]), \
                patch.object(storage, 'adb'), patch.object(storage.time, 'sleep'):
            self.assertEqual(storage.ensure_button_detector('192.0.2.10'), 'Restored')
            self.assertEqual(check.call_count, 3)

    def test_persistent_unbound_or_crashed_never_reports_restored(self):
        for crashed in (False, True):
            with self.subTest(crashed=crashed), patch.object(storage, 'check_button_detector', return_value=self.status(crashed=crashed)), \
                    patch.object(storage, 'button_detector_component', return_value=storage.A11Y_SERVICES[0]), \
                    patch.object(storage, 'adb'), patch.object(storage.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'not healthy'):
                    storage.ensure_button_detector('192.0.2.10')

    def test_missing_package_does_not_write_settings(self):
        with patch.object(storage, 'check_button_detector', return_value=self.status()), \
                patch.object(storage, 'adb', return_value='package:com.netflix.ninja') as adb:
            with self.assertRaisesRegex(RuntimeError, 'not installed'):
                storage.ensure_button_detector('192.0.2.10')
            adb.assert_called_once_with('192.0.2.10', 'shell', 'pm', 'list', 'packages')

    def test_restart_respects_disable_and_logs_failure(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(storage, 'launch_component', return_value='pkg/.Main'), \
                    patch.object(storage, 'adb'), patch.object(storage, 'api', return_value={}), \
                    patch.object(storage.time, 'sleep'), \
                    patch.object(storage, 'ensure_button_detector', side_effect=RuntimeError('still crashed')) as ensure:
                if enabled:
                    with self.assertLogs('tivo_channels_mqtt.storage', level='WARNING') as logs:
                        storage.restart_app('192.0.2.10', 'Master', restore_button_detector=True)
                    self.assertIn('still crashed', logs.output[0])
                    ensure.assert_called_once()
                else:
                    storage.restart_app('192.0.2.10', 'Master', restore_button_detector=False)
                    ensure.assert_not_called()


class AccessibilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from test_bridge import config
        from tivo_channels_mqtt.bridge import Poller
        from unittest.mock import Mock
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.temp.name)
        self.config['accessibility_watchdog'] = True
        self.publisher = Mock()
        self.poller = Poller(self.config, self.publisher)
        # Any accidental real ADB call makes the test fail, never contacts a device.
        self.adb = patch.object(storage, 'adb', side_effect=AssertionError('Unmocked ADB')).start()
        self.addCleanup(patch.stopall)

    def test_all_three_devices_checked_and_interval_respected_on_errors(self):
        with patch.object(storage, 'api', return_value={'status':'stopped'}), \
                patch.object(storage, 'free_mib', return_value=1000), \
                patch.object(storage, 'ensure_button_detector', side_effect=RuntimeError('not bound')) as ensure, \
                patch('tivo_channels_mqtt.bridge.time.monotonic', return_value=1000):
            for _ in range(2):
                for device in self.config['devices']:
                    self.poller.poll(device)
            self.assertEqual(ensure.call_count, 3)
            self.assertEqual({c.args[0] for c in ensure.call_args_list}, {d['ip'] for d in self.config['devices']})
            self.assertEqual(self.publisher.publish.call_args.args[1]['button_detector'], 'Error')
            with patch('tivo_channels_mqtt.bridge.time.monotonic', return_value=1301):
                self.poller.poll(self.config['devices'][0])
            self.assertEqual(ensure.call_count, 4)

    def test_global_and_device_opt_out(self):
        for global_on, device_on in [(False, True), (True, False)]:
            self.config['accessibility_watchdog'] = global_on
            device = self.config['devices'][0]
            device['button_detector_enabled'] = device_on
            with patch.object(storage, 'api', return_value={'status':'stopped'}), \
                    patch.object(storage, 'free_mib', return_value=1000), \
                    patch.object(storage, 'ensure_button_detector') as ensure:
                self.poller.poll(device)
                ensure.assert_not_called()
                self.assertEqual(self.publisher.publish.call_args.args[1]['button_detector'], 'Disabled')


class RecoveryOptOutTests(unittest.TestCase):
    def test_storage_restart_forwards_disabled_accessibility(self):
        from types import SimpleNamespace
        from test_bridge import live
        args = SimpleNamespace(threshold=600, rearm=900, restart_fallback=True,
                               restore_button_detector=False)
        with patch.object(storage, 'api', side_effect=[live(), None, {'status':'stopped'},
                                                      {'status':'stopped'}, None, live()]), \
                patch.object(storage, 'free_mib', side_effect=[500, 500, 1000]), \
                patch.object(storage, 'restart_app') as restart, patch.object(storage.time, 'sleep'):
            self.assertEqual(storage.recover('192.0.2.10', 'Master', '7.1', args), (True, 1000))
            restart.assert_called_once_with('192.0.2.10', 'Master', restore_button_detector=False)


if __name__ == '__main__':
    unittest.main()
