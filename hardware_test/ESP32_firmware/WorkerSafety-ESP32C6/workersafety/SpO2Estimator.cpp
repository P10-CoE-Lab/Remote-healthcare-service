#include "SpO2Estimator.h"
#include "Config.h"
#include <math.h>

void SpO2Estimator::reset()
{
    head = 0;
    full = false;

    spo2 = 0;
    quality = 0;
    fingerDetected = false;

    lastCalc = 0;

    memset(irBuffer, 0, sizeof(irBuffer));
    memset(redBuffer, 0, sizeof(redBuffer));
}

void SpO2Estimator::update(uint32_t red, uint32_t ir)
{
    if (ir < FINGER_THRESHOLD)
    {
        reset();
        return;
    }

    fingerDetected = true;

    irBuffer[head] = ir;
    redBuffer[head] = red;

    head++;

    if (head >= BUFFER_SIZE)
    {
        head = 0;
        full = true;
    }

    if (!full)
        return;

    if (millis() - lastCalc < 1000)
        return;

    lastCalc = millis();

    calculate();
}

float SpO2Estimator::getSpO2() const
{
    return spo2;
}

int SpO2Estimator::getQuality() const
{
    return quality;
}

bool SpO2Estimator::isFingerDetected() const
{
    return fingerDetected;
}

void SpO2Estimator::calculate()
{
    float irDC = 0;
    float redDC = 0;

    for (int i = 0; i < BUFFER_SIZE; i++)
    {
        irDC += irBuffer[i];
        redDC += redBuffer[i];
    }

    irDC /= BUFFER_SIZE;
    redDC /= BUFFER_SIZE;

    float irAC = 0;
    float redAC = 0;

    for (int i = 0; i < BUFFER_SIZE; i++)
    {
        float dIR = irBuffer[i] - irDC;
        float dRED = redBuffer[i] - redDC;

        irAC += dIR * dIR;
        redAC += dRED * dRED;
    }

    irAC = sqrt(irAC / BUFFER_SIZE);
    redAC = sqrt(redAC / BUFFER_SIZE);

    // Debug prints
    Serial.printf(
        "IRDC=%.0f IRAC=%.0f REDDC=%.0f REDAC=%.0f\n",
        irDC,
        irAC,
        redDC,
        redAC
    );

    float ratio =
        (redAC / redDC) /
        (irAC / irDC);


    // Reject obvious bad ratios
    if (ratio < 0.2f || ratio > 1.2f)
        return;

    // Linear approximation
    float value = 110.0f - (25.0f * ratio);

    // Clamp
    value = constrain(value, 80.0f, 100.0f);

    // Smooth
    if (spo2 == 0)
        spo2 = value;
    else
        spo2 = 0.8f * spo2 + 0.2f * value;

    float pulseStrength = (irAC / irDC) * 100.0f;

    quality = constrain(
        (int)(pulseStrength * 500),
        0,
        100
    );

    Serial.printf(
        "R: %.3f  SpO2: %.1f%%\n",
        ratio,
        spo2
    );
}