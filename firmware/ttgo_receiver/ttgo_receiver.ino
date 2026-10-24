/*
  AquaMesh TTGO LoRa receiver (REFERENCE) - prints every received packet to USB serial.
  If your existing TTGO sketch already prints the raw packet text (FROM=...,C=XX) you do NOT need this:
  AquaMesh finds the packet inside any line and also reads optional "RSSI=" / "SNR=" text.

  Board: TTGO LoRa32 (T3 v1.6.1 pins shown - change if yours differs). Library: "LoRa" by Sandeep Mistry.
  Output example:
    FROM=FLOOD01,PKT=33,STATUS=NORMAL,RAIN=120,SOIL=330,PRESS=192547,UP=165,FW=2.4,C=F3 | RSSI=-71 SNR=9.5
    FROM=GAS01,PKT=21,STATUS=NORMAL,MQ=780,DIST=2.45,MPU=OK,UP=165,FW=2.1,C=AA | RSSI=-71 SNR=9.5
  Receiver is a transparent bridge: it does not rename or modify node fields.
  AquaMesh parser maps PRESS to its internal HX feature and consumes DIST directly.
*/
#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK 5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_SS 18
#define LORA_RST 23
#define LORA_DIO0 26
#define LORA_FREQ 433E6
#define LORA_SYNC 0x12      // must match the nodes

void setup() {
  Serial.begin(115200);
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  while (!LoRa.begin(LORA_FREQ)) { Serial.println("LoRa init failed, retrying"); delay(500); }
  LoRa.setSyncWord(LORA_SYNC);
  Serial.println("TTGO receiver ready");
}

void loop() {
  int n = LoRa.parsePacket();
  if (n) {
    String s;
    while (LoRa.available()) s += (char)LoRa.read();
    s.trim();
    Serial.print(s);
    Serial.print(" | RSSI="); Serial.print(LoRa.packetRssi());
    Serial.print(" SNR=");    Serial.println(LoRa.packetSnr());
  }
}
