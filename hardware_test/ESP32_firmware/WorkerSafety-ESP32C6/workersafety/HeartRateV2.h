#ifndef HEARTRATEV2_H
#define HEARTRATEV2_H

#include <Arduino.h>

class HeartRateV2
{
public:

    HeartRateV2();

    void reset();

    void beatDetected(unsigned long rr);

    bool isValid() const;

    float getBPM() const;

    // HRV (RMSSD, ms) over a longer rolling window than the BPM
    // smoothing buffer uses — kept separate so BPM responsiveness is
    // unaffected by HRV's need for more beats to be meaningful.
    float getHRV() const;

private:

    static const uint8_t BUFFER_SIZE = 5;

    unsigned long rrIntervals[BUFFER_SIZE];

    uint8_t index;

    uint8_t count;

    float bpm;

    bool valid;

    float computeMedianBPM();

    // ---- HRV: dedicated ~30-beat RR-interval history ----
    // Same head/count circular-buffer pattern as CircularBuffer.cpp,
    // sized for RR intervals (~1/sec) rather than raw 100Hz samples.
    static const uint8_t HRV_BUFFER_SIZE = 30;

    unsigned long hrvIntervals[HRV_BUFFER_SIZE];

    uint8_t hrvHead;

    uint8_t hrvCount;

    float hrv;

    float computeRMSSD();
};

#endif