#ifndef BATTERY_MONITOR_H
#define BATTERY_MONITOR_H

#include <Arduino.h>

class BatteryMonitor
{
public:
    explicit BatteryMonitor(uint8_t adcPin);

    void begin();

    float getVoltage();
    int getPercentage();

private:
    uint8_t pin;

    float readVoltage();
};

#endif