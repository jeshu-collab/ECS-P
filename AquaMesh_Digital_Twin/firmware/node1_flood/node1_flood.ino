/*
  AquaMesh Node 1  (FLOOD01)  v2.0  -  rain sensor + HX711 -> LoRa 433 MHz
  Written WITHOUT access to your original node1.ino: check PIN numbers below against your wiring.
  Packet stays backward-compatible with the old Pi parser:
     FROM=FLOOD01,PKT=33,STATUS=NORMAL,RAIN=120,HX=192547,UP=165,FW=2.0,C=3F
  What is better than v1:
   - median of 5 ADC samples (kills single-sample spikes)            - STATUS alert uses hysteresis (2 in a row to set, 3 to clear)
   - HX711 omitted (not sent as 0) when it is not ready              - uptime UP= so the Pi can see reboots
   - CRC-8 (poly 0x07) over the payload so corrupted packets are rejected  - hardware watchdog restarts a hung node
   - optional battery voltage VBAT= (set VBAT_PIN)                    - jittered TX so two nodes do not always collide
*/
#include <SPI.h>
#include <LoRa.h>
#include <HX711.h>
#include <esp_task_wdt.h>

// ---- CONFIG (edit) ----
#define NODE_ID        "FLOOD01"
#define FW_VERSION     "2.0"
#define RAIN_PIN       34          // analog rain board AO   <-- check
#define HX_DOUT        16          // <-- check
#define HX_SCK         17          // <-- check
#define VBAT_PIN       -1          // e.g. 35 with a 2:1 divider; -1 = not fitted
#define LORA_SS 18
#define LORA_RST 14
#define LORA_DIO0 26
#define LORA_FREQ      433E6
#define LORA_SYNC      0x12
#define TX_PERIOD_MS   5000
#define ALERT_RAW      500         // same threshold the Pi mirrors
#define ALERT_SET_N    2
#define ALERT_CLEAR_N  3
#define ALERT_CLEAR_RAW 400
// -----------------------
HX711 hx;
uint32_t pktCounter = 0, lastTx = 0;
int aboveCount = 0, belowCount = 0;
bool alertOn = false;

uint8_t crc8(const char* s) {
  uint8_t c = 0;
  while (*s) { c ^= (uint8_t)*s++; for (int i = 0; i < 8; i++) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1); }
  return c;
}
int medianAnalog(int pin) {
  int v[5];
  for (int i = 0; i < 5; i++) { v[i] = analogRead(pin); delay(3); }
  for (int i = 0; i < 4; i++) for (int j = i + 1; j < 5; j++) if (v[j] < v[i]) { int t = v[i]; v[i] = v[j]; v[j] = t; }
  return v[2];
}
void setup() {
  Serial.begin(115200);
  esp_task_wdt_init(15, true); esp_task_wdt_add(NULL);       // reboot if loop() stalls 15 s
  analogReadResolution(12);
  hx.begin(HX_DOUT, HX_SCK);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  while (!LoRa.begin(LORA_FREQ)) { Serial.println("LoRa init failed, retrying"); delay(500); esp_task_wdt_reset(); }
  LoRa.setSyncWord(LORA_SYNC);
  randomSeed(esp_random());
}
void loop() {
  esp_task_wdt_reset();
  if (millis() - lastTx < TX_PERIOD_MS) { delay(20); return; }
  lastTx = millis() + random(0, 400);                        // jitter
  int rain = medianAnalog(RAIN_PIN);
  if (rain > ALERT_RAW) { aboveCount++; belowCount = 0; } else if (rain < ALERT_CLEAR_RAW) { belowCount++; aboveCount = 0; }
  if (!alertOn && aboveCount >= ALERT_SET_N) alertOn = true;
  if (alertOn && belowCount >= ALERT_CLEAR_N) alertOn = false;
  char buf[160];
  int n = snprintf(buf, sizeof buf, "FROM=%s,PKT=%lu,STATUS=%s,RAIN=%d", NODE_ID, (unsigned long)++pktCounter, alertOn ? "ALERT" : "NORMAL", rain);
  if (hx.is_ready()) n += snprintf(buf + n, sizeof buf - n, ",HX=%ld", hx.read());   // omitted when not ready (v1 sent 0)
  n += snprintf(buf + n, sizeof buf - n, ",UP=%lu,FW=%s", (unsigned long)(millis() / 1000), FW_VERSION);
  if (VBAT_PIN >= 0) n += snprintf(buf + n, sizeof buf - n, ",VBAT=%.2f", analogRead(VBAT_PIN) * 2.0 * 3.3 / 4095.0);
  char out[200];
  snprintf(out, sizeof out, "%s,C=%02X", buf, crc8(buf));
  LoRa.beginPacket(); LoRa.print(out); LoRa.endPacket();
  Serial.println(out);
}
