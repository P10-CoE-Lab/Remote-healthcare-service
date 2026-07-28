#include <Arduino.h>
#include <NimBLEDevice.h>

#define ANCHOR_ID 3
#define ANCHOR_SERVICE_UUID ((uint16_t)0xFFF0)

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println("Starting Anchor...");

    NimBLEDevice::init("");

    NimBLEServer *pServer = NimBLEDevice::createServer();

    NimBLEService *pService =
        pServer->createService(NimBLEUUID((uint16_t)ANCHOR_SERVICE_UUID));

    pService->start();

    NimBLEAdvertising *pAdvertising =
        NimBLEDevice::getAdvertising();

    NimBLEAdvertisementData advData;

    // Advertise 16-bit UUID
    advData.addServiceUUID(NimBLEUUID((uint16_t)ANCHOR_SERVICE_UUID));

    // 1-byte Anchor ID
    std::string serviceData;
    serviceData.push_back((char)ANCHOR_ID);

    bool ok = advData.setServiceData(
        NimBLEUUID((uint16_t)ANCHOR_SERVICE_UUID),
        serviceData);

    Serial.print("setServiceData = ");
    Serial.println(ok ? "SUCCESS" : "FAILED");

    bool ok2 = pAdvertising->setAdvertisementData(advData);

    Serial.print("setAdvertisementData = ");
    Serial.println(ok2 ? "SUCCESS" : "FAILED");

    pAdvertising->start();

    Serial.print("Advertising Anchor ID: ");
    Serial.println(ANCHOR_ID);
}

void loop()
{
    delay(1000);
}