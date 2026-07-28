#include "WiFiManager.h"

bool WiFiManager::begin(const char* ssid, const char* password)
{
    wifiSSID = ssid;
    wifiPassword = password;

    Serial.println("===== WiFi Debug =====");
    Serial.print("SSID: ");
    Serial.println(wifiSSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(wifiSSID, wifiPassword);

    Serial.print("Connecting");

    unsigned long start = millis();

    while (WiFi.status() != WL_CONNECTED)
    {
        delay(500);

        Serial.print(".");

        Serial.print(" Status=");
        Serial.println(WiFi.status());

        if (millis() - start > 15000)
        {
            Serial.println("\nConnection failed");
            return false;
        }
    }

    Serial.println();
    Serial.print("Connected IP: ");
    Serial.println(WiFi.localIP());

    return true;
}

void WiFiManager::update()
{
    if (WiFi.status() == WL_CONNECTED)
        return;

    if (millis() - lastReconnect < 5000)
        return;

    lastReconnect = millis();

    Serial.println("Reconnecting WiFi...");

    WiFi.disconnect();
    WiFi.begin(wifiSSID, wifiPassword);
}

bool WiFiManager::isConnected() const
{
    return WiFi.status() == WL_CONNECTED;
}

IPAddress WiFiManager::getIP() const
{
    return WiFi.localIP();
}