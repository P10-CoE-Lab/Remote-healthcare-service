#include "BLEScanner.h"
#include "Config.h"
#include <math.h>

bool BLEScanner::begin()
{
    NimBLEDevice::init("");

    scanner = NimBLEDevice::getScan();
    scanner->setActiveScan(true);
    scanner->setInterval(100);
    scanner->setWindow(80);

    return true;
}

void BLEScanner::update()
{
    nearestAnchorID = 0;
    nearestRSSI = -127;
    nearestDistance = -1.0f;

    NimBLEScanResults results = scanner->getResults(50);

    Serial.print("Devices Found: ");
    Serial.println(results.getCount());

    for (int i = 0; i < results.getCount(); i++)
    {
        const NimBLEAdvertisedDevice* device = results.getDevice(i);

        // Must advertise our service
        if (!device->haveServiceUUID())
            continue;

        if (!device->isAdvertisingService(NimBLEUUID(ANCHOR_SERVICE_UUID)))
            continue;

        // Read Anchor ID from Service Data
        std::string serviceData =
            device->getServiceData(NimBLEUUID(ANCHOR_SERVICE_UUID));

        if (serviceData.length() != 1)
            continue;

        uint8_t anchorID = (uint8_t)serviceData[0];

        if (device->getRSSI() > nearestRSSI)
        {
            nearestRSSI = device->getRSSI();
            nearestDistance = calculateDistance(nearestRSSI);

            // Store the ID as a string
            nearestAnchorID = anchorID;
        }
    }

    scanner->clearResults();
}

float BLEScanner::calculateDistance(int rssi)
{
    return pow(10.0,
               (TX_POWER - rssi) /
               (10.0f * PATH_LOSS));
}

bool BLEScanner::isAnchorFound() const
{
    return nearestRSSI != -127;
}

uint8_t BLEScanner::getNearestAnchorID() const
{
    return nearestAnchorID;
}

int BLEScanner::getRSSI() const
{
    return nearestRSSI;
}

float BLEScanner::getDistance() const
{
    return nearestDistance;
}