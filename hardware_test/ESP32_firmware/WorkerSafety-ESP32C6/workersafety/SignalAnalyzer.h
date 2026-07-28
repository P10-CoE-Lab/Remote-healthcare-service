#ifndef SIGNALANALYZER_H
#define SIGNALANALYZER_H

#include <Arduino.h>

struct SignalStats
{
    float dc;
    float acAmplitude;
    float rms;
    float noise;
    float snr;
    uint8_t quality;
    bool fingerPresent;
};

class SignalAnalyzer
{
public:

    SignalAnalyzer();

    void reset();

    SignalStats process(uint32_t rawIR, float filtered);

    bool isReady() const;

private:

    // Window tracking
    float maxValue;
    float minValue;

    // Signal metrics
    float noise;
    float rms;
    float acAmplitude;

    // Window state
    uint16_t sampleCount;
    bool ready;
    float previousFiltered;

    // Configuration
    static constexpr uint16_t WINDOW_SIZE = 32;
};

#endif