#ifndef MQTT_MANAGER_H
#define MQTT_MANAGER_H

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "WorkerStatus.h"

class MQTTManager
{
public:

    MQTTManager();

    bool begin();

    void update();

    bool connected();

    void publishStatus(const WorkerStatus& status);

    // Canonical per-sensor iots/... publish — see
    // HANDOFF_wrist_unit_healthcare_integration.md. Publishes alongside
    // publishStatus() from the same tick, does not replace it.
    void publishCanonicalSensors(const WorkerStatus& status);

private:

    void reconnect();
    void syncTime();
    void handleConfigMessage(char* topic, byte* payload, unsigned int length);
    void publishCanonicalSensor(const char* sensorName, float value, const char* unit,
        const char* phase, const char* condition, const char* quality,
        bool faultActive, long seq, const char* ts);

    static void staticCallback(char* topic, byte* payload, unsigned int length);
    static MQTTManager* instance;

    WiFiClient wifiClient;
    PubSubClient mqttClient;

    char patientLabel[64] = "Unregistered Device";
    char configTopic[80];

    long hrSeq   = 0;
    long spo2Seq = 0;
    long hrvSeq  = 0;
    long tempSeq = 0;
};

#endif