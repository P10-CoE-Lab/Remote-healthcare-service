#include "PeakDetectorV4.h"

#define MIN_PROMINENCE   180.0f
#define MIN_RR           500
#define MAX_RR          1500

void PeakDetectorV4::reset()
{
    rising = false;

    previous = 0;
    peakValue = 0;

    lastBeatTime = 0;
    lastRR = 0;
}

bool PeakDetectorV4::update(float sample, const SignalStats &stats)
{
    unsigned long now = millis();    

    //------------------------------------
    // Ignore weak signals
    //------------------------------------

    if (stats.quality < 60)
    {
        Serial.print("Rejected: Quality=");
        Serial.println(stats.quality);
        return false;
    }

    if (stats.acAmplitude < 80)
    {
        Serial.print("Rejected: AC=");
        Serial.println(stats.acAmplitude);
        return false;
    }

    //------------------------------------
    // Rising edge
    //------------------------------------

    if (sample > previous)
    {
        rising = true;

        if (sample > peakValue)
            peakValue = sample;
    }

    //------------------------------------
    // Falling edge -> peak detected
    //------------------------------------

    else if (rising)
    {
        rising = false;

        float prominence = peakValue - sample;

        peakValue = 0;

        if (prominence < MIN_PROMINENCE)
        {
            previous = sample;
            return false;
        }

        if (lastBeatTime == 0)
        {
            lastBeatTime = now;
            previous = sample;
            return false;
        }

        unsigned long rr = now - lastBeatTime;

        if (rr < MIN_RR || rr > MAX_RR)
        {
            previous = sample;
            return false;
        }

        lastRR = rr;
        lastBeatTime = now;

        previous = sample;

        return true;
    }

    previous = sample;

    return false;
}