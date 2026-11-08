#!/usr/bin/env python3
"""Install a system service using this checkout, current user and existing ADB keys.

Run with the project's virtualenv Python as your normal login user, NOT with sudo.
The script invokes sudo only for installing/enabling the system service.
"""
import argparse
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile


def render_unit(project, user, group, home, state_dir):
    values = {'USER': user, 'GROUP': group, 'CHECKOUT': str(project),
              'HOME': str(home), 'STATE_DIR': str(state_dir)}
    # This intentionally supports ordinary Linux usernames/paths. Reject systemd
    # metacharacters rather than interpolating ambiguous unit directives.
    for name, value in values.items():
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
            raise ValueError('Unsupported characters in ' + name + ': ' + value)
    unit = (project/'deploy/tivo-channels-mqtt-checkout.service.in').read_text()
    for key, value in values.items():
        unit = unit.replace('@' + key + '@', value)
    return unit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--print', action='store_true', help='Print the rendered unit without installing it')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your normal login user, not with sudo')
    project = Path(__file__).resolve().parents[1]
    user = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(user.pw_gid).gr_name
    home = Path(user.pw_dir)
    config_path = project/'config.json'
    if not config_path.is_file():
        parser.error('Create and verify config.json before installing the service')
    executable = project/'.venv/bin/tivo-channels-mqtt'
    if not executable.is_file() or not os.access(executable,os.X_OK):
        parser.error('Install this project into its .venv first')
    # Validate using the same environment and entry point that the service will run.
    subprocess.run([str(executable),'--config',str(config_path),'--check-config'],check=True)
    config = json.loads(config_path.read_text())
    state_dir = Path(config.get('state_dir','state')).expanduser()
    if not state_dir.is_absolute():
        state_dir = project/state_dir
    state_dir = state_dir.resolve()
    unit = render_unit(project,user.pw_name,group,home,state_dir)
    if args.print:
        print(unit)
        return
    state_dir.mkdir(parents=True,exist_ok=True)
    (home/'.android').mkdir(mode=0o700,exist_ok=True)
    # Refuse to install while the foreground bridge still owns its state lock.
    # Release before systemd starts the replacement process.
    with (state_dir/'bridge.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('The bridge is still running. Stop the foreground process with Control-C first.')
    with tempfile.TemporaryDirectory(prefix='tivo-service-') as directory:
        path = Path(directory)/'tivo-channels-mqtt.service'
        path.write_text(unit)
        subprocess.run(['sudo','install','-m','0644',str(path),
                        '/etc/systemd/system/tivo-channels-mqtt.service'],check=True)
    subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
    subprocess.run(['sudo','systemctl','enable','tivo-channels-mqtt.service'],check=True)
    subprocess.run(['sudo','systemctl','restart','tivo-channels-mqtt.service'],check=True)
    subprocess.run(['sudo','systemctl','status','tivo-channels-mqtt.service','--no-pager'],check=True)
    print('\nService installed. Check live readings with:')
    print('sudo journalctl -u tivo-channels-mqtt -f')
    print('It uses the current config.json; recovery settings were not changed.')


if __name__ == '__main__':
    main()
