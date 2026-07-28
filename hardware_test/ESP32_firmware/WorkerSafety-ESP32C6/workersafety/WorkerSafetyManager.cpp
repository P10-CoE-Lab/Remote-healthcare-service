#include "WorkerSafetyManager.h"
#include <Arduino.h>
#include <Wire.h>

bool WorkerSafetyManager::begin()
{
    Wire.begin(6, 7);

    if (!accel.begin())
    {
        Serial.println("ADXL345 initialization failed");
        return false;
    }

    if (!pulse.begin())
    {
        Serial.println("MAX30102 initialization failed");
        return false;
    }

    if (!temperatureSensor.begin())
    {
        Serial.println("TMP102 initialization failed");
        return false;
    }

    batteryMonitor.begin();

    bleScanner.begin();

    Serial.println("Worker Safety initialized");
    return true;
}

// void WorkerSafetyManager::update()
// {
//     updateMotion();

//     updateHealth();

//     updateTemperature();

//     updateBattery();

//     safety.update(status);

//     bleScanner.update();

//     status.nearestAnchorID = bleScanner.getNearestAnchorID();
//     status.anchorRSSI = bleScanner.getRSSI();
//     status.anchorDistance = bleScanner.getDistance();

//     status.uptime = millis();

//     if (millis() - lastPrint >= 1000)
//     {
//         lastPrint = millis();

//         printStatus();
//     }
// }

void WorkerSafetyManager::update()
{
    updateMotion();
    updateHealth();
    updateTemperature();
    updateBattery();

    safety.update(status);

    static uint32_t lastBLE = 0;

    // Scan only once every second
    if (millis() - lastBLE >= 1000)
    {
        lastBLE = millis();

        bleScanner.update();

        status.nearestAnchorID = bleScanner.getNearestAnchorID();
        status.anchorRSSI = bleScanner.getRSSI();
        status.anchorDistance = bleScanner.getDistance();
    }

    status.uptime = millis();

    if (millis() - lastPrint >= 1000)
    {
        lastPrint = millis();
        printStatus();
    }
}

const WorkerStatus& WorkerSafetyManager::getStatus() const
{
    return status;
}

void WorkerSafetyManager::updateMotion()
{
    accel.update();

    motion.update(accel.getMagnitude());

    orientation.update(
        accel.getX(),
        accel.getY(),
        accel.getZ());

    activity.update(
        accel.getMagnitude(),
        motion.getJerk());

    fall.update(
        accel.getMagnitude(),
        orientation.getAngle(),
        motion.getJerk());

    // Update WorkerStatus
    status.acceleration = accel.getMagnitude();
    status.jerk = motion.getJerk();
    status.orientation = orientation.getAngle();
    status.fallDetected = fall.getState() == FallDetector::FALL_DETECTED;

    switch (activity.getActivity())
    {
        case ActivityDetector::STANDING:
            status.activity = ACTIVITY_STANDING;
            break;

        case ActivityDetector::WALKING:
            status.activity = ACTIVITY_WALKING;
            break;

        case ActivityDetector::RUNNING:
            status.activity = ACTIVITY_RUNNING;
            break;
    }
}

void WorkerSafetyManager::updateHealth()
{
    static uint32_t last = 0;

    last = millis();

    if (!pulse.update())
        return;

    if (!pulse.hasFinger())
    {
        filter.reset();
        analyzer.reset();
        detector.reset();
        heartRate.reset();
        spo2.reset();

        status.fingerDetected = false;
        status.heartRate = 0;
        status.spo2 = 0;
        status.heartRateVariability = 0;

        return;
    }

    status.fingerDetected = true;

    spo2.update(
        pulse.getRed(),
        pulse.getIR());

    float filtered = filter.process(pulse.getIR());

    SignalStats stats =
        analyzer.process(
            pulse.getIR(),
            filtered);
    
    if (!analyzer.isReady())
        return;

    bool beat =
        detector.update(
            filtered,
            stats);

    if (beat)
    {
        unsigned long rr = detector.getLastRR();

        heartRate.beatDetected(rr);
    }

    status.heartRate = heartRate.getBPM();
    status.spo2 = spo2.getSpO2();
    status.heartRateVariability = heartRate.getHRV();
}

void WorkerSafetyManager::updateTemperature()
{
    static unsigned long lastRead = 0;

    if (millis() - lastRead < 1000)
        return;

    lastRead = millis();

    if (temperatureSensor.update())
    {
        status.bodyTemperature =
            temperatureSensor.getTemperature();
    }
}

void WorkerSafetyManager::printStatus()
{
    Serial.print("State: ");

    switch (fall.getState())
    {
        case FallDetector::IDLE:
            Serial.print("IDLE");
            break;

        case FallDetector::IMPACT_WAITING:
            Serial.print("IMPACT");
            break;

        case FallDetector::INACTIVITY:
            Serial.print("STILL");
            break;

        case FallDetector::FALL_DETECTED:
            Serial.print("FALL");
            break;
    }

    Serial.print("  Mag:");
    Serial.print(status.acceleration, 2);

    Serial.print("  Angle:");
    Serial.print(status.orientation, 1);

    Serial.print("  Jerk:");
    Serial.print(status.jerk, 3);

    Serial.print("  Activity:");
    Serial.println(activity.getActivityName());

    Serial.printf(
        "HR:%5.1f  HRV:%5.1fms  SpO2:%5.1f%%  Temp:%5.1fC  Activity:%-9s  Fall:%s\n",
        status.heartRate,
        status.heartRateVariability,
        status.spo2,
        status.bodyTemperature,
        activity.getActivityName(),
        status.fallDetected ? "YES" : "NO"
    );

    Serial.print(" Battery Voltage:");
    Serial.print(status.batteryVoltage);

    Serial.print("  Battery Percentage:");
    Serial.print(status.battery);

    Serial.print("  Alert:");
    Serial.println(safety.getAlertName());


    Serial.printf(
        "Nearest: %u  RSSI: %d  Distance: %.2f m\n",
        status.nearestAnchorID,
        status.anchorRSSI,
        status.anchorDistance
    );
}

void WorkerSafetyManager::updateBattery()
{
    status.battery = batteryMonitor.getPercentage();
    status.batteryVoltage = batteryMonitor.getVoltage();
}