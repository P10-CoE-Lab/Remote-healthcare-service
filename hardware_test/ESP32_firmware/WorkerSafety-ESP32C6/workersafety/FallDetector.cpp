#include "FallDetector.h"

FallDetector::FallDetector()
{
    begin();
}

void FallDetector::begin()
{
    state = IDLE;
    stateStartTime = 0;
    fallDetected = false;
}

void FallDetector::reset()
{
    state = IDLE;
    fallDetected = false;
    stateStartTime = 0;
}

bool FallDetector::isFallDetected() const
{
    return fallDetected;
}

FallDetector::State FallDetector::getState() const
{
    return state;
}

void FallDetector::update(float magnitude,
                          float orientationAngle,
                          float jerk)
{
    unsigned long now = millis();

    switch (state)
    {
        case IDLE:
            if (magnitude > IMPACT_THRESHOLD &&
                jerk > IMPACT_JERK_THRESHOLD)
            {
                Serial.println("******** IMPACT DETECTED ********");

                state = IMPACT_WAITING;
                stateStartTime = now;
            }

            break;

        case IMPACT_WAITING:

            if ((now - stateStartTime) > IMPACT_TIMEOUT)
            {
                Serial.println("Impact Timeout");
                reset();
            }
            else if (magnitude >= STILL_MIN_MAGNITUDE &&
                     magnitude <= STILL_MAX_MAGNITUDE &&
                     orientationAngle > ORIENTATION_THRESHOLD)
            {
                Serial.println("Entering INACTIVITY");

                state = INACTIVITY;
                stateStartTime = now;
            }

            break;

        case INACTIVITY:

            if (jerk > STILL_JERK_THRESHOLD)
            {
                Serial.println("Movement detected");
                stateStartTime = now;
            }
            else
            {
                unsigned long elapsed = now - stateStartTime;
                
                if (elapsed >= STILL_TIME)
                {
                    Serial.println("******** FALL DETECTED ********");

                    fallDetected = true;
                    state = FALL_DETECTED;
                }
            }

            break;

        case FALL_DETECTED:

            Serial.println("FALL DETECTED - Waiting for movement");

            if (jerk > STILL_JERK_THRESHOLD)
            {
                Serial.println("Movement detected - Resetting");
                reset();
            }

            break;
    }
}