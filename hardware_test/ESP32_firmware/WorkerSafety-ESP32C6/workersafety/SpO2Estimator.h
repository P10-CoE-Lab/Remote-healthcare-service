#pragma once

#include <Arduino.h>

class SpO2Estimator
{
public:
    void reset();
    void update(uint32_t red, uint32_t ir);

    float getSpO2() const;
    int getQuality() const;
    bool isFingerDetected() const;

private:

    static const uint16_t BUFFER_SIZE = 100;

    uint32_t irBuffer[BUFFER_SIZE];
    uint32_t redBuffer[BUFFER_SIZE];

    uint16_t head = 0;
    bool full = false;

    float spo2 = 0;
    int quality = 0;
    bool fingerDetected = false;

    unsigned long lastCalc = 0;

    void calculate();
};