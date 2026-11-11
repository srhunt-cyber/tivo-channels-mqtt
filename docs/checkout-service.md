# Run an existing checkout as a service

Use this method when the bridge already works in a terminal and lives under your login user's home/code directory. It installs a system-level service that starts at boot, runs as that same login user, and keeps working after SSH disconnects.

It uses the checkout's existing `.venv`, `config.json`, state directory and the login user's existing `~/.android` ADB keys. It connects to the same local ADB server on port 5037, so previously authorized TiVos should not require another authorization. This is an alternative to the dedicated account and `/opt` installation in the main README; use one method at a time.

1. Stop the foreground bridge with **Control-C**.
2. In the checkout, run:

```bash
git pull --ff-only
.venv/bin/python deploy/install-checkout-service.py
sudo journalctl -u tivo-channels-mqtt -n 20 --no-pager
```

Run the installer as your normal login user. It requests sudo for the system service installation and startup. It refuses to proceed while the foreground bridge holds its state lock.

If your broker requires credentials, place `MQTT_USERNAME` and `MQTT_PASSWORD` in a local `mqtt.env` file in the checkout. Do not commit that file. Environment-file values follow systemd syntax (not shell expansion). A broker without credentials does not require the file.

The installer replaces `/etc/systemd/system/tivo-channels-mqtt.service`, enables startup at boot and starts/restarts the service. The rendered unit uses the actual account, checkout and state paths. It preserves the local polling interval, thresholds and recovery-enable settings. Use `--print` to inspect the generated unit without installing it.

Verify both `active (running)` and live MQTT/device/storage readings in the journal. After those appear, SSH and the client Mac can be closed. The Linux VM and its physical UTM host must remain awake and running.

Useful commands:

```bash
sudo systemctl status tivo-channels-mqtt --no-pager
sudo journalctl -u tivo-channels-mqtt -f
sudo systemctl restart tivo-channels-mqtt
sudo systemctl stop tivo-channels-mqtt
```

To update this checkout:

```bash
sudo systemctl stop tivo-channels-mqtt
git pull --ff-only
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/tivo-channels-mqtt --config config.json --check-config
sudo systemctl start tivo-channels-mqtt
```

The service depends on the checkout remaining at its installed path. If you move it, rerun the installer from the new location after updating the virtualenv. The service gives its process read-only filesystem access except for its configured state directory and `~/.android`. A short `active` status immediately after startup is not proof that the broker or devices are reachable; inspect the readings in the journal.


Run each update step only if the previous step succeeded. Keep your existing
config.json and mqtt.env; do not replace them with examples. After starting:

```bash
sudo journalctl -u tivo-channels-mqtt -n 60 --no-pager
```

For the Button Detector update, perform the [live verification](accessibility-watchdog.md#live-verification)
on Master, then check the diagnostic sensors for the other configured rooms.
