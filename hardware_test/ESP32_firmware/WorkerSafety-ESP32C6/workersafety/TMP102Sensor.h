#ifndef TMP102_SENSOR_H
#define TMP102_SENSOR_H

#include <Arduino.h>
#include <Wire.h>

class TMP102Sensor
{
public:
    bool begin();

    bool update();

    float getTemperature() const;

private:
    float temperature = 0.0f;

    static constexpr uint8_t ADDRESS = 0x48;
};

#endif