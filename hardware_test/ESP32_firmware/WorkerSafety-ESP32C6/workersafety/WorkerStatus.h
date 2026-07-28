#pragma once

#include <Arduino.h>
#include <stdint.h>

enum ActivityType
{
    ACTIVITY_STANDING,
    ACTIVITY_WALKING,
    ACTIVITY_RUNNING
};

struct WorkerStatus
{
    // -------- Vital Signs --------

    float heartRate = 0;
    float spo2 = 0;
    float heartRateVariability = 0;
    float bodyTemperature = 0;
    bool fingerDetected = false;

    // -------- Motion --------

    float acceleration = 0;
    float jerk = 0;
    float orientation = 0;
    ActivityType activity = ACTIVITY_STANDING;
    bool fallDetected = false;

    // -------- Device --------

    int battery = 100;
    float batteryVoltage = 0.0f;
    uint32_t uptime = 0;

    // -------- BLE --------

    uint8_t nearestAnchorID = 0;     // 0 = No Anchor
    int anchorRSSI = -127;
    float anchorDistance = -1.0f;

    // -------- Alerts --------

    bool alert = false;
};