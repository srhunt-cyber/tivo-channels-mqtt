# TiVo Channels MQTT

A small service for an always-on Linux host or VM. It supplements Home Assistant's Android media-player entities with information from Channels itself, and monitors the TiVo Stream 4K's limited internal storage.

**Monitoring is enabled; playback recovery is disabled by default.** The project has automated tests but has not been run against physical TiVos or a live broker yet.

## What it provides

- Channels playback state, channel number/name, program title and episode title.
- Compact now-playing text such as `▶ ABC 7.1 · Program title`.
- Free `/data` storage in MiB, obtained over authorized ADB.
- Home Assistant MQTT discovery with separate availability for playback and ADB/storage.
- An optional low-storage watchdog that stops/reopens the live channel, with app restart as an optional fallback.
- A dedicated systemd service that can run alongside other bridges.

These are **streaming-player** states. They do not establish whether the television panel is on; the dashboard continues to use each TV's own power entity.

## Requirements

- Linux with systemd; Python 3.9+ and Android platform-tools (`adb`).
- Network access from the VM to the MQTT broker and each TiVo on TCP 57000 (Channels API) and 5555 (the existing authorized network ADB setup).
- Channels launched at least once; the API may be available in the background on Android.
- Home Assistant connected to the same MQTT broker, with discovery enabled.

VM networking must permit LAN access. The example uses non-routable documentation addresses (192.0.2.x); replace them with your device addresses before starting.

## Already running from a home-directory checkout?

Use the [existing-checkout service installer](docs/checkout-service.md) to keep the current user, config and authorized ADB keys. It starts at boot and keeps running after SSH disconnects.

## Install on Debian/Ubuntu

These commands create a separate service account and directory. Choose another path/account if `/opt/tivo-channels-mqtt` already exists. The committed examples use placeholder addresses and entities. Keep your real configuration and credentials outside the checkout; no MQTT passwords are included.

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv adb
sudo useradd --system --home-dir /var/lib/tivo-channels-mqtt --shell /usr/sbin/nologin tivo-channels
sudo git clone https://github.com/srhunt-cyber/tivo-channels-mqtt.git /opt/tivo-channels-mqtt
cd /opt/tivo-channels-mqtt
sudo python3 -m venv .venv
sudo .venv/bin/pip install .
sudo install -d -m 0755 /etc/tivo-channels-mqtt
sudo install -d -m 0700 -o tivo-channels -g tivo-channels /var/lib/tivo-channels-mqtt
sudo install -m 0640 -o root -g tivo-channels config.example.json /etc/tivo-channels-mqtt/config.json
sudo install -m 0600 deploy/mqtt.env.example /etc/tivo-channels-mqtt/mqtt.env
sudoedit /etc/tivo-channels-mqtt/config.json
sudoedit /etc/tivo-channels-mqtt/mqtt.env
```

The public repository can be cloned without GitHub authentication.

In `config.json`, set the broker host and each TiVo address, then set:

```json
"state_dir": "/var/lib/tivo-channels-mqtt"
```

Put your broker username/password only in `mqtt.env`, using the environment variable names in the example. `mqtt.tls=true` enables verified TLS using system trust or optional `mqtt.ca_certs`. Keep TLS enabled if your broker requires it; the default port 1883 is for a trusted LAN broker without TLS.

Check configuration (this does not connect to the TiVos or broker):

```bash
sudo .venv/bin/tivo-channels-mqtt --config /etc/tivo-channels-mqtt/config.json --check-config
```

Authorize ADB **as the service account**, so its keys survive reboots. The example service uses a dedicated ADB server on port 5038 to avoid sharing a server with another bridge/user:

```bash
sudo -u tivo-channels env HOME=/var/lib/tivo-channels-mqtt ADB_SERVER_SOCKET=tcp:127.0.0.1:5038 adb connect 192.0.2.101:5555
sudo -u tivo-channels env HOME=/var/lib/tivo-channels-mqtt ADB_SERVER_SOCKET=tcp:127.0.0.1:5038 adb connect 192.0.2.102:5555
sudo -u tivo-channels env HOME=/var/lib/tivo-channels-mqtt ADB_SERVER_SOCKET=tcp:127.0.0.1:5038 adb connect 192.0.2.103:5555
```

Accept the debugging authorization on each TiVo when prompted. This step requires the existing network-debugging setup; do not expose ADB to the Internet. If storage monitoring is not needed yet, set `adb_enabled=false` and leave recovery disabled; Channels/MQTT reporting works without ADB.

Install and start the service:

```bash
sudo install -m 0644 deploy/tivo-channels-mqtt.service /etc/systemd/system/tivo-channels-mqtt.service
sudo systemctl daemon-reload
sudo systemctl enable --now tivo-channels-mqtt
sudo journalctl -u tivo-channels-mqtt -f
```

Expected MQTT-discovered sensor IDs (Home Assistant can append a suffix if a name already exists):

| Room | Playback and attributes | Compact text | Free space |
|---|---|---|---|
| Kitchen | `sensor.kitchen_tivo_mqtt_playback` | `sensor.kitchen_tivo_mqtt_display` | `sensor.kitchen_tivo_mqtt_free_mib` |
| Master | `sensor.master_tivo_mqtt_playback` | `sensor.master_tivo_mqtt_display` | `sensor.master_tivo_mqtt_free_mib` |
| Guest Bedroom | `sensor.guest_bedroom_tivo_mqtt_playback` | `sensor.guest_bedroom_tivo_mqtt_display` | `sensor.guest_bedroom_tivo_mqtt_free_mib` |

Other sensors expose the channel, program title and watchdog status. The playback sensor's attributes include all telemetry, `low_storage`, `last_recovery` and errors. ADB failure marks only storage unavailable; an API failure marks playback-related entities unavailable. A bridge last-will and sensor expiry protect against stale displays.

## Dashboard

`examples/whole_house_tv_mqtt.yaml` is a compact standalone dashboard example for one room. Replace its placeholder television and media-player entities with yours, and verify the discovered MQTT entity IDs. The status card combines the actual TV power state with Channels playback/channel/title. Play and Pause buttons color from the MQTT playback state and send commands through your existing Android media-player entity. Duplicate the room configuration for additional devices.

Do not replace a customized existing dashboard wholesale with this generic example. Copy its status/transport cards into your existing dashboard, or merge the MQTT metadata into your current templates.

## Enable storage recovery after observing telemetry

Start with Kitchen only. Change global `recovery.enabled` to `true` and Kitchen's `recovery_enabled` to `true`; leave other rooms disabled until the Kitchen test is satisfactory. Set `recovery.restart_fallback=true` if you want an app restart when stopping alone does not free sufficient space. Restart the service after changing configuration.

Defaults:

| Setting | Value |
|---|---:|
| Poll interval | 15 seconds, when checks complete in time |
| Trigger | Below 600 MiB on two consecutive readings of the same live channel |
| Rearm / successful recovery target | At least 900 MiB free |
| Cooldown | 5 minutes |
| Maximum resets | 3 per rolling hour per device |

The reported Android warning near 330 MB is an observation to verify, not a documented guarantee. These thresholds are provisional and configurable. `/data` free space is total device storage; this does not prove which app consumed it.

Recovery remembers the live channel, rechecks status/free space, stops playback, measures storage reclaimed, optionally force-stops and relaunches Channels, and retunes/checks the saved channel. It never clears application data or reboots the TiVo. Recovery history and its failure latch are saved before changing playback and survive service restarts.

**The picture briefly stops and returns to LIVE. Rewind history and any time-shifted viewing position are lost.** Paused streams and recordings are intentionally excluded. Existing HA pause-guard logic can remain, but do not run the older standalone storage guard alongside this service. Recovery may act if Channels is playing while the TV screen is off; no TV power is queried, and retuning can have device-specific CEC effects.

If stopping/restarting fails to reclaim space, further resets latch off until storage recovers. It logs the condition; it cannot guarantee recovery if ADB/the API is unreachable, another app fills storage, or a warning has already blocked the interface. Terminal/journal warnings are not push notifications.

## MQTT topics

Under `tivo_channels`:

- `availability`: retained bridge `online`/`offline` (MQTT last will).
- `<room>/state`: non-retained JSON telemetry.
- `<room>/api_availability` and `<room>/adb_availability`: retained per-source availability.

Discovery is retained under `homeassistant/sensor/tivo_channels_<room>/.../config`. Discovery is republished on broker reconnect and Home Assistant's `homeassistant/status=online` birth message. When manual_control.enabled is true, the bridge also subscribes to allowlisted per-device command topics. Broker ACLs must permit discovery/state publishing, the Home Assistant birth-topic subscription, and those command subscriptions. Manual controls default to disabled.

## Update from GitHub

Stop before updating, install dependencies/code in the existing virtual environment, then restart:

```bash
sudo systemctl stop tivo-channels-mqtt
cd /opt/tivo-channels-mqtt
sudo git pull --ff-only
sudo .venv/bin/pip install .
sudo systemctl start tivo-channels-mqtt
sudo journalctl -u tivo-channels-mqtt -n 30 --no-pager
```

Local configuration and MQTT credentials live outside the checkout. After upgrading the service unit itself, reinstall it and run `systemctl daemon-reload` before restarting.

## Development / checks

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Tests simulate Channels/ADB/MQTT; they do not replace a long live-session trial with your devices and broker. See `docs/storage-notes.md` for sizing calculations and source links.

## Manual maintenance controls

Optional MQTT buttons now support restarting a live stream, restarting the Channels
app, and rebooting a TiVo, with verified results and per-device progress. Set
`manual_control.enabled` to `true` to opt in; automatic recovery remains unchanged.
See [manual controls](docs/manual-controls.md) and the hold-to-confirm
[System Health example](examples/system_health_tivo.yaml).
