#include "WorkerSafetyManager.h"
#include "WiFiManager.h"
#include "MQTTManager.h"
#include "MQTTConfig.h"

WorkerSafetyManager worker;
WiFiManager wifi;
MQTTManager mqtt;

void setup()
{
    Serial.begin(115200);

    // ESP32-C6 uses native USB Serial/JTAG (no separate USB-UART bridge
    // chip) — without this, Serial.print() blocks indefinitely waiting
    // for a host to read the USB CDC buffer, so the whole boot sequence
    // (WiFi, MQTT, everything) silently stalls until a Serial Monitor
    // is opened. This makes prints non-blocking: bytes are dropped if
    // nobody's listening, instead of stalling the device.
    Serial.setTxTimeoutMs(0);

    worker.begin();

    #ifdef ENABLE_WIFI
        wifi.begin(
            WIFI_SSID,
            WIFI_PASSWORD);

        mqtt.begin();
    #endif
}

// void loop()
// {
//     // worker.update();

//     // #ifdef ENABLE_WIFI
//     //     wifi.update();

//     //     if (wifi.isConnected())
//     //     {
//     //         mqtt.update();

//     //         static unsigned long lastPublish = 0;

//     //         if (millis() - lastPublish > 1000)
//     //         {
//     //             lastPublish = millis();

//     //             mqtt.publishStatus(worker.getStatus());
//     //         }
//     //     }
//     // #endif

//     // delay(20);
    
// }
void loop()
{
    worker.update();

    #ifdef ENABLE_WIFI
        wifi.update();

        if (wifi.isConnected())
        {
            mqtt.update();

            static unsigned long lastPublish = 0;

            if (millis() - lastPublish > 1000)
            {
                lastPublish = millis();
                mqtt.publishStatus(worker.getStatus());
                mqtt.publishCanonicalSensors(worker.getStatus());
            }
        }
    #endif

    delay(20);
}