#include <Arduino.h>
#include "OrientationDetector.h"
#include <math.h>

void OrientationDetector::reset()
{
    refX = 0;
    refY = 0;
    refZ = 1;

    curX = 0;
    curY = 0;
    curZ = 1;

    angle = 0;
}

void OrientationDetector::update(float x, float y, float z)
{
    curX = x;
    curY = y;
    curZ = z;

    calculateAngle();
}

void OrientationDetector::captureReference()
{
    refX = curX;
    refY = curY;
    refZ = curZ;
}

void OrientationDetector::calculateAngle()
{
    float dot =
        refX * curX +
        refY * curY +
        refZ * curZ;

    float magRef =
        sqrt(refX * refX +
             refY * refY +
             refZ * refZ);

    float magCur =
        sqrt(curX * curX +
             curY * curY +
             curZ * curZ);

    if (magRef == 0 || magCur == 0)
        return;

    float cosTheta = dot / (magRef * magCur);

    if (cosTheta > 1.0f) cosTheta = 1.0f;
    if (cosTheta < -1.0f) cosTheta = -1.0f;

    angle = acos(cosTheta) * 180.0f / PI;
}

float OrientationDetector::getAngle() const
{
    return angle;
}

bool OrientationDetector::hasOrientationChanged() const
{
    return angle > CHANGE_THRESHOLD;
}