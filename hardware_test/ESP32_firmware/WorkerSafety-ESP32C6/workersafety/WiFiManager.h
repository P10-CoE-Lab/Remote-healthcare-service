#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <WiFi.h>

class WiFiManager
{
public:
    bool begin(const char* ssid, const char* password);

    void update();

    bool isConnected() const;

    IPAddress getIP() const;

private:
    const char* wifiSSID = nullptr;
    const char* wifiPassword = nullptr;

    unsigned long lastReconnect = 0;
};

#endif