"""Local JSON configuration. Credentials remain in the host environment."""
import ipaddress
import json
import math
import re
from pathlib import Path


def positive(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(name + ' must be a finite number >= ' + str(minimum))


def load(path):
    config = json.loads(Path(path).read_text())
    mqtt = config['mqtt']
    if not isinstance(mqtt.get('host'),str) or not mqtt['host'] or mqtt['host'] == 'YOUR_MQTT_BROKER':
        raise ValueError('Set mqtt.host in your local config')
    for field in ('topic_prefix','discovery_prefix','client_id'):
        if not re.fullmatch(r'[a-zA-Z0-9_/-]+',mqtt[field]) or mqtt[field].startswith('/') or mqtt[field].endswith('/'):
            raise ValueError('Invalid mqtt.' + field)
    port = mqtt.get('port',1883)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid MQTT port')
    config.setdefault('poll_seconds',15)
    positive(config['poll_seconds'],'poll_seconds')
    config.setdefault('adb_enabled',True)
    manual = config.setdefault('manual_control', {'enabled': False})
    if not isinstance(manual, dict) or type(manual.get('enabled', False)) is not bool:
        raise ValueError('manual_control.enabled must be true or false')
    recovery = config['recovery']
    for name in ('threshold_mib','rearm_mib','low_readings','cooldown_seconds','max_resets_per_hour'):
        positive(recovery[name],'recovery.' + name)
    if recovery['rearm_mib'] <= recovery['threshold_mib']:
        raise ValueError('rearm_mib must exceed threshold_mib')
    if type(recovery['low_readings']) is not int or type(recovery['max_resets_per_hour']) is not int:
        raise ValueError('low_readings and max_resets_per_hour must be integers')
    if recovery.get('enabled') and not config['adb_enabled']:
        raise ValueError('Recovery requires adb_enabled')
    for section, field in ((config,'adb_enabled'),(recovery,'enabled'),(recovery,'restart_fallback'),(mqtt,'tls')):
        if type(section.get(field,False)) is not bool:
            raise ValueError(field + ' must be true or false')
    ids, ips = set(), set()
    if not config.get('devices'):
        raise ValueError('Configure at least one device')
    for device in config['devices']:
        if not re.fullmatch(r'[a-z][a-z0-9_]*',device['id']):
            raise ValueError('Device ids must be lowercase letters/numbers/underscores')
        ipaddress.IPv4Address(device['ip'])
        if device['id'] in ids or device['ip'] in ips:
            raise ValueError('Duplicate device id or address')
        ids.add(device['id']);ips.add(device['ip'])
        if not isinstance(device['name'],str) or not device['name']:
            raise ValueError('Device name required')
        if type(device.get('recovery_enabled',False)) is not bool:
            raise ValueError('device.recovery_enabled must be true or false')
    # Resolve relative state storage against the configuration directory, not cwd.
    directory = Path(config.get('state_dir','state')).expanduser()
    config['state_dir'] = str(directory if directory.is_absolute() else Path(path).resolve().parent / directory)
    return config
