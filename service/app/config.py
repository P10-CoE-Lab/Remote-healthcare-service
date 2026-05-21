import os

MQTT_HOST  = os.getenv("MQTT_HOST",  "localhost")
MQTT_PORT  = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "iots/#")

INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "my-super-secret-token")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "iot_org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vitals")

NOTIFICATION_URL = os.getenv("NOTIFICATION_URL", "http://localhost:8000/notify")
ALERT_EMAIL      = os.getenv("ALERT_EMAIL", "")
ALERT_PHONE      = os.getenv("ALERT_PHONE", "")
