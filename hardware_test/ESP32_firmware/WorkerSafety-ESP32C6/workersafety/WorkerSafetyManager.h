#ifndef WORKER_SAFETY_MANAGER_H
#define WORKER_SAFETY_MANAGER_H

#include "WorkerStatus.h"

#include "ADXL345Sensor.h"
#include "MotionAnalyzer.h"
#include "OrientationDetector.h"
#include "ActivityDetector.h"
#include "FallDetector.h"
#include "PPGFilter.h"
#include "SignalAnalyzer.h"
#include "MAX30102Sensor.h"
#include "SignalFilter.h"
#include "PeakDetectorV4.h"
#include "HeartRateV2.h"
#include "SpO2Estimator.h"
#include "TMP102Sensor.h"
#include "SafetyEngine.h"
#include "BatteryMonitor.h"
#include "BLEScanner.h"

class WorkerSafetyManager
{
public:

    bool begin();

    void update();

    const WorkerStatus& getStatus() const;

private:

    WorkerStatus status;

    // Motion
    ADXL345Sensor accel;
    MotionAnalyzer motion;
    OrientationDetector orientation;
    ActivityDetector activity;
    FallDetector fall;


    // Health
    MAX30102Sensor pulse;

    SignalFilter filter;
    SignalAnalyzer analyzer;
    PeakDetectorV4 detector;
    HeartRateV2 heartRate;
    SpO2Estimator spo2;

    //temp
    TMP102Sensor temperatureSensor;

    //safety
    SafetyEngine safety;

    //Battery
    BatteryMonitor batteryMonitor{0};

    //BLE
    BLEScanner bleScanner;

    unsigned long lastPrint = 0;

    void updateMotion();
    void updateHealth();
    void updateTemperature();
    void updateBattery();
    void printStatus();
};

#endif