#include "SignalFilter.h"

SignalFilter::SignalFilter()
{
    reset();
}

void SignalFilter::reset()
{
    initialized = false;

    index = 0;

    runningSum = 0;

    dc = 0;

    for(int i = 0; i < WINDOW_SIZE; i++)
        samples[i] = 0;
}

float SignalFilter::process(uint32_t sample)
{
    //--------------------------------------------------
    // Fill buffer first
    //--------------------------------------------------

    if(!initialized)
    {
        for(int i=0;i<WINDOW_SIZE;i++)
        {
            samples[i]=sample;
        }

        runningSum = sample * WINDOW_SIZE;

        dc = sample;

        initialized = true;

        return 0;
    }

    //--------------------------------------------------
    // Running Average
    //--------------------------------------------------

    runningSum -= samples[index];

    samples[index] = sample;

    runningSum += sample;

    index++;

    if(index >= WINDOW_SIZE)
        index = 0;

    dc = (float)runningSum / WINDOW_SIZE;

    //--------------------------------------------------
    // AC Component
    //--------------------------------------------------

    int32_t ac = (int32_t)sample - (int32_t)dc;
    return (float)ac;
}

float SignalFilter::getDC() const
{
    return dc;
}