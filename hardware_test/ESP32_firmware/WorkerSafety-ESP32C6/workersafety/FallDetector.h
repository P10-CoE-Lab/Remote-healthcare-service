#ifndef FALL_DETECTOR_H
#define FALL_DETECTOR_H

#include <Arduino.h>

class FallDetector
{
public:
    enum State
    {
        IDLE,
        IMPACT_WAITING,
        INACTIVITY,
        FALL_DETECTED
    };

    FallDetector();

    void begin();

    void update(float rawMagnitude,
                float orientationAngle,
                float jerk);

    bool isFallDetected() const;
    State getState() const;
    void reset();

private:
    State state;

    unsigned long stateStartTime;

    bool fallDetected;

    static constexpr float IMPACT_THRESHOLD       = 2.2f;
    static constexpr float IMPACT_JERK_THRESHOLD  = 3.0f;

    static constexpr float ORIENTATION_THRESHOLD  = 70.0f;

    static constexpr float STILL_MIN_MAGNITUDE    = 0.85f;
    static constexpr float STILL_MAX_MAGNITUDE    = 1.15f;

    static constexpr float STILL_JERK_THRESHOLD   = 0.8f;

    static constexpr uint32_t IMPACT_TIMEOUT      = 5000;
    static constexpr uint32_t STILL_TIME          = 2500;
};

#endif