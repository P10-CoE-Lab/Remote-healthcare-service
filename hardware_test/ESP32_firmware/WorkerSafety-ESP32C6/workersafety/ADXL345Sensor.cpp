#include "ADXL345Sensor.h"
#include <math.h>

bool ADXL345Sensor::begin()
{
    if (!accel.begin())
        return false;

    accel.setRange(ADXL345_RANGE_16_G);
    accel.setDataRate(ADXL345_DATARATE_100_HZ);

    return true;
}

bool ADXL345Sensor::update()
{
    accel.getEvent(&event);

    x = event.acceleration.x / 9.80665f;
    y = event.acceleration.y / 9.80665f;
    z = event.acceleration.z / 9.80665f;

    magnitude = sqrt(
        x * x +
        y * y +
        z * z);

    return true;
}

float ADXL345Sensor::getX() const
{
    return x;
}

float ADXL345Sensor::getY() const
{
    return y;
}

float ADXL345Sensor::getZ() const
{
    return z;
}

float ADXL345Sensor::getMagnitude() const
{
    return magnitude;
}