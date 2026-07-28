#ifndef MAX30102SENSOR_H
#define MAX30102SENSOR_H

#include <Arduino.h>
#include <Wire.h>
#include "MAX30105.h"
#include "Config.h"

class MAX30102Sensor
{
public:
    bool begin();

    bool update();

    uint32_t getIR() const;
    uint32_t getRed() const;

    bool hasFinger() const;

    uint8_t getSignalQuality() const;

private:

    MAX30105 sensor;

    uint32_t irValue = 0;
    uint32_t redValue = 0;
};

#endif