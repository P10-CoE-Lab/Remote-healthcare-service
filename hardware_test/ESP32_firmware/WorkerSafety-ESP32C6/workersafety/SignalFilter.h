#ifndef SIGNALFILTER_H
#define SIGNALFILTER_H

#include <Arduino.h>

class SignalFilter
{
public:
    SignalFilter();

    void reset();

    float process(uint32_t sample);

    float getDC() const;

private:

    static const uint8_t WINDOW_SIZE = 32;

    uint32_t samples[WINDOW_SIZE];

    uint8_t index;

    bool initialized;

    uint64_t runningSum;

    float dc;
};

#endif