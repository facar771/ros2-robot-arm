// K9 Faz 1: ESP8266 şeffaf aktarıcı. Wi-Fi TCP (tek istemci) <-> UART0 (GPIO1 TX, GPIO3 RX) <-> Uno D0/D1.
// KURALLAR (Robot_1 kararı): kalp atışı ÜRETMEZ, çerçeve yorumlamaz, komut enjekte etmez, baytları olduğu gibi taşır.
// Uno'nun watchdog'u yalnız PC'den gelen çerçeveleri sayar; bu kart sessizse Uno robotu durdurur.
// UART0'a HİÇBİR günlük yazılmaz (Serial.print yok): protokol akışını bozar. Açılışta ESP8266 ROM'u 74880 baud'da
// bir miktar çöp bayt basar; Uno çözücüsü bunları düşürür (CRC/COBS).
// Şifre/SSID wifi_config.h dosyasında (gitignore'lu), kodda yok.
#include <Arduino.h>
#include <ESP8266WiFi.h>

#if __has_include("wifi_config.h")
#include "wifi_config.h"
#else
#error "include/wifi_config.h yok: wifi_config.example.h dosyasini wifi_config.h olarak kopyalayip doldur (gitignore'lu)."
#endif

static WiFiServer server(K9_TCP_PORT);
static WiFiClient client;

void setup() {
  Serial.setRxBufferSize(256);
  Serial.begin(K9_UART_BAUD);
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(K9_WIFI_SSID, K9_WIFI_PASS);
  server.begin();
}

void loop() {
  // Tek istemci: ikinci bağlantı reddedilir (HARDWARE.md §9 ile aynı kural)
  WiFiClient incoming = server.accept();
  if (incoming) {
    if (client && client.connected()) {
      incoming.stop();
    } else {
      client = incoming;
      client.setNoDelay(true);
    }
  }

  if (client && client.connected()) {
    uint8_t buf[64];
    // TCP -> UART (UART yazma tamponuna sığdığı kadar; kalan TCP tamponunda bekler)
    int n = client.available();
    if (n > 0) {
      int room = Serial.availableForWrite();
      if (n > room) n = room;
      if (n > (int)sizeof buf) n = sizeof buf;
      if (n > 0) {
        n = client.read(buf, n);
        if (n > 0) Serial.write(buf, n);
      }
    }
    // UART -> TCP
    int m = Serial.available();
    if (m > 0) {
      if (m > (int)sizeof buf) m = sizeof buf;
      m = Serial.readBytes(buf, m);
      if (m > 0) client.write(buf, m);
    }
  } else {
    while (Serial.available() > 0) Serial.read();  // istemci yok: Uno çıktısı atılır
  }
}
