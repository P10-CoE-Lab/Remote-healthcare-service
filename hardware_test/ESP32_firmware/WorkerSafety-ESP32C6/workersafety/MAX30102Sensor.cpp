#include "MAX30102Sensor.h"

bool MAX30102Sensor::begin()
{
    delay(50);   // <-- Add this

    if (!sensor.begin(Wire))
        return false;

    sensor.setup(
        60,                 // LED Brightness
        SAMPLE_AVERAGE,     // Sample Average
        2,                  // RED + IR
        SAMPLE_RATE,        // Sample Rate
        411,                // Pulse Width
        4096                // ADC Range
    );

    sensor.setPulseAmplitudeGreen(0);

    return true;
}

bool MAX30102Sensor::update()
{
    sensor.check();

    if (!sensor.available())
        return false;

    redValue = sensor.getRed();
    irValue  = sensor.getIR();

    sensor.nextSample();

    return true;
}

uint32_t MAX30102Sensor::getIR() const
{
    return irValue;
}

uint32_t MAX30102Sensor::getRed() const
{
    return redValue;
}

bool MAX30102Sensor::hasFinger() const
{
    return irValue > FINGER_THRESHOLD;
}

uint8_t MAX30102Sensor::getSignalQuality() const
{
    if (!hasFinger())
        return 0;

    if (irValue >= 180000)
        return 100;

    return map(irValue, FINGER_THRESHOLD, 180000, 20, 100);
}