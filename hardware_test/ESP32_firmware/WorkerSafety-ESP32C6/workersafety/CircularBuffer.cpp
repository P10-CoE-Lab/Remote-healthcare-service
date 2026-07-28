#include "CircularBuffer.h"

CircularBuffer::CircularBuffer()
{
    clear();
}

void CircularBuffer::clear()
{
    head = 0;
    count = 0;

    for (int i = 0; i < BUFFER_SIZE; i++)
        buffer[i] = 0;
}

void CircularBuffer::push(uint32_t value)
{
    buffer[head] = value;

    head++;

    if (head >= BUFFER_SIZE)
        head = 0;

    if (count < BUFFER_SIZE)
        count++;
}

uint16_t CircularBuffer::size() const
{
    return count;
}

bool CircularBuffer::isFull() const
{
    return count == BUFFER_SIZE;
}

uint32_t CircularBuffer::get(uint16_t index) const
{
    if (index >= count)
        return 0;

    int pos = head - count + index;

    if (pos < 0)
        pos += BUFFER_SIZE;

    return buffer[pos];
}

float CircularBuffer::average() const
{
    if (count == 0)
        return 0;

    uint64_t sum = 0;

    for (int i = 0; i < count; i++)
        sum += get(i);

    return (float)sum / count;
}