#pragma once

class MotionAnalyzer
{
public:
    void reset();
    float update(float magnitude);

    float getFiltered() const;
    float getJerk() const;

private:
    float filtered = 1.0f;
    float previous = 1.0f;
    float jerk = 0.0f;

    static constexpr float ALPHA = 0.2f;
};