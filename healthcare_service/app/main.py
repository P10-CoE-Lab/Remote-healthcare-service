import json
import logging
import logging.config
import time

import paho.mqtt.client as mqtt

import alert_handler
import config
import influx_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("MQTT connected to %s:%s", config.MQTT_HOST, config.MQTT_PORT)
        client.subscribe(config.MQTT_TOPIC)
        log.info("Subscribed to topic: %s", config.MQTT_TOPIC)
    else:
        log.error("MQTT connection refused (rc=%s)", rc)


def on_message(client, userdata, message):
    try:
        msg       = json.loads(message.payload.decode())
        sensor    = msg.get("sensor_name", "?")
        value     = msg.get("value", "?")
        condition = msg.get("condition", "normal")

        log.info("MSG  sensor=%-25s value=%-8s condition=%s", sensor, value, condition)

        influx_writer.write(msg)
        alert_handler.send_alert(msg)

    except Exception as e:
        log.error("Failed to process message: %s", e)


def main():
    log.info("Remote Healthcare Service starting")
    log.info("Config — MQTT: %s:%s  topic: %s", config.MQTT_HOST, config.MQTT_PORT, config.MQTT_TOPIC)
    log.info("Config — InfluxDB: %s  org: %s  bucket: %s", config.INFLUXDB_URL, config.INFLUXDB_ORG, config.INFLUXDB_BUCKET)

    # Verify InfluxDB is reachable and properly initialised before subscribing
    influx_writer.verify_connection()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            log.info("Connecting to MQTT broker %s:%s ...", config.MQTT_HOST, config.MQTT_PORT)
            client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            log.warning("MQTT connection failed: %s — retrying in 5s", e)
            time.sleep(5)

    client.loop_forever()


if __name__ == "__main__":
    main()
