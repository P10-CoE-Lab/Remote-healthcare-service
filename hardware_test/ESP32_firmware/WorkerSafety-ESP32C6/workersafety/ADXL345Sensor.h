#pragma once

#include <Adafruit_ADXL345_U.h>

class ADXL345Sensor
{
public:
    bool begin();
    bool update();

    float getX() const;
    float getY() const;
    float getZ() const;
    float getMagnitude() const;

private:
    Adafruit_ADXL345_Unified accel =
        Adafruit_ADXL345_Unified(12345);

    sensors_event_t event;

    float x = 0;
    float y = 0;
    float z = 0;
    float magnitude = 0;
};