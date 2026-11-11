# Channels Button Detector watchdog

Part of **Channels Android MQTT**. Designed for Android TV devices running Channels;
the live reference device is TiVo Stream 4K. Accessibility behavior and permissions
on other Android devices have not been verified. Existing `tivo` entity IDs are
retained for compatibility; see the [project naming notes](../README.md#project-name-and-compatibility).

The watchdog uses each device's existing `ip` and `id` in config.json. It covers
all configured Android TV devices without hard-coded room addresses. It is independent of
storage recovery and manual playback controls. No storage threshold is changed.

## What it checks and changes

With `adb_enabled`, `accessibility_watchdog`, and per-device
`button_detector_enabled` enabled (all default true), each device is checked on
its first successful internal-storage ADB poll, then every
`accessibility_check_seconds` (default 300; minimum 15). Failed attempts also wait
that interval, preventing repeated writes every playback poll. Unreachable ADB
reports unavailable; successful storage access is required before this check.

Health requires all four conditions:

1. Android accessibility is enabled.
2. A Channels detector component is in enabled_accessibility_services.
3. It appears specifically in the dump's Bound services section.
4. It is absent from Crashed services.

If unhealthy, the installed Channels package is resolved (beta preferred if both
are installed). If neither is installed, no settings are written. Other enabled
accessibility services are preserved. A listed but unbound/crashed detector is
removed and re-added to request a rebind; a missing detector is added. The global
accessibility switch is enabled. There is no app force-stop, playback command,
data clearing or reboot in this watchdog operation.

Binding is asynchronous: after writing, it reads health up to four times, waiting
one second between unsuccessful reads. Each ADB command has a six-second timeout.
It reports `Restored` only on verified health. It reports `Error` and logs the
reason when verification fails. This confirms Android service state, not physical
remote behavior or that the TV panel is on.

## Sensor and options

`sensor.<device-id>_tivo_mqtt_button_detector` reports `Enabled`, `Restored`,
`Disabled`, `Error`, or `Unavailable`. It retains the last check result between
checks; `Restored` normally becomes `Enabled` at the next healthy check.
ADB/bridge availability and sensor expiry prevent offline devices looking fresh.

Set global `accessibility_watchdog: false` or a device's
`button_detector_enabled: false` to stop automatic checks/restoration. Use JSON
booleans, not quoted strings. Restart the bridge after config edits. These options
also apply to app restart/reboot hooks and automatic storage-recovery restarts.
They do not turn off an already-enabled Android accessibility service.

Post-maintenance restoration failure is logged without preventing live retuning.
Manual-command success refers to playback/API verification; check the journal
for accessibility warnings and the next periodic sensor reading separately.

## Live verification

After updating the existing checkout and restarting the bridge:

1. Confirm the service and MQTT connection in the journal. Each of your
   configured devices should show an Enabled or Restored detector sensor after
   its first successful ADB poll. Existing HA entity naming conflicts can add a suffix.
2. In an unoccupied room (Master in the original TiVo test installation), turn off only Channels
   Button Detector in Android Accessibility. Leave the bridge running.
3. Wait one configured check interval plus poll/ADB time. Confirm the journal
   records a verified restoration and the sensor becomes Restored.
4. Run `adb -s DEVICE_IP:5555 shell dumpsys accessibility`. Confirm Channels Button
   Detector is in Bound services and not Crashed services. Test the real Guide
   button, then confirm the next check reports Enabled.
5. Check the remaining devices' sensors (Kitchen and Florence in the original
   installation), and repeat the physical-button test as
   convenient. Use the current IPs in local config, not old remembered addresses.

If it reports Error, inspect the journal and dump rather than repeatedly
restarting playback. Do not infer that the cause of Android disabling the service
has been identified merely because restoration works.

## Automated tests

Run `.venv/bin/python -m unittest discover -s tests -v` from the repo root.
Accessibility tests simulate ADB and cover missing settings, preserving another
service, delayed binding, persistent unbound/crashed failure, labels outside the
bound section, missing packages, all three device polls, failed-attempt spacing,
and opt-outs. Existing playback, storage, MQTT and maintenance regressions remain
in the same suite. Tests do not contact live devices or a broker.
