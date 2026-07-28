#ifndef CONFIG_H
#define CONFIG_H

// ======================================================
// MAX30102 I2C Pins (Beetle ESP32-C6)
// ======================================================

#define MAX30102_SDA          6
#define MAX30102_SCL          7

// ======================================================
// Sensor Configuration
// ======================================================

#define SAMPLE_RATE           100
#define SAMPLE_AVERAGE        4

// ======================================================
// Finger Detection
// ======================================================

#define FINGER_THRESHOLD      50000

// ======================================================
// Signal Filter
// ======================================================

#define DC_ALPHA              0.95f
#define LP_ALPHA              0.20f
#define MOVING_AVG_SIZE       5

// ======================================================
// Peak Detector V3
// ======================================================

// RR interval limits
// 550 ms = 109 BPM
// 1500 ms = 40 BPM
#define MIN_RR               350
#define MAX_RR               2000

// Ignore any peak for this duration after a valid beat
#define REFRACTORY_TIME      350

// Peak prominence must exceed this percentage of AC amplitude
// Increase to reject noise, decrease if real beats are missed.
#define PROMINENCE_FACTOR    0.6f

// Minimum acceptable signal quality (0-100)
#define MIN_SIGNAL_QUALITY   60

// Minimum AC amplitude required
#define MIN_AC_AMPLITUDE     200.0f

// Number of samples used for local peak detection
// 21 samples @100Hz = 210ms window
#define PEAK_WINDOW          17

// ======================================================
// Heart Rate
// ======================================================

// Median/average BPM smoothing
#define BPM_AVERAGE_SIZE     5


#define SPO2_FINGER_THRESHOLD 30000

// ======================================================
// BLE
// ======================================================

#define ANCHOR_SERVICE_UUID ((uint16_t)0xFFF0)

#define TX_POWER   -59
#define PATH_LOSS  2.8f

#endif