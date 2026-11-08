"""Polling service for Channels API, Android storage and optional recovery."""
import argparse
import concurrent.futures
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import threading
import time
from types import SimpleNamespace

from . import storage
from .config import load
from .commands import Commands
from .model import normalize
from .mqtt import Publisher

LOG = logging.getLogger(__name__)


class RecoveryPolicy:
    """Persist the rate limit and failure latch before any playback mutation."""
    def __init__(self, device, config, directory):
        self.settings = config
        self.path = Path(directory) / (device['id'] + '.json')
        self.low = 0
        self.last_channel = None
        self.history = []
        self.armed = True
        self.last_event = 'No recovery performed'
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data,dict) or not isinstance(data.get('history'),list):
                raise ValueError('Invalid recovery state in ' + str(self.path))
            self.history = [float(t) for t in data['history']]
            self.armed = bool(data.get('armed',False))
            self.last_event = str(data.get('last_event','Unknown previous recovery'))

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'history':self.history,'armed':self.armed,'last_event':self.last_event}))
        os.replace(temporary,self.path)

    def observe(self, status, free, now):
        self.history = [t for t in self.history if now-t < 3600]
        if free is not None and free >= self.settings['rearm_mib']:
            self.armed = True
        number = storage.channel(status)
        eligible = storage.live_playing(status) and free is not None and free < self.settings['threshold_mib']
        if not eligible:
            self.low = 0
            self.last_channel = None
            return False
        self.low = self.low + 1 if number == self.last_channel else 1
        self.last_channel = number
        if self.low < self.settings['low_readings'] or not self.armed:
            return False
        if len(self.history) >= self.settings['max_resets_per_hour']:
            return False
        if self.history and now-self.history[-1] < self.settings['cooldown_seconds']:
            return False
        return True

    def begin(self, now):
        self.armed = False
        self.low = 0
        self.history.append(now)
        self.last_event = 'Recovery started; jumps to live'
        self.save()

    def finish(self, attempted, reclaimed_free):
        if not attempted:
            self.history.pop()
            self.armed = True
            self.last_event = 'Cancelled: playback/channel/space changed'
        else:
            self.armed = reclaimed_free is not None and reclaimed_free >= self.settings['rearm_mib']
            self.last_event = ('Recovered; free %.0f MiB' % reclaimed_free if self.armed else
                               'Recovery incomplete; latched until free space recovers')
        self.save()


class Poller:
    def __init__(self, config, publisher, commands=None):
        self.commands = commands
        self.config, self.publisher = config,publisher
        self.policies = {d['id']:RecoveryPolicy(d,config['recovery'],config['state_dir']) for d in config['devices']}

    def poll(self, device):
        if self.commands is None:
            return self._poll(device)
        lock = self.commands.locks[device['id']]
        if not lock.acquire(blocking=False):
            return
        try:
            return self._poll(device)
        finally:
            lock.release()

    def _poll(self, device):
        ip, name = device['ip'],device['name']
        state, raw = {},{}
        api_ok, adb_ok, free = False,False,None
        policy = self.policies[device['id']]
        errors = []
        try:
            raw = storage.api(ip)
            state = normalize(raw)
            api_ok = state['playback'] != 'unknown'
        except Exception as error:
            errors.append('API: ' + str(error))
            state = normalize({})
        if self.config['adb_enabled']:
            try:
                try:
                    free = storage.free_mib(ip)
                except Exception:
                    storage.connect(ip)
                    free = storage.free_mib(ip)
                adb_ok = True
            except Exception as error:
                errors.append('ADB: ' + str(error))
        now = time.time()
        ready = policy.observe(raw,free,now)
        enabled = bool(self.config['recovery']['enabled'] and device.get('recovery_enabled',False))
        manual_cooldown = (self.commands is not None and time.monotonic() -
                           self.commands.last_started.get(device['id'], -1e9) <
                           self.config['recovery']['cooldown_seconds'])
        if enabled and ready and not manual_cooldown:
            policy.begin(now)
            try:
                options = SimpleNamespace(threshold=self.config['recovery']['threshold_mib'],
                                          rearm=self.config['recovery']['rearm_mib'],
                                          restart_fallback=self.config['recovery']['restart_fallback'])
                attempted, recovered = storage.recover(ip,name,storage.channel(raw),options)
                policy.finish(attempted,recovered)
            except Exception:
                policy.last_event = 'Recovery exception; latched until free space recovers'
                policy.save()
                LOG.exception('%s recovery failed',name)
            # Repoll telemetry after any possible playback change; never publish the old state.
            try:
                raw = storage.api(ip)
                state = normalize(raw)
                api_ok = state['playback'] != 'unknown'
            except Exception as error:
                state = normalize({})
                api_ok = False
                errors.append('API after recovery: ' + str(error))
            try:
                free = storage.free_mib(ip)
                adb_ok = True
            except Exception as error:
                free,adb_ok = None,False
                errors.append('ADB after recovery: ' + str(error))
        state.update(
            free_mib=round(free,1) if free is not None else None,
            low_storage=free is not None and free < self.config['recovery']['threshold_mib'],
            recovery_enabled=enabled,
            recovery_status=('Monitor only' if not enabled else
                             'Recovery latched' if not policy.armed else
                             'Rate limited' if policy.history and (
                                 len(policy.history) >= self.config['recovery']['max_resets_per_hour'] or
                                 now-policy.history[-1] < self.config['recovery']['cooldown_seconds']) else
                             'Watching'),
            last_recovery=policy.last_event, api_available=api_ok, adb_available=adb_ok,
            errors=errors)
        self.publisher.publish(device,state,api_ok,adb_ok)
        LOG.info('%s: %s | free=%s MiB | %s',name,state['display'],state['free_mib'],state['recovery_status'])
        for error in errors:
            LOG.warning('%s: %s',name,error)
        if state['low_storage']:
            LOG.warning('%s: low storage; eligibility=%s, armed=%s, recovery_enabled=%s',
                        name,storage.live_playing(raw),policy.armed,enabled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--check-config',action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config = load(args.config)
    if args.check_config:
        print('Configuration valid; %s devices; global recovery=%s' %
              (len(config['devices']),config['recovery']['enabled']))
        return
    if config['adb_enabled'] and not shutil.which('adb'):
        parser.error('adb is not in PATH; install Android platform-tools or set adb_enabled=false')
    directory = Path(config['state_dir'])
    directory.mkdir(parents=True,exist_ok=True)
    # One process per MQTT topic prefix/state directory. Service must own its state directory.
    lock = (directory / 'bridge.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another bridge is using this state directory')
    publisher = Publisher(config)
    commands = Commands(config,publisher)
    publisher.commands = commands
    poller = Poller(config,publisher,commands)
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    publisher.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(config['devices'])) as pool:
            while not stop.is_set():
                started = time.monotonic()
                publisher.announce()
                publisher.heartbeat()
                futures = [pool.submit(poller.poll,d) for d in config['devices']]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        LOG.exception('Device poll failed; sensors will expire if updates stop')
                stop.wait(max(0.1,config['poll_seconds']-(time.monotonic()-started)))
    finally:
        commands.close()
        publisher.close()
        lock.close()


if __name__ == '__main__':
    main()
