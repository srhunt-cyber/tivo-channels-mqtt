"""ADB / Channels client API and bounded buffer recovery helpers."""
import json
import re
import subprocess
import time
import urllib.request
from urllib.parse import quote

PACKAGES = ('com.getchannels.dvr.app.beta', 'com.getchannels.dvr.app')
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(room, message):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), room + ':', message, flush=True)


def adb(ip, *args):
    result = subprocess.run(['adb', '-s', ip + ':5555', *args],
                            capture_output=True, text=True, timeout=6)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'ADB failed')
    return result.stdout.strip()


def connect(ip):
    result = subprocess.run(['adb','connect',ip + ':5555'],
                            capture_output=True, text=True, timeout=6)
    if result.returncode:
        raise RuntimeError('ADB connection failed')


def free_mib(ip):
    output = adb(ip, 'shell', 'df', '-k', '/data')
    for line in reversed(output.splitlines()):
        fields = line.split()
        if len(fields) >= 6 and fields[-1] == '/data' and fields[-3].isdigit():
            return int(fields[-3]) / 1024
    raise RuntimeError('Could not parse df -k /data; no action taken')


def api(ip, path='status', post=False):
    req = urllib.request.Request('http://' + ip + ':57000/api/' + path,
                                 data=b'' if post else None,
                                 method='POST' if post else 'GET')
    with HTTP.open(req, timeout=4) as response:
        raw = response.read()
    if post:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError('Unexpected API status response')
    return value


def channel(status):
    item = status.get('channel')
    value = item.get('number') if isinstance(item, dict) else None
    return str(value) if value is not None and str(value) else None


def live_playing(status):
    return status.get('status') == 'playing' and channel(status) is not None


def restart_app(ip, room):
    # Resolve launch activity BEFORE stopping the app. Never wipe app data.
    installed = adb(ip, 'shell', 'pm', 'list', 'packages').splitlines()
    package = next((p for p in PACKAGES if 'package:' + p in installed), None)
    if package is None:
        raise RuntimeError('Channels package not found; no app restart attempted')
    output = adb(ip, 'shell', 'cmd', 'package', 'resolve-activity', '--brief',
                 '-a', 'android.intent.action.MAIN',
                 '-c', 'android.intent.category.LEANBACK_LAUNCHER', package)
    components = [s.strip() for s in output.splitlines()
                  if re.fullmatch(re.escape(package) + r'/[A-Za-z0-9_.$]+', s.strip())]
    if not components:
        raise RuntimeError('Cannot resolve Channels launch activity; no force-stop attempted')
    log(room, 'Trying Channels app restart; settings and app data are retained')
    adb(ip, 'shell', 'am', 'force-stop', package)
    time.sleep(1)
    adb(ip, 'shell', 'am', 'start', '-n', components[-1])
    for _ in range(8):
        time.sleep(1)
        try:
            api(ip)
            return
        except Exception:
            pass
    raise RuntimeError('Channels API did not return after relaunch')


def recover(ip, room, saved_channel, args):
    """Return (attempted, restored_free_space). None free-space means failure."""
    current = api(ip)
    before = free_mib(ip)
    if not live_playing(current) or channel(current) != saved_channel or before >= args.threshold:
        log(room, 'State/channel/space changed; cancelling reset')
        return False, None
    log(room, 'Resetting live buffer on channel ' + saved_channel +
        ' at %.0f MiB free; playback will return to LIVE' % before)
    # A stop timeout is ambiguous: permit a bounded restoration attempt in finally.
    restore = True
    after = None
    success = False
    try:
        api(ip, 'stop', post=True)
        time.sleep(2)
        current = api(ip)
        if current.get('status') != 'stopped':
            restore = False
            log(room, 'Playback changed or stop was not confirmed; no restart/retune')
            return True, None
        after = free_mib(ip)
        if after < args.rearm and args.restart_fallback:
            restart_app(ip, room)
            time.sleep(2)
            after = free_mib(ip)
    except Exception as error:
        log(room, 'Recovery warning: ' + str(error))
    finally:
        if restore:
            try:
                current = api(ip)
                # Preserve a different channel or paused playback selected meanwhile.
                if current.get('status') == 'paused' or (
                    current.get('status') == 'playing' and channel(current) != saved_channel
                ):
                    log(room, 'Playback changed during recovery; leaving it alone')
                elif current.get('status') in ('stopped', 'playing'):
                    if current.get('status') != 'playing':
                        api(ip, 'play/channel/' + quote(saved_channel, safe=''), post=True)
                    for _ in range(6):
                        time.sleep(1)
                        check = api(ip)
                        if live_playing(check) and channel(check) == saved_channel:
                            success = True
                            break
                    if not success:
                        log(room, 'ALERT: could not verify restored playback; check this TiVo')
            except Exception as error:
                log(room, 'ALERT: could not restore playback: ' + str(error))
    if after is not None:
        log(room, 'Free space after cleanup: %.0f MiB (reclaimed %.0f MiB)' % (after, after-before))
    return True, after if success else None

