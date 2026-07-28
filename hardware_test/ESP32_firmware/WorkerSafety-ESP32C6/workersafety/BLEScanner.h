#pragma once

#include <Arduino.h>
#include <NimBLEDevice.h>

class BLEScanner
{
public:
    bool begin();
    void update();

    bool isAnchorFound() const;

    uint8_t getNearestAnchorID() const;
    int getRSSI() const;
    float getDistance() const;

private:
    NimBLEScan* scanner = nullptr;

    uint8_t nearestAnchorID = 0;
    int nearestRSSI = -127;
    float nearestDistance = -1.0f;

    float calculateDistance(int rssi);
};