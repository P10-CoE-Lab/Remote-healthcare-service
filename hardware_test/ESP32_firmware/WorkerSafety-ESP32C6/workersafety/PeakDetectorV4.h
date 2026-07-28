#pragma once

#include <Arduino.h>
#include "SignalAnalyzer.h"

class PeakDetectorV4
{
public:
    void reset();

    bool update(float sample, const SignalStats &stats);

    unsigned long getLastRR() const
    {
        return lastRR;
    }

private:
    bool rising = false;

    float previous = 0;
    float peakValue = 0;

    unsigned long lastBeatTime = 0;
    unsigned long lastRR = 0;
};