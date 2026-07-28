#include "ActivityDetector.h"

ActivityDetector::ActivityDetector()
{
    begin();
}

void ActivityDetector::begin()
{
    activity = STANDING;
    previousMagnitude = 1.0f;
    lastStepTime = 0;
    stepCounter = 0;
}

void ActivityDetector::update(float magnitude, float jerk)
{
    unsigned long now = millis();

    //------------------------------------------------
    // Detect a step
    //------------------------------------------------

    if(previousMagnitude < STEP_THRESHOLD &&
       magnitude >= STEP_THRESHOLD)
    {
        stepCounter++;
        lastStepTime = now;
    }

    previousMagnitude = magnitude;

    if (jerk < WALK_MIN)
    {
        activity = STANDING;
    }
    else if (jerk < RUN_MIN)
    {
        activity = WALKING;
    }
    else
    {
        activity = RUNNING;
    }
}

ActivityDetector::Activity ActivityDetector::getActivity() const
{
    return activity;
}

const char* ActivityDetector::getActivityName() const
{
    switch(activity)
    {
        case STANDING:
            return "STANDING";

        case WALKING:
            return "WALKING";

        case RUNNING:
            return "RUNNING";

        default:
            return "UNKNOWN";
    }
}