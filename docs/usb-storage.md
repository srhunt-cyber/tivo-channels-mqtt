# Adopted USB storage

After moving Channels to an adopted USB volume, set that device's `storage_path`
to its exact `/mnt/expand/<volume-id>` mount. The default remains `/data`.
Do not select an arbitrary attached drive: confirm Channels was moved to it.
This configuration selects the monitored filesystem; it does not move the app.

Run `adb -s DEVICE_IP:5555 shell df -k` to find the adopted mount, then:

```sh
.venv/bin/python deploy/configure-storage.py --device kitchen --path /mnt/expand/VOLUME-ID
sudo systemctl restart tivo-channels-mqtt
sudo journalctl -u tivo-channels-mqtt -n 20 --no-pager
```

The helper validates connectivity and an exact mounted USB volume before saving,
backs up config.json, and preserves IP addresses and recovery settings.
Repeat for each device when its app has been moved. Use `--path /data` after
moving Channels back to internal storage. A reformatted drive gets a new volume ID.

Home Assistant discovery adds these sensors for each device:

| Entity suffix | Meaning |
| --- | --- |
| `free_mib` | Existing sensor: free space on the configured Channels volume, MiB |
| `internal_free_mib` | Internal Android storage free, MiB |
| `usb_free_gib` | Configured adopted USB volume free, GiB |
| `usb_total_gib` | Configured adopted USB volume capacity, GiB |
| `usb_used_percent` | Used blocks / total blocks, percent |
| `storage_location` | Internal, USB, or USB missing or unreadable |

Prefix: `sensor.<device-id>_tivo_mqtt_`. USB values are unknown for internal-only
devices. Free storage keeps its existing entity ID, so existing dashboard cards
continue working. Fixed-range gauges sized for internal storage need larger ranges
or replacement with an entities card. No dashboard is automatically rewritten.

Automatic recovery and manual-command before/after measurements use the configured
volume. Internal free space stays visible separately. Missing USB readings never
fall back to internal storage and do not trigger automatic recovery. Mount matching
also rejects Android returning a parent filesystem for an absent drive. Recovery
is still opt-in; this update does not enable it or change thresholds. Internal
Android storage can still run low independently; USB capacity is not combined with it.

Example compact dashboard card (new entities appear after service restart):

```yaml
type: entities
title: Kitchen TiVo storage
entities:
  - entity: sensor.kitchen_tivo_mqtt_storage_location
    name: Channels storage
  - entity: sensor.kitchen_tivo_mqtt_usb_free_gib
    name: USB free (GiB)
  - entity: sensor.kitchen_tivo_mqtt_usb_used_percent
    name: USB used
  - entity: sensor.kitchen_tivo_mqtt_internal_free_mib
    name: Internal free (MiB)
```
