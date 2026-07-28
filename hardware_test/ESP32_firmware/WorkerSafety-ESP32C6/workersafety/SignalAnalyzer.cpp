#include "SignalAnalyzer.h"
#include <math.h>

SignalAnalyzer::SignalAnalyzer()
{
    reset();
}

void SignalAnalyzer::reset()
{
    maxValue = 0.0f;
    minValue = 0.0f;

    noise = 0.0f;
    rms = 0.0f;

    acAmplitude = 0.0f;

    sampleCount = 0;

    ready = false;
    previousFiltered = 0.0f;
}

SignalStats SignalAnalyzer::process(uint32_t rawIR, float filtered)
{
    SignalStats stats;

    //----------------------------------------
    // Finger Detection
    //----------------------------------------

    stats.fingerPresent = (rawIR > 50000);

    //----------------------------------------
    // DC Level
    //----------------------------------------

    stats.dc = (float)rawIR;

    //----------------------------------------
    // Initialize new measurement window
    //----------------------------------------

    if (sampleCount == 0)
    {
        maxValue = filtered;
        minValue = filtered;
    }

    //----------------------------------------
    // Track waveform min/max
    //----------------------------------------

    if (filtered > maxValue)
        maxValue = filtered;

    if (filtered < minValue)
        minValue = filtered;

    sampleCount++;

    //----------------------------------------
    // AC Amplitude
    //----------------------------------------

    if (sampleCount >= WINDOW_SIZE)
    {
        float newAC = (maxValue - minValue) * 0.5f;

        Serial.println("WINDOW COMPLETE");


        if (acAmplitude == 0.0f)
        {
            acAmplitude = newAC;
        }
        else
        {
            // Smooth the AC amplitude
            // acAmplitude =
            //     acAmplitude * 0.85f +
            //     newAC * 0.15f;
            acAmplitude =
            acAmplitude * 0.30f +
            newAC * 0.70f;
        }

        // Start a fresh measurement window
        maxValue = filtered;
        minValue = filtered;

        ready = true;

        sampleCount = 0;
    }

    stats.acAmplitude = acAmplitude;

    //----------------------------------------
    // Noise Estimate
    //----------------------------------------

    float delta = filtered - previousFiltered;
    previousFiltered = filtered;

    // Estimate noise from rapid sample-to-sample changes
    noise =
        0.98f * noise +
        0.02f * fabs(delta);

    stats.noise = noise;

    //----------------------------------------
    // RMS Estimate
    //----------------------------------------

    float square = filtered * filtered;

    rms = 0.99f * rms + 0.01f * square;

    stats.rms = sqrtf(rms);

    //----------------------------------------
    // Signal-to-Noise Ratio
    //----------------------------------------

    if (noise < 1.0f)
        stats.snr = 0;
    else
        stats.snr = acAmplitude / noise;

    //----------------------------------------
    // Signal Quality
    //----------------------------------------

    if (!stats.fingerPresent)
    {
        stats.quality = 0;
    }
    else if (stats.snr >= 8.0f)
    {
        stats.quality = 100;
    }
    else if (stats.snr >= 6.0f)
    {
        stats.quality = 90;
    }
    else if (stats.snr >= 4.5f)
    {
        stats.quality = 80;
    }
    else if (stats.snr >= 3.5f)
    {
        stats.quality = 70;
    }
    else if (stats.snr >= 2.5f)
    {
        stats.quality = 60;
    }
    else if (stats.snr >= 1.5f)
    {
        stats.quality = 40;
    }
    else
    {
        stats.quality = 20;
    }

    return stats;
}

bool SignalAnalyzer::isReady() const
{
    return ready;
}