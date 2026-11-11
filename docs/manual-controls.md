# Manual maintenance and System Health dashboard

Manual control is independent of the storage watchdog. Manual playback controls remain disabled until `manual_control.enabled` is explicitly set to true.
This setting does not enable automatic storage recovery.

The bridge discovers three MQTT buttons per device, two connectivity sensors,
a last-command sensor (with progress, timestamp, before/after free space), and
bridge connection/uptime entities. Existing six sensor IDs are unchanged.

## Actions

* Restart live stream: requires a fresh playing/paused live channel, stops it,
  verifies stopped, retunes the exact channel, verifies playing. Jumps to live.
* Restart Channels app: resolves its launcher before force-stopping, starts it,
  waits for its API and restores a freshly known live channel when available.
* Reboot TiVo: resolves the launcher before reboot, verifies a changed Android
  boot ID AND boot-completed, launches Channels, restores known live playback.

No recording position or stale/previous channel is inferred. If the API was down
or no current live channel was known, app restart/reboot only verifies Channels
is reachable and explicitly reports that no live channel was restored. This can
interrupt recordings being watched, but does not delete DVR recordings or app
settings. No guarantee is made that an app restart frees disk space; measurements
before/after show the observed result. Playback verification is API-based, not
proof the television is powered on or displaying a picture.

The included dashboard uses HOLD + confirmation for all three controls; taps and
double-taps do nothing. The raw MQTT button entities can also be invoked directly
from HA, without this dashboard protection. Do not expose maintenance buttons to
voice assistants or put them on everyday remotes.

## Install from an existing checkout

Back up config.json. Pull main and reinstall the package in your existing venv.
Add `"manual_control": {"enabled": true}` at the top level of config.json, validate
with `tivo-channels-mqtt --config config.json --check-config`, restart the service.
No changes to recovery thresholds or recovery_enabled flags are required.

Use examples/system_health_tivo.yaml as a standalone dashboard or append its
cards to an existing dashboard. Room IDs in that example are kitchen/master/
florence: adapt them for your own configured device IDs. Discovery uses
`default_entity_id`; previously customized/conflicting IDs may require adjusting
references in the dashboard. The dashboard needs Mushroom cards.

Test one unoccupied room first: hold Restart live stream, confirm and watch the
result. Then test app restart, finally full reboot. Real hardware behavior and
ADB reconnection after boot must be verified on your device.

## Protocol and operation

* Topic `<prefix>/<device-id>/command`, payload exactly `restart_stream`,
  `restart_app`, or `reboot`, QoS 0, retain false. Retained deliveries are rejected.
* Only configured devices and allowlisted commands are accepted. No shell text,
  package names, or addresses can be supplied through MQTT.
* One operation per device; polling and automatic recovery share the same lock.
  Other rooms continue polling while manual maintenance is running.
* Repeated requests within 30 seconds are ignored, as are requests while busy.
  Automatic recovery observes its configured cooldown after a manual request.
* Bounded retries; exceptions report failure, never a fictitious playback success.
  A command result is persisted under state_dir and retained in MQTT. An unfinished
  operation at bridge restart is marked interrupted and is never replayed.
* During maintenance API/ADB availability is temporarily withdrawn, so old readings
  do not look current. Polling restores it after completion. Progress remains visible.
* MQTT broker access permits device control when enabled. Use the same trusted
  broker/authentication/ACL setup used for other home-control commands.
* A broker or bridge outage cannot deliver maintenance commands. A TiVo with no
  functioning ADB cannot be software-rebooted; physical power recovery is separate.


## Button Detector after maintenance

When ADB, `accessibility_watchdog`, and the device's `button_detector_enabled`
are enabled, app restarts and reboots also attempt to restore the Channels Button
Detector. Automatic storage-recovery app restarts honor the same switches.
Binding must be verified before recovery is called successful. A detector failure
is logged as a warning while live-channel restoration continues; a successful
manual playback result is not proof that accessibility recovery succeeded.
The periodic diagnostic sensor updates on its next scheduled check.
See [accessibility operation and live tests](accessibility-watchdog.md).
