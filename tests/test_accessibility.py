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


if __name__ == '__main__':
    unittest.main()
