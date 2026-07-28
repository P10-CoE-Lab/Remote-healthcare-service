#include "HeartRateV2.h"

HeartRateV2::HeartRateV2()
{
    reset();
}

void HeartRateV2::reset()
{
    index = 0;
    count = 0;
    bpm = 0.0f;

    valid = false;

    for (uint8_t i = 0; i < BUFFER_SIZE; i++)
    {
        rrIntervals[i] = 0;
    }

    hrvHead = 0;
    hrvCount = 0;
    hrv = 0.0f;

    for (uint8_t i = 0; i < HRV_BUFFER_SIZE; i++)
    {
        hrvIntervals[i] = 0;
    }
}

void HeartRateV2::beatDetected(unsigned long rr)
{
    //--------------------------------------------------
    // Reject impossible HR
    //--------------------------------------------------

    if (rr < 500 || rr > 1500)
        return;

    //--------------------------------------------------
    // Reject outliers
    //--------------------------------------------------

    if (count >= 3)
    {
        unsigned long sum = 0;

        for (int i = 0; i < count; i++)
            sum += rrIntervals[i];

        unsigned long avgRR = sum / count;

        // Ignore RR that differs by >20%
        if (abs((long)rr - (long)avgRR) > (avgRR * 0.20f))
            return;
    }

    //--------------------------------------------------
    // Store RR
    //--------------------------------------------------

    rrIntervals[index] = rr;

    index++;

    if (index >= BUFFER_SIZE)
        index = 0;

    if (count < BUFFER_SIZE)
        count++;

    //--------------------------------------------------
    // Compute BPM using median
    //--------------------------------------------------

    bpm = computeMedianBPM();

    valid = (bpm > 0);

    //--------------------------------------------------
    // Store RR for HRV (same accepted beat, longer window)
    //--------------------------------------------------

    hrvIntervals[hrvHead] = rr;

    hrvHead++;

    if (hrvHead >= HRV_BUFFER_SIZE)
        hrvHead = 0;

    if (hrvCount < HRV_BUFFER_SIZE)
        hrvCount++;

    hrv = computeRMSSD();
}

float HeartRateV2::computeMedianBPM()
{
    if (count == 0)
        return 0.0f;

    //--------------------------------------------------
    // Copy RR values
    //--------------------------------------------------

    unsigned long temp[BUFFER_SIZE];

    for (uint8_t i = 0; i < count; i++)
    {
        temp[i] = rrIntervals[i];
    }

    //--------------------------------------------------
    // Simple bubble sort
    //--------------------------------------------------

    for (uint8_t i = 0; i < count - 1; i++)
    {
        for (uint8_t j = i + 1; j < count; j++)
        {
            if (temp[j] < temp[i])
            {
                unsigned long t = temp[i];
                temp[i] = temp[j];
                temp[j] = t;
            }
        }
    }

    //--------------------------------------------------
    // Median RR
    //--------------------------------------------------

    unsigned long medianRR;

    if (count & 1)
    {
        medianRR = temp[count / 2];
    }
    else
    {
        medianRR =
            (temp[count / 2] +
             temp[count / 2 - 1]) / 2;
    }

    return 60000.0f / medianRR;
}

float HeartRateV2::getBPM() const
{
    return bpm;
}

bool HeartRateV2::isValid() const
{
    return valid;
}

float HeartRateV2::getHRV() const
{
    return hrv;
}

float HeartRateV2::computeRMSSD()
{
    // RMSSD needs at least 2 intervals to form 1 successive difference.
    if (hrvCount < 2)
        return 0.0f;

    //--------------------------------------------------
    // Walk the circular buffer oldest -> newest.
    // Oldest is at hrvHead once full (about to be overwritten next);
    // if not yet full, oldest is simply index 0.
    //--------------------------------------------------

    uint8_t start = (hrvCount < HRV_BUFFER_SIZE) ? 0 : hrvHead;

    float sumSquares = 0.0f;
    uint8_t diffs = hrvCount - 1;

    unsigned long prev = hrvIntervals[start];

    for (uint8_t i = 1; i < hrvCount; i++)
    {
        unsigned long curr = hrvIntervals[(start + i) % HRV_BUFFER_SIZE];

        float d = (float)curr - (float)prev;
        sumSquares += d * d;

        prev = curr;
    }

    return sqrt(sumSquares / diffs);
}