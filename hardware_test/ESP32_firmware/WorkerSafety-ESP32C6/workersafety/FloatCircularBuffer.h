#ifndef FLOATCIRCULARBUFFER_H
#define FLOATCIRCULARBUFFER_H

#include <Arduino.h>

class FloatCircularBuffer
{
public:
    static const uint16_t BUFFER_SIZE = 100;

    FloatCircularBuffer();

    void clear();

    void push(float value);

    float get(uint16_t index) const;

    float latest() const;

    float oldest() const;

    uint16_t size() const;

    bool isFull() const;

private:
    float buffer[BUFFER_SIZE];

    uint16_t head;
    uint16_t count;
};

#endif