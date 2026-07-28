#ifndef PPGFILTER_H
#define PPGFILTER_H

#include <Arduino.h>

class PPGFilter
{
public:
    PPGFilter();

    void reset();

    float update(uint32_t sample);

    float getDCLevel();

private:
    static const uint8_t WINDOW_SIZE = 5;

    bool initialized;

    float dcEstimate;
    float filteredValue;

    float window[WINDOW_SIZE];
    uint8_t windowIndex;
};

#endif