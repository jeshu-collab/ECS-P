/*
  AquaMesh Node 2  (GAS01)  v2.0  -  MQ gas + soil probe (+ optional overflow float switch) -> LoRa 433 MHz
  Written WITHOUT access to your original node2.ino: check PIN numbers against your wiring.
     FROM=GAS01,PKT=12,STATUS=NORMAL,MQ=860,SOIL=330,OVF=0,UP=300,FW=2.0,C=A1
  Better than v1: median sampling, MQ warm-up (STATUS stays NORMAL and FLAGS=WARMUP for 60 s after power-on, because a cold MQ sensor
  reads wrong), hysteresis, CRC-8, uptime, watchdog, optional OVF= overflow float switch (enables the 'surface overflow' rule on the Pi).
  SOIL: send the RAW ADC. Do not convert to % here; the Pi converts only after you verify calibration.
*/
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <esp_task_wdt.h>
#define NODE_ID "GAS01"
#define FW_VERSION "2.0"
#define MQ_PIN 34          // <-- check
#define SOIL_PIN 35        // <-- check
#define OVF_PIN -1         // float switch to GND with INPUT_PULLUP, e.g. 25; -1 = not fitted
#define LORA_SS 18
#define LORA_RST 14
#define LORA_DIO0 26
#define LORA_FREQ 433E6
#define LORA_SYNC 0x12
#define TX_PERIOD_MS 5000
#define GAS_ALERT_RAW 1500
#define MQ_WARMUP_S 60
#define MPU_ADDR 0x68
uint32_t pktCounter = 0, lastTx = 0; int above = 0, below = 0; bool alertOn = false;
uint8_t crc8(const char* s) { uint8_t c = 0; while (*s) { c ^= (uint8_t)*s++; for (int i = 0; i < 8; i++) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1); } return c; }
int medianAnalog(int pin) { int v[5]; for (int i = 0; i < 5; i++) { v[i] = analogRead(pin); delay(3); } for (int i = 0; i < 4; i++) for (int j = i + 1; j < 5; j++) if (v[j] < v[i]) { int t = v[i]; v[i] = v[j]; v[j] = t; } return v[2]; }
bool readMpu(int16_t &ax, int16_t &ay, int16_t &az, int16_t &gx, int16_t &gy, int16_t &gz) {
  Wire.beginTransmission(MPU_ADDR); Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MPU_ADDR, 14, true) != 14) return false;
  ax = (int16_t)(Wire.read() << 8 | Wire.read()); ay = (int16_t)(Wire.read() << 8 | Wire.read()); az = (int16_t)(Wire.read() << 8 | Wire.read());
  Wire.read(); Wire.read();
  gx = (int16_t)(Wire.read() << 8 | Wire.read()); gy = (int16_t)(Wire.read() << 8 | Wire.read()); gz = (int16_t)(Wire.read() << 8 | Wire.read());
  return true;
}
void setup() {
  Serial.begin(115200); esp_task_wdt_init(15, true); esp_task_wdt_add(NULL); analogReadResolution(12);
  Wire.begin(); Wire.beginTransmission(MPU_ADDR); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
  if (OVF_PIN >= 0) pinMode(OVF_PIN, INPUT_PULLUP);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  while (!LoRa.begin(LORA_FREQ)) { delay(500); esp_task_wdt_reset(); }
  LoRa.setSyncWord(LORA_SYNC); randomSeed(esp_random());
}
void loop() {
  esp_task_wdt_reset();
  if (millis() - lastTx < TX_PERIOD_MS) { delay(20); return; }
  lastTx = millis() + random(0, 400);
  bool warm = millis() / 1000 < MQ_WARMUP_S;
  int mq = medianAnalog(MQ_PIN), soil = medianAnalog(SOIL_PIN);
  if (!warm) { if (mq > GAS_ALERT_RAW) { above++; below = 0; } else { below++; above = 0; } if (!alertOn && above >= 2) alertOn = true; if (alertOn && below >= 3) alertOn = false; }
  int16_t ax, ay, az, gx, gy, gz; bool mpuOk = readMpu(ax, ay, az, gx, gy, gz);
  char buf[230]; int n = snprintf(buf, sizeof buf, "FROM=%s,PKT=%lu,STATUS=%s,MQ=%d,SOIL=%d", NODE_ID, (unsigned long)++pktCounter, alertOn ? "ALERT" : "NORMAL", mq, soil);
  if (mpuOk) n += snprintf(buf + n, sizeof buf - n, ",AX=%d,AY=%d,AZ=%d,GX=%d,GY=%d,GZ=%d", ax, ay, az, gx, gy, gz);
  if (OVF_PIN >= 0) n += snprintf(buf + n, sizeof buf - n, ",OVF=%d", digitalRead(OVF_PIN) == LOW ? 1 : 0);
  n += snprintf(buf + n, sizeof buf - n, ",UP=%lu,FW=%s", (unsigned long)(millis() / 1000), FW_VERSION);
  if (warm) n += snprintf(buf + n, sizeof buf - n, ",FLAGS=WARMUP");
  char out[210]; snprintf(out, sizeof out, "%s,C=%02X", buf, crc8(buf));
  LoRa.beginPacket(); LoRa.print(out); LoRa.endPacket(); Serial.println(out);
}
