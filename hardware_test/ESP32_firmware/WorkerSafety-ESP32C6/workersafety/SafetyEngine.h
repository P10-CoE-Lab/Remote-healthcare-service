#ifndef SAFETY_ENGINE_H
#define SAFETY_ENGINE_H

#include "WorkerStatus.h"

enum AlertType
{
    ALERT_NONE,

    ALERT_FALL,

    ALERT_LOW_SPO2,

    ALERT_HIGH_HEART_RATE,

    ALERT_LOW_HEART_RATE,

    ALERT_HIGH_TEMPERATURE,

    ALERT_SENSOR_REMOVED,

    ALERT_EMERGENCY
};

class SafetyEngine
{
public:

    void update(const WorkerStatus& status);

    AlertType getAlert() const;

    const char* getAlertName() const;

private:

    AlertType alert = ALERT_NONE;
};

#endif