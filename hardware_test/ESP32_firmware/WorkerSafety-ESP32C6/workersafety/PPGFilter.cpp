#include "PPGFilter.h"

PPGFilter::PPGFilter()
{
    reset();
}

void PPGFilter::reset()
{
    initialized = false;

    dcEstimate = 0.0f;
    filteredValue = 0.0f;

    windowIndex = 0;

    for (int i = 0; i < WINDOW_SIZE; i++)
        window[i] = 0.0f;
}

float PPGFilter::getDCLevel()
{
    return dcEstimate;
}

float PPGFilter::update(uint32_t sample)
{
    // Initialize filter with first sample
    if (!initialized)
    {
        initialized = true;

        dcEstimate = sample;

        filteredValue = 0;

        for (int i = 0; i < WINDOW_SIZE; i++)
            window[i] = 0;

        return 0;
    }

    // DC Removal
    const float alphaDC = 0.95f;

    dcEstimate =
        alphaDC * dcEstimate +
        (1.0f - alphaDC) * sample;

    float ac = sample - dcEstimate;

    // Moving Average
    window[windowIndex] = ac;

    windowIndex++;

    if (windowIndex >= WINDOW_SIZE)
        windowIndex = 0;

    float average = 0;

    for (int i = 0; i < WINDOW_SIZE; i++)
        average += window[i];

    average /= WINDOW_SIZE;

    // Low-pass filter
    const float alphaLP = 0.20f;

    filteredValue =
        alphaLP * average +
        (1.0f - alphaLP) * filteredValue;

    return filteredValue;
}