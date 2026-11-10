"""Home Assistant MQTT discovery, independent API/storage health and a bridge LWT."""
import json
import logging
import os
import threading
import time

from .commands import ACTIONS

from paho.mqtt import client as mqtt

LOG = logging.getLogger(__name__)
FIELDS = {
    'playback':('Playback','mdi:play-pause','api'),
    'channel':('Channel','mdi:television-guide','api'),
    'program_title':('Program','mdi:movie-open','api'),
    'display':('Now playing','mdi:television-play','api'),
    'free_mib':('Free storage','mdi:harddisk','adb'),
    'internal_free_mib':('Internal free storage','mdi:harddisk','adb'),
    'usb_free_gib':('USB free storage','mdi:usb-flash-drive','adb'),
    'usb_total_gib':('USB capacity','mdi:usb-flash-drive','adb'),
    'usb_used_percent':('USB used','mdi:usb-flash-drive','adb'),
    'storage_location':('Channels storage','mdi:folder','adb'),
    'recovery_status':('Storage watchdog','mdi:shield-refresh','api'),
}


def discovery(config, device, key):
    prefix = config['mqtt']['topic_prefix']
    root = prefix + '/' + device['id']
    name, icon, health = FIELDS[key]
    expiry = max(90,int(config['poll_seconds'] * 6))
    result = {
        'name':name, 'unique_id':'tivo_channels_' + device['id'] + '_' + key,
        'default_entity_id':'sensor.' + device['id'] + '_tivo_mqtt_' + key,
        'state_topic':root + '/state', 'value_template':'{{ value_json.' + key + ' }}',
        'icon':icon, 'expire_after':expiry,
        'availability':[
            {'topic':prefix + '/availability'},
            {'topic':root + '/' + health + '_availability'}],
        'availability_mode':'all',
        'device':{'identifiers':['tivo_channels_' + device['id']], 'name':device['name'],
                  'manufacturer':'TiVo', 'model':'Stream 4K / Channels'},
    }
    if key == 'playback':
        result['json_attributes_topic'] = root + '/state'
    units = {'free_mib': 'MiB', 'internal_free_mib': 'MiB', 'usb_free_gib': 'GiB',
             'usb_total_gib': 'GiB', 'usb_used_percent': '%'}
    if key in units:
        result.update(unit_of_measurement=units[key], state_class='measurement', entity_category='diagnostic')
        result['value_template'] = '{{ value_json.' + key + ' if value_json.' + key + ' is not none else none }}'
    if key == 'storage_location':
        result['entity_category'] = 'diagnostic'
    if key == 'recovery_status':
        result['entity_category'] = 'diagnostic'
    return result


class Publisher:
    def __init__(self, config):
        self.config = config
        self.commands = None
        self.started = time.monotonic()
        self.settings = config['mqtt']
        self.prefix = self.settings['topic_prefix']
        self.rediscover = threading.Event()
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=self.settings['client_id'])
        user_env = self.settings.get('username_env','MQTT_USERNAME')
        pass_env = self.settings.get('password_env','MQTT_PASSWORD')
        username, password = os.environ.get(user_env), os.environ.get(pass_env)
        if password and not username:
            raise ValueError('MQTT password is set but username is missing')
        if username:
            self.client.username_pw_set(username,password)
        if self.settings.get('tls'):
            self.client.tls_set(ca_certs=self.settings.get('ca_certs'))
        self.client.will_set(self.prefix + '/availability','offline',qos=1,retain=True)
        self.client.reconnect_delay_set(1,60)
        self.client.max_queued_messages_set(256)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            LOG.error('MQTT connection rejected: %s',reason)
            return
        self.connected.set()
        client.subscribe(self.settings['discovery_prefix'] + '/status',qos=1)
        if self.config.get('manual_control', {}).get('enabled', False):
            for device in self.config['devices']:
                client.subscribe(self.prefix + '/' + device['id'] + '/command', qos=0)
        self.rediscover.set()
        LOG.info('MQTT connected')

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.connected.clear()
        LOG.warning('MQTT disconnected: %s',reason)

    def on_message(self, client, userdata, message):
        if message.topic == self.settings['discovery_prefix'] + '/status':
            if message.payload == b'online':
                self.rediscover.set()
            return
        if self.commands is not None:
            for device in self.config['devices']:
                if message.topic == self.prefix + '/' + device['id'] + '/command':
                    try:
                        action = message.payload.decode('ascii')
                    except UnicodeDecodeError:
                        return
                    self.commands.submit(device['id'], action, retained=message.retain)
                    return

    def send(self, topic, payload, retain=False):
        if not self.connected.is_set():
            return
        if isinstance(payload,dict):
            payload = json.dumps(payload,ensure_ascii=False,allow_nan=False)
        info = self.client.publish(topic,payload,qos=1,retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning('MQTT publish not accepted for %s: %s',topic,info.rc)

    def announce(self):
        if not self.rediscover.is_set() or not self.connected.is_set():
            return
        self.rediscover.clear()
        self.announce_controls()
        for device in self.config['devices']:
            # Force a fresh poll before reasserting per-device availability.
            root = self.prefix + '/' + device['id']
            self.send(root + '/api_availability','offline',True)
            self.send(root + '/adb_availability','offline',True)
            for key in FIELDS:
                topic = self.settings['discovery_prefix'] + '/sensor/tivo_channels_' + device['id'] + '/' + key + '/config'
                self.send(topic,discovery(self.config,device,key),True)
        self.send(self.prefix + '/availability','online',True)

    def announce_controls(self):
        bridge_device = {'identifiers': ['tivo_channels_bridge_' + self.settings['client_id']],
                         'name': 'TiVo Channels Bridge', 'manufacturer': 'tivo-channels-mqtt',
                         'model': 'MQTT bridge'}
        base = {'device': bridge_device, 'availability_topic': self.prefix + '/availability'}
        for key, name, unit in [('uptime', 'Uptime', 's')]:
            data = dict(base, name=name, unique_id=self.settings['client_id'] + '_' + key,
                        default_entity_id='sensor.tivo_channels_bridge_' + key,
                        state_topic=self.prefix + '/bridge', value_template='{{ value_json.' + key + ' }}',
                        unit_of_measurement=unit, entity_category='diagnostic',
                        expire_after=max(90, int(self.config['poll_seconds'] * 6)))
            self.send(self.settings['discovery_prefix'] + '/sensor/' + self.settings['client_id'] + '/' + key + '/config', data, True)
        online = dict(base, name='Connection', unique_id=self.settings['client_id'] + '_connection',
                      default_entity_id='binary_sensor.tivo_channels_bridge_connection',
                      state_topic=self.prefix + '/availability', device_class='connectivity',
                      payload_on='online', payload_off='offline')
        online.pop('availability_topic')
        self.send(self.settings['discovery_prefix'] + '/binary_sensor/' + self.settings['client_id'] + '/connection/config', online, True)
        for device in self.config['devices']:
            root = self.prefix + '/' + device['id']
            common = {'device': discovery(self.config, device, 'playback')['device'],
                      'availability_topic': self.prefix + '/availability'}
            for health, label in [('api', 'Channels connection'), ('adb', 'ADB connection')]:
                value = dict(common, name=label, unique_id='tivo_channels_' + device['id'] + '_' + health,
                             default_entity_id='binary_sensor.' + device['id'] + '_tivo_mqtt_' + health,
                             state_topic=root + '/health', value_template='{{ value_json.' + health + ' }}',
                             payload_on='online', payload_off='offline', device_class='connectivity',
                             expire_after=max(90, int(self.config['poll_seconds'] * 6)))
                self.send(self.settings['discovery_prefix'] + '/binary_sensor/tivo_channels_' + device['id'] + '/' + health + '/config', value, True)
            value = dict(common, name='Last manual command',
                         unique_id='tivo_channels_' + device['id'] + '_command_status',
                         default_entity_id='sensor.' + device['id'] + '_tivo_mqtt_command_status',
                         state_topic=root + '/command_status', json_attributes_topic=root + '/command_status',
                         value_template='{{ value_json.status }}', icon='mdi:progress-wrench')
            self.send(self.settings['discovery_prefix'] + '/sensor/tivo_channels_' + device['id'] + '/command_status/config', value, True)
            for action in ACTIONS:
                topic = self.settings['discovery_prefix'] + '/button/tivo_channels_' + device['id'] + '/' + action + '/config'
                self.send(topic, button_discovery(self.config, device, action), True)
            if self.commands is not None:
                self.command_status(device, self.commands.results[device['id']])

    def command_status(self, device, result):
        root = self.prefix + '/' + device['id']
        self.send(root + '/command_status', result, True)
        ready = self.config.get('manual_control', {}).get('enabled', False) and not result.get('busy')
        self.send(root + '/control_availability', 'online' if ready else 'offline', True)
        if result.get('busy'):
            self.send(root + '/api_availability', 'offline', True)
            self.send(root + '/adb_availability', 'offline', True)
            self.send(root + '/health', {'api': 'offline', 'adb': 'offline'})

    def heartbeat(self):
        self.send(self.prefix + '/bridge', {'uptime': int(time.monotonic() - self.started)})

    def publish(self, device, state, api_ok, adb_ok):
        root = self.prefix + '/' + device['id']
        # Telemetry isn't retained: old values must not look fresh after HA restart.
        self.send(root + '/state',state)
        self.send(root + '/health', {'api': 'online' if api_ok else 'offline', 'adb': 'online' if adb_ok else 'offline'})
        self.send(root + '/api_availability','online' if api_ok else 'offline',True)
        self.send(root + '/adb_availability','online' if adb_ok else 'offline',True)

    def start(self):
        self.client.connect_async(self.settings['host'],self.settings.get('port',1883),keepalive=30)
        self.client.loop_start()

    def close(self):
        if self.connected.is_set():
            info = self.client.publish(self.prefix + '/availability','offline',qos=1,retain=True)
            try:
                info.wait_for_publish(timeout=3)
            except RuntimeError:
                pass
        self.client.disconnect()
        self.client.loop_stop()


def button_discovery(config, device, action):
    root = config['mqtt']['topic_prefix'] + '/' + device['id']
    base = discovery(config, device, 'playback')
    name, icon = ACTIONS[action]
    health = 'api' if action == 'restart_stream' else 'adb'
    return {
        'name': name, 'icon': icon, 'device': base['device'],
        'unique_id': 'tivo_channels_' + device['id'] + '_' + action,
        'default_entity_id': 'button.' + device['id'] + '_tivo_mqtt_' + action,
        'command_topic': root + '/command', 'payload_press': action,
        'qos': 0, 'retain': False, 'entity_category': 'config',
        'availability_mode': 'all',
        'availability': [{'topic': config['mqtt']['topic_prefix'] + '/availability'},
                         {'topic': root + '/control_availability'},
                         {'topic': root + '/' + health + '_availability'}],
    }
