#include "MQTTManager.h"
#include "MQTTConfig.h"
#include <time.h>

MQTTManager* MQTTManager::instance = nullptr;

MQTTManager::MQTTManager() :
    mqttClient(wifiClient)
{
    instance = this;
}

bool MQTTManager::begin()
{
    snprintf(configTopic, sizeof(configTopic), "config/%s/patient_label", IOTS_DEVICE_ID);

    mqttClient.setBufferSize(640);   // must cover the largest publishStatus() payload
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(staticCallback);

    syncTime();   // needed for iots/... timestamp_utc — worker_status has no time field

    reconnect();

    return mqttClient.connected();
}

// ─── Time (NTP, for timestamp_utc) — ported from cardiac_patient_sim.ino ──────
void MQTTManager::syncTime()
{
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("Waiting for NTP time sync");
    time_t now = time(nullptr);
    int tries = 0;
    while (now < 8 * 3600 * 2 && tries++ < 20)
    {
        delay(500);
        Serial.print(".");
        now = time(nullptr);
    }
    Serial.println();
    // Demo-safe: if NTP never syncs, keep running with epoch-based
    // timestamps rather than blocking or crashing.
    Serial.println(now > 8 * 3600 * 2 ? "NTP OK" : "NTP failed — continuing anyway");
}

static void isoTimestamp(char* buf, size_t len)
{
    time_t now = time(nullptr);
    struct tm tmStruct;
    gmtime_r(&now, &tmStruct);
    char base[32];
    strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &tmStruct);
    snprintf(buf, len, "%s.000+00:00", base);
}

// ─── Condition classification — mirrors cardiac_patient_sim.ino's ────────────
// classifyHeartRate()/classifySpo2()/classifyHrv(), thresholds from
// personas/generic_wearable_patient.yaml via MQTTConfig.h.
static const char* classifyHeartRate(float v)
{
    if (v >= IOTS_THRESH_HR_HIGH || v <= IOTS_THRESH_HR_LOW) return "warning";
    return "normal";
}

static const char* classifySpo2(float v)
{
    if (v <= IOTS_THRESH_SPO2_LOW) return "warning";
    return "normal";
}

static const char* classifyHrv(float v)
{
    if (v <= IOTS_THRESH_HRV_LOW) return "warning";
    return "normal";
}

// "phase" has no scripted scenario timeline on real hardware — derived
// instead from the device's own real ActivityDetector state.
static const char* phaseFromActivity(ActivityType activity)
{
    switch (activity)
    {
        case ACTIVITY_STANDING: return "standing";
        case ACTIVITY_WALKING:  return "walking";
        case ACTIVITY_RUNNING:  return "running";
    }
    return "standing";
}

void MQTTManager::staticCallback(char* topic, byte* payload, unsigned int length)
{
    if (instance) instance->handleConfigMessage(topic, payload, length);
}

// Only ever called for the retained config/{device}/patient_label topic —
// pushes the operator's chosen name down from demo_api.py's hardware
// patient registration so Grafana/the UI show the real identity instead
// of the "Unregistered Device" placeholder.
void MQTTManager::handleConfigMessage(char* topic, byte* payload, unsigned int length)
{
    size_t len = length < sizeof(patientLabel) - 1 ? length : sizeof(patientLabel) - 1;
    memcpy(patientLabel, payload, len);
    patientLabel[len] = '\0';
    Serial.printf("[CONFIG] patient_label -> \"%s\"\n", patientLabel);
}

void MQTTManager::update()
{
    if (mqttClient.connected())
    {
        mqttClient.loop();
        return;
    }

    static unsigned long lastReconnect = 0;

    if (millis() - lastReconnect < 5000)
        return;

    lastReconnect = millis();

    Serial.print("Connecting MQTT...");

    if (strlen(MQTT_USERNAME) == 0)
    {
        if (mqttClient.connect(MQTT_CLIENT_ID))
        {
            Serial.println("Connected");
            mqttClient.subscribe(configTopic);
        }
        else
        {
            Serial.print("Failed rc=");
            Serial.println(mqttClient.state());
        }
    }
    else
    {
        if (mqttClient.connect(
                MQTT_CLIENT_ID,
                MQTT_USERNAME,
                MQTT_PASSWORD))
        {
            Serial.println("Connected");
            mqttClient.subscribe(configTopic);
        }
        else
        {
            Serial.print("Failed rc=");
            Serial.println(mqttClient.state());
        }
    }
}

bool MQTTManager::connected()
{
    return mqttClient.connected();
}

void MQTTManager::reconnect()
{
    while (!mqttClient.connected())
    {
        Serial.print("Connecting MQTT...");

        WiFiClient test;

        Serial.print("TCP Test (");
        Serial.print(MQTT_SERVER);
        Serial.print(":");
        Serial.print(MQTT_PORT);
        Serial.print("): ");

        if (test.connect(MQTT_SERVER, MQTT_PORT))
        {
            Serial.println("SUCCESS");
            test.stop();
        }
        else
        {
            Serial.println("FAILED");
        }


        bool ok;

        if (strlen(MQTT_USERNAME) == 0)
        {
            ok = mqttClient.connect(MQTT_CLIENT_ID);
        }
        else
        {
            ok = mqttClient.connect(
                MQTT_CLIENT_ID,
                MQTT_USERNAME,
                MQTT_PASSWORD);
        }

        if (ok)
        {
            Serial.println("Connected");
            mqttClient.subscribe(configTopic);   // resubscribe — PubSubClient sessions aren't persistent
        }
        else
        {
            Serial.print("Failed rc=");
            Serial.println(mqttClient.state());

            delay(3000);
        }
    }
}

void MQTTManager::publishStatus(const WorkerStatus& status)
{
    if (!mqttClient.connected())
        return;

    StaticJsonDocument<640> doc;

    doc["workerId"] = MQTT_CLIENT_ID;
    doc["heartRate"] = status.heartRate;
    doc["heartRateVariability"] = status.heartRateVariability;
    doc["spo2"] = status.spo2;
    doc["temperature"] = status.bodyTemperature;
    doc["fingerDetected"] = status.fingerDetected;
    doc["fallDetected"] = status.fallDetected;
    doc["battery"] = status.battery;
    doc["uptime"] = status.uptime;
    doc["acceleration"] = status.acceleration;
    doc["jerk"] = status.jerk;
    doc["orientation"] = status.orientation;
    doc["nearestAnchorID"] = status.nearestAnchorID;
    doc["anchorRSSI"] = status.anchorRSSI;
    doc["anchorDistance"] = status.anchorDistance;
    
    switch (status.activity)
    {
        case ACTIVITY_STANDING:
            doc["activity"] = "STANDING";
            break;

        case ACTIVITY_WALKING:
            doc["activity"] = "WALKING";
            break;

        case ACTIVITY_RUNNING:
            doc["activity"] = "RUNNING";
            break;

        default:
            doc["activity"] = "UNKNOWN";
            break;
    }

    char payload[640];

    serializeJson(doc, payload);

    size_t len = serializeJson(doc, payload);

    Serial.print("Payload length: ");
    Serial.println(len);
    Serial.println(payload);

    bool ok = mqttClient.publish(
    MQTT_STATUS_TOPIC,
    payload,
    true);

    Serial.print("Publish: ");
    Serial.println(ok ? "SUCCESS" : "FAILED");

    if (!ok)
    {
        Serial.print("MQTT State: ");
        Serial.println(mqttClient.state());
    }
}

// ─── Canonical iots/... publish ───────────────────────────────────────────
// Mirrors cardiac_patient_sim.ino's publishSensor() — see
// HANDOFF_wrist_unit_healthcare_integration.md. Publishes one message per
// sensor to iots/{poc_type}/{persona_id}/{sensor_name}, alongside (not
// instead of) the worker_status blob above.
void MQTTManager::publishCanonicalSensors(const WorkerStatus& status)
{
    if (!mqttClient.connected())
        return;

    char ts[40];
    isoTimestamp(ts, sizeof(ts));

    const char* phase = phaseFromActivity(status.activity);

    // A real "0" while the finger isn't on the sensor is not a genuine
    // clinical reading (WorkerSafetyManager::updateHealth() zeroes these
    // on-device in that state) — quality/fault_active tell the app layer
    // apart from a true low reading, and condition is hardcoded "normal"
    // rather than run through classify*() so a finger-off zero can never
    // read as e.g. a bradycardia warning.
    const char* healthQuality   = status.fingerDetected ? "good" : "bad";
    bool        healthFault     = !status.fingerDetected;
    const char* hrCondition     = status.fingerDetected ? classifyHeartRate(status.heartRate) : "normal";
    const char* spo2Condition   = status.fingerDetected ? classifySpo2(status.spo2)            : "normal";
    const char* hrvCondition    = status.fingerDetected ? classifyHrv(status.heartRateVariability) : "normal";

    publishCanonicalSensor("heart_rate", status.heartRate, "bpm",
        phase, hrCondition, healthQuality, healthFault, ++hrSeq, ts);
    publishCanonicalSensor("spo2", status.spo2, "percent",
        phase, spo2Condition, healthQuality, healthFault, ++spo2Seq, ts);
    publishCanonicalSensor("heart_rate_variability", status.heartRateVariability, "ms",
        phase, hrvCondition, healthQuality, healthFault, ++hrvSeq, ts);

    // Display-only — never a real threshold classification (see
    // personas/generic_wearable_patient.yaml). Independent of finger
    // detection: TMP102 always reads regardless of PPG state.
    publishCanonicalSensor("body_temperature", status.bodyTemperature, "celsius",
        phase, "normal", "good", false, ++tempSeq, ts);
}

void MQTTManager::publishCanonicalSensor(const char* sensorName, float value, const char* unit,
    const char* phase, const char* condition, const char* quality,
    bool faultActive, long seq, const char* ts)
{
    StaticJsonDocument<512> doc;
    doc["timestamp_utc"]   = ts;
    doc["device_id"]       = IOTS_DEVICE_ID;
    doc["patient_label"]   = patientLabel;
    doc["persona_id"]      = IOTS_PERSONA_ID;
    doc["poc_type"]        = IOTS_POC_TYPE;
    doc["sensor_name"]     = sensorName;
    doc["value"]           = value;
    doc["unit"]            = unit;
    doc["phase"]           = phase;
    doc["condition"]       = condition;
    doc["quality"]         = quality;
    doc["fault_active"]    = faultActive;
    doc["sequence_number"] = seq;

    char payload[640];   // must stay <= mqttClient.setBufferSize(640) from begin()
    serializeJson(doc, payload);

    char topic[80];
    snprintf(topic, sizeof(topic), "%s/%s/%s/%s", IOTS_MQTT_PREFIX, IOTS_POC_TYPE, IOTS_PERSONA_ID, sensorName);
    mqttClient.publish(topic, payload);
}

