#pragma once

class OrientationDetector
{
public:
    void reset();

    void update(float x, float y, float z);

    void captureReference();

    float getAngle() const;

    bool hasOrientationChanged() const;

private:
    float refX = 0;
    float refY = 0;
    float refZ = 1;

    float curX = 0;
    float curY = 0;
    float curZ = 1;

    float angle = 0;

    static constexpr float CHANGE_THRESHOLD = 45.0f;

    void calculateAngle();
};