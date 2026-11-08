"""Bounded manual maintenance; no arbitrary commands and no retained requests."""
from datetime import datetime, timezone
import logging
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import quote

from . import storage

LOG = logging.getLogger(__name__)
ACTIONS = {
    'restart_stream': ('Restart live stream', 'mdi:play-circle-outline'),
    'restart_app': ('Restart Channels app', 'mdi:application-cog-outline'),
    'reboot': ('Reboot TiVo', 'mdi:restart'),
}


class Commands:
    def __init__(self, config, publisher):
        self.config, self.publisher = config, publisher
        self.devices = {d['id']: d for d in config['devices']}
        self.locks = {key: threading.Lock() for key in self.devices}
        self.jobs = {}
        self.last_started = {}
        self.results = {key: {'status': 'No manual command yet', 'busy': False}
                        for key in self.devices}
        self.stopping = threading.Event()
        for key in self.devices:
            path = Path(config['state_dir']) / (key + '-manual.json')
            if path.exists():
                try:
                    result = json.loads(path.read_text())
                    if not isinstance(result, dict) or not isinstance(result.get('status'), str):
                        raise ValueError('Invalid saved command status')
                    if result.get('busy'):
                        result.update(status='Interrupted by bridge restart; check TiVo', busy=False,
                                      outcome='interrupted')
                    self.results[key] = result
                except (ValueError, OSError):
                    LOG.exception('Cannot read saved manual command status for %s', key)

    def report(self, key, status, **extra):
        self.results[key] = dict(self.results[key], status=status[:255],
                                 updated_at=datetime.now(timezone.utc).isoformat(), **extra)
        path = Path(self.config['state_dir']) / (key + '-manual.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.results[key]))
        os.replace(temporary, path)
        self.publisher.command_status(self.devices[key], self.results[key])

    def submit(self, key, action, retained=False):
        if (retained or self.stopping.is_set() or key not in self.devices or
                action not in ACTIONS or not self.config.get('manual_control', {}).get('enabled', False)):
            return False
        lock = self.locks[key]
        if not lock.acquire(blocking=False):
            LOG.warning('%s command ignored: device is busy', key)
            return False
        if time.monotonic() - self.last_started.get(key, -1e9) < 30:
            lock.release()
            LOG.warning('%s command ignored: 30 second debounce', key)
            return False
        self.last_started[key] = time.monotonic()
        try:
            self.report(key, 'Starting: ' + ACTIONS[action][0], busy=True, action=action,
                        outcome='running', before_mib=None, after_mib=None, channel=None)
        except Exception:
            lock.release()
            LOG.exception('Cannot record command; no device changes made')
            return False
        thread = threading.Thread(target=self.run, args=(key, action), name='control-' + key)
        self.jobs[key] = thread
        thread.start()
        return True

    def pause(self, seconds):
        if self.stopping.wait(seconds):
            raise RuntimeError('Bridge stopping; maintenance interrupted')

    def wait_for(self, check, attempts, interval=2):
        error = 'Timed out waiting for device'
        for _ in range(attempts):
            self.pause(interval)
            try:
                if check():
                    return
            except Exception as exc:
                error = str(exc)
        raise RuntimeError(error)

    def free(self, ip):
        if not self.config['adb_enabled']:
            return None
        try:
            return round(storage.free_mib(ip), 1)
        except Exception:
            return None

    def execute(self, key, action):
        device = self.devices[key]
        ip = device['ip']
        try:
            raw = storage.api(ip)
        except Exception:
            raw = {}
        # Never infer a recording position or reuse stale channel metadata.
        number = storage.channel(raw) if raw.get('status') in ('playing', 'paused') else None
        if action == 'restart_stream' and not number:
            raise RuntimeError('No current live channel; no playback changed')
        if action != 'restart_stream' and not self.config['adb_enabled']:
            raise RuntimeError('ADB is disabled; no device changes made')
        self.report(key, 'Checking device', channel=number, before_mib=self.free(ip))
        if action == 'restart_stream':
            self.report(key, 'Stopping live stream; returning to live')
            storage.api(ip, 'stop', post=True)
            self.pause(1)
        elif action == 'restart_app':
            self.report(key, 'Restarting Channels app')
            storage.connect(ip)
            storage.restart_app(ip, device['name'])
        else:
            self.report(key, 'Preparing TiVo reboot')
            storage.connect(ip)
            # Resolve the app before rebooting, so missing packages cannot strand us.
            component = storage.launch_component(ip)
            boot_id = storage.adb(ip, 'shell', 'cat', '/proc/sys/kernel/random/boot_id')
            if not boot_id:
                raise RuntimeError('Cannot read boot identity; no reboot attempted')
            self.report(key, 'Rebooting TiVo; waiting for Android')
            storage.adb(ip, 'reboot')
            def booted():
                storage.connect(ip)
                new_id = storage.adb(ip, 'shell', 'cat', '/proc/sys/kernel/random/boot_id')
                return (bool(new_id) and new_id != boot_id and
                        storage.adb(ip, 'shell', 'getprop', 'sys.boot_completed') == '1')
            self.wait_for(booted, 45)
            self.report(key, 'Android restarted; launching Channels')
            storage.adb(ip, 'shell', 'am', 'start', '-n', component)
            self.wait_for(lambda: storage.api(ip).get('status') in ('playing', 'paused', 'stopped'), 15)
        if number:
            current = storage.api(ip)
            # Respect a different show selected using the physical remote meanwhile.
            if current.get('status') in ('playing', 'paused') and storage.channel(current) != number:
                raise RuntimeError('Playback changed during maintenance; not overriding it')
            if action == 'restart_stream' and current.get('status') != 'stopped':
                raise RuntimeError('Stop not confirmed; not overriding playback')
            self.report(key, 'Tuning live channel ' + number)
            storage.api(ip, 'play/channel/' + quote(number, safe=''), post=True)
            def playing():
                status = storage.api(ip)
                return storage.live_playing(status) and storage.channel(status) == number
            self.wait_for(playing, 12, 1)
            result = 'Verified playing channel ' + number
        else:
            # App-only maintenance is useful when the API was down. Do not claim
            # playback recovery or jump out of a recording onto an arbitrary channel.
            storage.api(ip)
            result = 'Channels reachable; no live channel restored'
        self.report(key, result, after_mib=self.free(ip), outcome='success')

    def run(self, key, action):
        try:
            self.execute(key, action)
        except Exception as exc:
            LOG.exception('%s manual %s failed', key, action)
            self.report(key, 'Failed: ' + str(exc), outcome='failed',
                        after_mib=self.free(self.devices[key]['ip']))
        finally:
            try:
                self.report(key, self.results[key]['status'], busy=False)
            finally:
                self.locks[key].release()

    def close(self):
        self.stopping.set()
        for thread in list(self.jobs.values()):
            thread.join(timeout=35)
