#!/usr/bin/env python3
"""Back up a local config and enable only manual maintenance commands."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from tivo_channels_mqtt.config import load

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'config.json').resolve()
load(path)
data = json.loads(path.read_text())
backup = path.with_name(path.name + '.backup-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
with backup.open('x') as f:
    os.chmod(backup, 0o600)
    f.write(path.read_text())
data.setdefault('manual_control', {})['enabled'] = True
temporary = path.with_name(path.name + '.manual.tmp')
with temporary.open('w') as f:
    os.chmod(temporary, 0o600)
    f.write(json.dumps(data, indent=2) + '\n')
load(temporary)
os.replace(temporary, path)
print('Backup:', backup)
print('Manual controls enabled. Automatic recovery settings unchanged:', data['recovery'])
