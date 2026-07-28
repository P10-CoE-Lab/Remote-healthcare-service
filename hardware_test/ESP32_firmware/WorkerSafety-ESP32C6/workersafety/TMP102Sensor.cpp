#include "TMP102Sensor.h"

bool TMP102Sensor::begin()
{
    Wire.beginTransmission(ADDRESS);

    return Wire.endTransmission() == 0;
}

bool TMP102Sensor::update()
{
    Wire.beginTransmission(ADDRESS);
    Wire.write(0x00);

    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(ADDRESS, (uint8_t)2) != 2)
        return false;

    uint8_t msb = Wire.read();
    uint8_t lsb = Wire.read();

    int16_t raw = (msb << 8) | lsb;

    raw >>= 4;

    temperature = raw * 0.0625f;

    return true;
}

float TMP102Sensor::getTemperature() const
{
    return temperature;
}