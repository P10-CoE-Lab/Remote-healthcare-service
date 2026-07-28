#ifndef ACTIVITY_DETECTOR_H
#define ACTIVITY_DETECTOR_H

#include <Arduino.h>

class ActivityDetector
{
public:

    enum Activity
    {
        STANDING,
        WALKING,
        RUNNING
    };

    ActivityDetector();

    void begin();

    void update(float magnitude, float jerk);

    Activity getActivity() const;

    const char* getActivityName() const;

private:

    Activity activity;

    unsigned long lastStepTime;
    uint8_t stepCounter;

    float previousMagnitude;

    static constexpr float WALK_MIN = 0.50f;
    static constexpr float RUN_MIN  = 4.50f;

    static constexpr float STEP_THRESHOLD = 1.15f;

};

#endif