#ifndef CIRCULARBUFFER_H
#define CIRCULARBUFFER_H

#include <Arduino.h>

class CircularBuffer
{
public:
    CircularBuffer();

    void clear();

    void push(uint32_t value);

    uint32_t get(uint16_t index) const;

    uint16_t size() const;

    bool isFull() const;

    float average() const;

private:
    static const uint16_t BUFFER_SIZE = 200;

    uint32_t buffer[BUFFER_SIZE];

    uint16_t head;
    uint16_t count;
};

#endif