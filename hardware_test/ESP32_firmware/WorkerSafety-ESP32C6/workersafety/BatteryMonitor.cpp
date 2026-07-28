#include "BatteryMonitor.h"

BatteryMonitor::BatteryMonitor(uint8_t adcPin)
{
    pin = adcPin;
}

void BatteryMonitor::begin()
{
    analogReadResolution(12);
}

float BatteryMonitor::readVoltage()
{
    int mv = analogReadMilliVolts(pin);

    // DFRobot Beetle ESP32-C6 uses a 2:1 divider
    return (mv * 2.0f) / 1000.0f;
}

float BatteryMonitor::getVoltage()
{
    return readVoltage();
}

int BatteryMonitor::getPercentage()
{
    float v = readVoltage();

    if (v >= 4.20f) return 100;
    if (v >= 4.10f) return 90;
    if (v >= 4.00f) return 80;
    if (v >= 3.90f) return 65;
    if (v >= 3.80f) return 45;
    if (v >= 3.70f) return 25;
    if (v >= 3.60f) return 10;
    if (v >= 3.50f) return 5;

    return 0;
}