#include "MotionAnalyzer.h"
#include <math.h>

void MotionAnalyzer::reset()
{
    filtered = 1.0f;
    previous = 1.0f;
    jerk = 0.0f;
}

float MotionAnalyzer::update(float mag)
{
    // Low-pass filter acceleration
    filtered = filtered + ALPHA * (mag - filtered);

    const float dt = 0.01f;   // 100 Hz

    // Calculate raw jerk
    float rawJerk = fabs(filtered - previous) / dt;

    // Smooth the jerk signal
    jerk = 0.85f * jerk + 0.15f * rawJerk;

    // Save filtered value
    previous = filtered;

    return filtered;
}

float MotionAnalyzer::getFiltered() const
{
    return filtered;
}

float MotionAnalyzer::getJerk() const
{
    return jerk;
}