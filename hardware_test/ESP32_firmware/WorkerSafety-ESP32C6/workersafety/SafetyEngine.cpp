#include "SafetyEngine.h"

void SafetyEngine::update(const WorkerStatus& status)
{
    alert = ALERT_NONE;

    if (!status.fingerDetected)
    {
        alert = ALERT_SENSOR_REMOVED;
        return;
    }

    if (status.fallDetected)
    {
        alert = ALERT_FALL;
    }

    if (status.spo2 < 90)
    {
        alert = ALERT_LOW_SPO2;
    }

    if (status.heartRate > 140)
    {
        alert = ALERT_HIGH_HEART_RATE;
    }

    if (status.heartRate > 0 &&
        status.heartRate < 45)
    {
        alert = ALERT_LOW_HEART_RATE;
    }

    if (status.bodyTemperature > 38.5)
    {
        alert = ALERT_HIGH_TEMPERATURE;
    }

    if (status.fallDetected &&
        status.spo2 < 90)
    {
        alert = ALERT_EMERGENCY;
    }
}

AlertType SafetyEngine::getAlert() const
{
    return alert;
}

const char* SafetyEngine::getAlertName() const
{
    switch(alert)
    {
        case ALERT_NONE: return "NONE";
        case ALERT_FALL: return "FALL";
        case ALERT_LOW_SPO2: return "LOW SPO2";
        case ALERT_HIGH_HEART_RATE: return "HIGH HR";
        case ALERT_LOW_HEART_RATE: return "LOW HR";
        case ALERT_HIGH_TEMPERATURE: return "HIGH TEMP";
        case ALERT_SENSOR_REMOVED: return "NO SENSOR";
        case ALERT_EMERGENCY: return "EMERGENCY";
    }

    return "UNKNOWN";
}