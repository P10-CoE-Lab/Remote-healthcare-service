#include "FloatCircularBuffer.h"

FloatCircularBuffer::FloatCircularBuffer()
{
    clear();
}

void FloatCircularBuffer::clear()
{
    head = 0;
    count = 0;

    for(int i = 0; i < BUFFER_SIZE; i++)
        buffer[i] = 0.0f;
}

void FloatCircularBuffer::push(float value)
{
    buffer[head] = value;

    head = (head + 1) % BUFFER_SIZE;

    if(count < BUFFER_SIZE)
        count++;
}

float FloatCircularBuffer::get(uint16_t index) const
{
    if(index >= count)
        return 0;

    uint16_t pos = (head + BUFFER_SIZE - count + index) % BUFFER_SIZE;

    return buffer[pos];
}

float FloatCircularBuffer::latest() const
{
    if(count == 0)
        return 0;

    uint16_t pos = (head + BUFFER_SIZE - 1) % BUFFER_SIZE;

    return buffer[pos];
}

float FloatCircularBuffer::oldest() const
{
    if(count == 0)
        return 0;

    return get(0);
}

uint16_t FloatCircularBuffer::size() const
{
    return count;
}

bool FloatCircularBuffer::isFull() const
{
    return count == BUFFER_SIZE;
}