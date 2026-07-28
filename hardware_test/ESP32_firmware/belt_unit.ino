/**
 * Belt Unit Firmware — Seeed XIAO ESP32S3
 * Hardware: BNO055 IMU + MAX30102 HR/SpO2 + SSD1306 OLED + WiFi/MQTT
 *
 * Wiring (XIAO ESP32S3):
 *   I2C SDA  → D4
 *   I2C SCL  → D5
 *   Button   → D1  (to GND, internal pull-up)
 *   Buzzer   → D2  (active-high)
 *
 * I2C addresses:  BNO055 0x28 (0x29 fallback) | MAX30102 0x57 | OLED 0x3C
 *
 * Libraries (Arduino Library Manager):
 *   Adafruit BNO055, Adafruit Unified Sensor,
 *   Adafruit SSD1306, Adafruit GFX,
 *   SparkFun MAX3010x, PubSubClient, ArduinoJson
 */

#include <Wire.h>
#include "driver/rtc_io.h"
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BNO055.h>
#include <utility/imumaths.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ─── Board pins (XIAO ESP32S3) ────────────────────────────────────────────────
#define SDA_PIN       D4
#define SCL_PIN       D5
#define BUTTON_PIN      D1
#define BUZZER_PIN      D2
#define BUTTON_GPIO_NUM GPIO_NUM_2

// ─── OLED ─────────────────────────────────────────────────────────────────────
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 32
#define OLED_RESET    -1
#define OLED_ADDRESS  0x3C

// ─── Network ──────────────────────────────────────────────────────────────────
// Topic pattern: {MQTT_PREFIX}/{MQTT_POC_TYPE}/{DEVICE_ID}/{sensor_name}
#define DEVICE_ID         "belt-unit-01"
#define MQTT_PREFIX       "safety"
#define MQTT_POC_TYPE     "belt"
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_BROKER   = "192.168.0.156";
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";
const char* MQTT_PASS     = "";

// ─── Sensor mount offset ─────────────────────────────────────────────────────
// If posture reads inverted, change to -90.0f.
#define MOUNT_PITCH_OFFSET  90.0f

// ─── Detection thresholds ─────────────────────────────────────────────────────
#define POSTURE_PITCH_THRESHOLD   45.0f   // degrees forward/back bend
#define POSTURE_ROLL_THRESHOLD    30.0f   // degrees lateral lean
// Raw accelerometer includes gravity (~9.81 m/s²); 29.4 m/s² = 3g total
#define FALL_ACCEL_THRESHOLD      29.4f   // m/s²
#define ACTIVITY_IDLE_MS          5000    // ms still before "idle" triggers
#define ACTIVITY_IDLE_ACCEL       10.5f   // m/s² upper bound for "near-still"

// ─── Timing ───────────────────────────────────────────────────────────────────
#define IMU_INTERVAL        100   // ms  (10 Hz)
#define MQTT_INTERVAL       500   // ms
#define DISPLAY_INTERVAL    150   // ms

// ─── Button press durations ───────────────────────────────────────────────────
#define SHORT_PRESS_MAX_MS  800   // below this = short press → cycle screen
#define LONG_PRESS_MS      2000   // hold this long → power off (deep sleep)

// ─── Speaker tones (Hz) ───────────────────────────────────────────────────────
#define TONE_BOOT       1000
#define TONE_FALL        400
#define TONE_POSTURE     800
#define TONE_POWEROFF    600

// ─── MAX30102 ─────────────────────────────────────────────────────────────────
#define HR_BUFFER_LEN  100

// ─── Screen IDs ───────────────────────────────────────────────────────────────
#define SCREEN_IMU    0
#define SCREEN_HR     1
#define SCREEN_STATUS 2
#define SCREEN_COUNT  3

// ─── Objects ──────────────────────────────────────────────────────────────────
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
Adafruit_BNO055  bno(55, 0x28);
MAX30105         particleSensor;

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ─── State ────────────────────────────────────────────────────────────────────
struct ImuState {
  float pitch, roll, yaw;       // pitch/roll already offset-corrected
  float accelMag;               // raw accel magnitude m/s²
  uint8_t calSys, calGyro, calAccel, calMag;
  bool postureRisk;
  bool fallDetected;
  bool isIdle;
  unsigned long idleStartMs;
};

struct HrState {
  int32_t heartRate;
  int32_t spo2;
  int8_t  hrValid;
  int8_t  spo2Valid;
  bool    collecting;
};

ImuState imuState = {};
HrState  hrState  = {};

// ─── UI ───────────────────────────────────────────────────────────────────────
int currentScreen = SCREEN_IMU;

// Button polling state (no ISR needed — polling handles both short and long press)
unsigned long btnPressStart  = 0;
bool          btnWasPressed  = false;
bool          longPressFired = false;  // prevents repeat fire while held
bool          blockDisplay   = false;  // true while showing power-off progress

uint32_t irBuffer[HR_BUFFER_LEN];
uint32_t redBuffer[HR_BUFFER_LEN];
bool     max30102Found = false;

unsigned long lastImuMs     = 0;
unsigned long lastMqttMs    = 0;
unsigned long lastDisplayMs = 0;

// ─── Power off ────────────────────────────────────────────────────────────────
void enterDeepSleep() {
  Serial.println("Entering deep sleep — press button to wake");
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(20, 8);
  display.println("Powering off...");
  display.setCursor(16, 20);
  display.println("Hold btn to wake");
  display.display();
  delay(1500);
  beep(TONE_POWEROFF, 150); delay(60); beep(TONE_POWEROFF - 200, 250);
  display.ssd1306_command(SSD1306_DISPLAYOFF);  // blank OLED
  mqtt.disconnect();
  WiFi.disconnect(true);
  delay(100);
  // Wake when button is pressed LOW
  // Wait until button is fully released
  while (digitalRead(BUTTON_PIN) == LOW) delay(10);
  delay(300);

  // The regular GPIO pull-up is disabled during deep sleep, causing the pin to
  // float LOW and wake the chip immediately. The RTC pull-up persists in sleep.
  rtc_gpio_init(BUTTON_GPIO_NUM);
  rtc_gpio_set_direction(BUTTON_GPIO_NUM, RTC_GPIO_MODE_INPUT_ONLY);
  rtc_gpio_pullup_en(BUTTON_GPIO_NUM);
  rtc_gpio_pulldown_dis(BUTTON_GPIO_NUM);

  esp_sleep_enable_ext0_wakeup(BUTTON_GPIO_NUM, 0);
  esp_deep_sleep_start();
}

// ─── Button polling (short press = cycle screen, long press = sleep) ──────────
void handleButton() {
  bool pressed = (digitalRead(BUTTON_PIN) == LOW);

  if (pressed && !btnWasPressed) {
    // Falling edge — button just pressed
    btnPressStart  = millis();
    btnWasPressed  = true;
    longPressFired = false;
  }

  if (pressed && btnWasPressed && !longPressFired) {
    unsigned long held = millis() - btnPressStart;
    if (held >= LONG_PRESS_MS) {
      longPressFired = true;
      blockDisplay   = false;
      enterDeepSleep();   // does not return
    }
    // Once held past 400ms, take over the display to show progress
    if (held > 400) {
      blockDisplay = true;
      int filled = map(held, 400, LONG_PRESS_MS, 0, 128);  // progress bar width
      display.clearDisplay();
      display.setTextSize(1);
      display.setCursor(16, 6);
      display.print("Hold to power off");
      display.drawRect(0, 20, 128, 10, SSD1306_WHITE);     // bar outline
      display.fillRect(0, 20, filled, 10, SSD1306_WHITE);  // fill
      display.display();
    }
  }

  if (!pressed && btnWasPressed) {
    // Rising edge — button released
    unsigned long held = millis() - btnPressStart;
    if (held < SHORT_PRESS_MAX_MS && !longPressFired) {
      currentScreen = (currentScreen + 1) % SCREEN_COUNT;
    }
    btnWasPressed  = false;
    longPressFired = false;
    blockDisplay   = false;  // restore normal display
  }
}

// ─── Speaker ──────────────────────────────────────────────────────────────────
void beep(int freqHz, int durationMs) {
  tone(BUZZER_PIN, freqHz, durationMs);
  delay(durationMs);
  noTone(BUZZER_PIN);
}

// Normalise angle to -180..+180
float normalizeAngle(float a) {
  while (a >  180.0f) a -= 360.0f;
  while (a < -180.0f) a += 360.0f;
  return a;
}

// ─── WiFi / MQTT ──────────────────────────────────────────────────────────────
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi connecting");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("WiFi OK: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("WiFi failed — will retry in loop");
}

void reconnectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  static unsigned long lastWifiAttemptMs = 0;
  if (millis() - lastWifiAttemptMs < 10000) return;  // retry every 10 s
  lastWifiAttemptMs = millis();
  Serial.printf("[WiFi] Not connected (status=%d), retrying SSID: %s\n",
                WiFi.status(), WIFI_SSID);
  WiFi.disconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void reconnectMQTT() {
  // Print WiFi + MQTT status every 5 s regardless, so we can diagnose from Serial
  static unsigned long lastAttemptMs = 0;
  if (millis() - lastAttemptMs < 5000) return;
  lastAttemptMs = millis();

  Serial.printf("[NET] WiFi=%s(%d) IP=%s MQTT=%s\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "NO",
    WiFi.status(),
    WiFi.localIP().toString().c_str(),
    mqtt.connected() ? "OK" : "NO");

  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;

  Serial.printf("[MQTT] Connecting to %s:%d ... ", MQTT_BROKER, MQTT_PORT);
  bool ok = strlen(MQTT_USER) > 0
    ? mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)
    : mqtt.connect(DEVICE_ID);

  if (ok) {
    Serial.println("OK");
  } else {
    // -4=timeout -3=lost -2=failed -1=disconnected
    //  1=bad protocol 2=bad clientId 3=unavailable 4=bad credentials 5=unauthorized
    Serial.printf("FAILED state=%d\n", mqtt.state());
  }
}

// Builds topic string: {MQTT_PREFIX}/{MQTT_POC_TYPE}/{DEVICE_ID}/{sensor}
static void makeTopic(char* out, size_t len, const char* sensor) {
  snprintf(out, len, "%s/%s/%s/%s", MQTT_PREFIX, MQTT_POC_TYPE, DEVICE_ID, sensor);
}

void publishMQTT() {
  if (!mqtt.connected()) return;

  char topic[80];
  char buf[256];

  // ── IMU / posture / activity ──────────────────────────────────────────────
  {
    StaticJsonDocument<200> doc;
    doc["id"]           = DEVICE_ID;
    doc["pitch"]        = imuState.pitch;
    doc["roll"]         = imuState.roll;
    doc["yaw"]          = imuState.yaw;
    doc["accel_ms2"]    = imuState.accelMag;
    doc["cal_sys"]      = imuState.calSys;
    doc["posture_risk"] = imuState.postureRisk;
    doc["fall"]         = imuState.fallDetected;
    doc["idle"]         = imuState.isIdle;
    doc["idle_sec"]     = imuState.idleStartMs > 0
                            ? (millis() - imuState.idleStartMs) / 1000UL : 0;
    doc["ts"]           = millis();
    serializeJson(doc, buf);
    makeTopic(topic, sizeof(topic), "imu");
    mqtt.publish(topic, buf);
  }

  // ── Heart rate / SpO2 ─────────────────────────────────────────────────────
  {
    StaticJsonDocument<100> doc;
    doc["id"]   = DEVICE_ID;
    doc["hr"]   = hrState.hrValid   ? hrState.heartRate : -1;
    doc["spo2"] = hrState.spo2Valid ? hrState.spo2      : -1;
    doc["ts"]   = millis();
    serializeJson(doc, buf);
    makeTopic(topic, sizeof(topic), "hr");
    mqtt.publish(topic, buf);
  }
}

// ─── IMU ──────────────────────────────────────────────────────────────────────
void readIMU() {
  sensors_event_t event;
  bno.getEvent(&event);

  // Apply mount offset — sensor is 90° rotated on the belt
  imuState.pitch = normalizeAngle(event.orientation.y - MOUNT_PITCH_OFFSET);
  imuState.roll  = event.orientation.z;
  imuState.yaw   = event.orientation.x;

  imu::Vector<3> accel = bno.getVector(Adafruit_BNO055::VECTOR_ACCELEROMETER);
  imuState.accelMag = sqrt(accel.x()*accel.x() +
                           accel.y()*accel.y() +
                           accel.z()*accel.z());

  bno.getCalibration(&imuState.calSys, &imuState.calGyro,
                     &imuState.calAccel, &imuState.calMag);

  imuState.postureRisk  = (fabs(imuState.pitch) > POSTURE_PITCH_THRESHOLD ||
                           fabs(imuState.roll)  > POSTURE_ROLL_THRESHOLD);
  imuState.fallDetected = (imuState.accelMag > FALL_ACCEL_THRESHOLD);

  // Idle detection: accel magnitude stays close to 1g (stationary)
  bool stillNow = fabs(imuState.accelMag - 9.81f) < (ACTIVITY_IDLE_ACCEL - 9.81f);
  if (stillNow) {
    if (imuState.idleStartMs == 0) imuState.idleStartMs = millis();
    imuState.isIdle = (millis() - imuState.idleStartMs >= ACTIVITY_IDLE_MS);
  } else {
    imuState.idleStartMs = 0;
    imuState.isIdle      = false;
  }

  if (imuState.fallDetected) {
    Serial.println("[ALERT] Fall detected!");
    beep(TONE_FALL, 500);
  }

  Serial.printf("[IMU] P:%.1f R:%.1f Y:%.1f A:%.2f S:%d G:%d M:%d\n",
    imuState.pitch, imuState.roll, imuState.yaw, imuState.accelMag,
    imuState.calSys, imuState.calGyro, imuState.calMag);
}

// ─── MAX30102 ─────────────────────────────────────────────────────────────────
void collectHR() {
  if (!max30102Found) { hrState.hrValid = hrState.spo2Valid = 0; return; }

  hrState.collecting = true;
  for (byte i = 0; i < HR_BUFFER_LEN; i++) {
    if (currentScreen != SCREEN_HR) { hrState.collecting = false; return; }
    while (!particleSensor.available()) particleSensor.check();
    redBuffer[i] = particleSensor.getRed();
    irBuffer[i]  = particleSensor.getIR();
    particleSensor.nextSample();
  }
  hrState.collecting = false;

  maxim_heart_rate_and_oxygen_saturation(
    irBuffer, HR_BUFFER_LEN, redBuffer,
    &hrState.spo2, &hrState.spo2Valid,
    &hrState.heartRate, &hrState.hrValid);

  Serial.printf("[HR] HR=%d SPO2=%d\n", hrState.heartRate, hrState.spo2);
}

// ─── Display ──────────────────────────────────────────────────────────────────
void drawIMUScreen() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("P:"); display.print(imuState.pitch, 1);
  display.print(" R:"); display.print(imuState.roll, 1);
  display.print(" Y:"); display.print(imuState.yaw, 0);
  display.setCursor(0, 11);
  display.print("A:"); display.print(imuState.accelMag, 1);
  display.print(" S:"); display.print(imuState.calSys);
  display.print(" G:"); display.print(imuState.calGyro);
  display.print(" M:"); display.print(imuState.calMag);
  display.setCursor(0, 22);
  if      (imuState.fallDetected) display.print("!! FALL DETECTED !!");
  else if (imuState.postureRisk)  display.print("Bad posture!");
  else if (imuState.isIdle)       display.print("Idle - move!");
  else                            display.print("Posture OK");
  display.display();
}

void drawHRScreen() {
  display.clearDisplay();
  if (!max30102Found) {
    display.setTextSize(1);
    display.setCursor(0, 8);
    display.println("MAX30102");
    display.println("not connected");
    display.display();
    return;
  }
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.print("HR: ");
  display.print(hrState.hrValid ? hrState.heartRate : 0);
  display.setCursor(0, 16);
  display.print("SP: ");
  display.print(hrState.spo2Valid ? hrState.spo2 : 0);
  display.print("%");
  display.display();
}

void drawStatusScreen() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("WiFi: ");
  display.println(WiFi.status() == WL_CONNECTED ? "OK" : "NO");
  display.print("MQTT: ");
  display.println(mqtt.connected() ? "OK" : "NO");
  display.print("MAX30102: ");
  display.println(max30102Found ? "OK" : "N/A");
  display.print("Cal S:");
  display.print(imuState.calSys);
  display.print(" G:");
  display.print(imuState.calGyro);
  display.print(" M:");
  display.println(imuState.calMag);
  display.display();
}

void updateDisplay() {
  switch (currentScreen) {
    case SCREEN_IMU:    drawIMUScreen();    break;
    case SCREEN_HR:     drawHRScreen();     break;
    case SCREEN_STATUS: drawStatusScreen(); break;
  }
}

// ─── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\n=== Belt Unit Booting ===");

  pinMode(BUZZER_PIN, OUTPUT);  // tone() also sets this, but explicit is safer
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Wire.begin(SDA_PIN, SCL_PIN);

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS)) {
    Serial.println("SSD1306 not found"); while (true);
  }
  display.setTextColor(SSD1306_WHITE);
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("Starting...");
  display.display();

  // BNO055 — try 0x28 then 0x29
  bool bnoFound = bno.begin();
  if (!bnoFound) {
    Adafruit_BNO055 bno2(55, 0x29);
    if (bno2.begin()) { bno = bno2; bnoFound = true; Serial.println("BNO055 at 0x29"); }
  }
  while (!bnoFound) {
    Serial.println("BNO055 not found — check wiring (PS1+PS0 to GND?)");
    display.clearDisplay(); display.setCursor(0, 0);
    display.println("BNO055 not found!");
    display.println("Check wiring");
    display.display();
    delay(1000);
    bnoFound = bno.begin();
  }
  bno.setExtCrystalUse(true);
  Serial.println("BNO055 ready");

  // MAX30102 (optional)
  if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    particleSensor.setup(60, 4, 2, 100, 411, 4096);
    max30102Found = true;
    Serial.println("MAX30102 ready");
  } else {
    Serial.println("MAX30102 not found — HR disabled");
  }

  // WiFi + MQTT
  connectWiFi();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setBufferSize(512);      // default 256 is too small for dual-sensor payloads
  mqtt.setKeepAlive(30);        // send PINGREQ every 30 s to keep connection alive

  display.clearDisplay(); display.setCursor(0, 0);
  display.print("Ready!  MAX:"); display.println(max30102Found ? "OK" : "N/A");
  display.setCursor(0, 12);
  display.print("WiFi:"); display.println(WiFi.status() == WL_CONNECTED ? "OK" : "NO");
  display.display();

  beep(TONE_BOOT, 100); delay(80); beep(TONE_BOOT, 100);
  delay(1200);
  Serial.println("=== Belt Unit Ready ===\n");
}

// ─── Loop ─────────────────────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  if (now - lastImuMs >= IMU_INTERVAL) {
    lastImuMs = now;
    readIMU();
  }

  if (currentScreen == SCREEN_HR && !hrState.collecting) {
    collectHR();
  }

  reconnectWiFi();
  reconnectMQTT();
  mqtt.loop();
  if (now - lastMqttMs >= MQTT_INTERVAL) {
    lastMqttMs = now;
    publishMQTT();
  }

  if (!blockDisplay && now - lastDisplayMs >= DISPLAY_INTERVAL) {
    lastDisplayMs = now;
    updateDisplay();
  }

  handleButton();
}
