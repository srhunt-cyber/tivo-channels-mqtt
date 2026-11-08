"""Home Assistant MQTT discovery, independent API/storage health and a bridge LWT."""
import json
import logging
import os
import threading

from paho.mqtt import client as mqtt

LOG = logging.getLogger(__name__)
FIELDS = {
    'playback':('Playback','mdi:play-pause','api'),
    'channel':('Channel','mdi:television-guide','api'),
    'program_title':('Program','mdi:movie-open','api'),
    'display':('Now playing','mdi:television-play','api'),
    'free_mib':('Free storage','mdi:harddisk','adb'),
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
    if key == 'free_mib':
        result.update(unit_of_measurement='MiB',state_class='measurement',entity_category='diagnostic')
    if key == 'recovery_status':
        result['entity_category'] = 'diagnostic'
    return result


class Publisher:
    def __init__(self, config):
        self.config = config
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
        self.rediscover.set()
        LOG.info('MQTT connected')

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.connected.clear()
        LOG.warning('MQTT disconnected: %s',reason)

    def on_message(self, client, userdata, message):
        if message.payload == b'online':
            self.rediscover.set()

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
        for device in self.config['devices']:
            # Force a fresh poll before reasserting per-device availability.
            root = self.prefix + '/' + device['id']
            self.send(root + '/api_availability','offline',True)
            self.send(root + '/adb_availability','offline',True)
            for key in FIELDS:
                topic = self.settings['discovery_prefix'] + '/sensor/tivo_channels_' + device['id'] + '/' + key + '/config'
                self.send(topic,discovery(self.config,device,key),True)
        self.send(self.prefix + '/availability','online',True)

    def publish(self, device, state, api_ok, adb_ok):
        root = self.prefix + '/' + device['id']
        # Telemetry isn't retained: old values must not look fresh after HA restart.
        self.send(root + '/state',state)
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
