#!/usr/bin/env python3
"""Select a verified Channels storage volume without changing recovery or IPs."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tivo_channels_mqtt.config import load
from tivo_channels_mqtt import storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--device', required=True)
    parser.add_argument('--path', required=True)
    args = parser.parse_args()
    path = Path(args.config)
    config = load(path)
    device = next((d for d in config['devices'] if d['id'] == args.device), None)
    if device is None:
        parser.error('Unknown device ID')
    storage.validate_storage_path(args.path)
    storage.connect(device['ip'])
    free = storage.free_mib(device['ip'], args.path)
    original = path.read_text()
    updated = json.loads(original)
    for d in updated['devices']:
        if d['id'] == args.device:
            d['storage_path'] = args.path
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = path.with_name(path.name + '.storage-backup-' + stamp)
    with backup.open('x') as file:
        file.write(original)
    temporary = path.with_name(path.name + '.storage-tmp')
    temporary.write_text(json.dumps(updated, indent=2) + '\n')
    temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)
    print(f"{device['name']}: {args.path}, {free / 1024:.2f} GiB free. Backup: {backup}")
    print('IP addresses and recovery settings unchanged. Restart the bridge service to apply.')


if __name__ == '__main__':
    main()
