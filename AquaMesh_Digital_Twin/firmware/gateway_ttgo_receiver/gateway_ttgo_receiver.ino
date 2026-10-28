/*
  AquaMesh gateway receiver (TTGO LoRa32, 433 MHz)  v2.0
  Prints each received payload and appends the radio quality, which the Pi parses automatically:
     FROM=FLOOD01,PKT=33,...,C=3F,RSSI=-87,SNR=9.5
  The CRC covers only the payload (text before ",C="), so adding RSSI/SNR after it is safe.
  Written WITHOUT your original receiver sketch: verify the TTGO pin map for your board revision.
*/
#include <SPI.h>
#include <LoRa.h>
#define LORA_SCK 5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_SS 18
#define LORA_RST 14
#define LORA_DIO0 26
#define LORA_FREQ 433E6
#define LORA_SYNC 0x12
void setup() {
  Serial.begin(115200);
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
  while (!LoRa.begin(LORA_FREQ)) { Serial.println("LoRa init failed"); delay(500); }
  LoRa.setSyncWord(LORA_SYNC);
  Serial.println("AquaMesh receiver ready");
}
void loop() {
  int size = LoRa.parsePacket();
  if (!size) return;
  String s; while (LoRa.available()) s += (char)LoRa.read();
  s.trim();
  Serial.print(s); Serial.print(",RSSI="); Serial.print(LoRa.packetRssi()); Serial.print(",SNR="); Serial.println(LoRa.packetSnr());
}
