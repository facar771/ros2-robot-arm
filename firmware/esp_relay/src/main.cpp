// K9 Faz 1: ESP8266 şeffaf aktarıcı. Wi-Fi TCP (tek istemci) <-> UART0 (GPIO1 TX, GPIO3 RX) <-> Uno D0/D1.
// KURALLAR (Robot_1 kararı): kalp atışı ÜRETMEZ, çerçeve yorumlamaz, komut enjekte etmez, baytları olduğu gibi taşır.
// Uno'nun watchdog'u yalnız PC'den gelen çerçeveleri sayar; bu kart sessizse Uno robotu durdurur.
// UART0'a HİÇBİR günlük yazılmaz (Serial.print yok): protokol akışını bozar. Açılışta ESP8266 ROM'u 74880 baud'da
// bir miktar çöp bayt basar; Uno çözücüsü bunları düşürür (CRC/COBS).
// Şifre/SSID wifi_config.h dosyasında (gitignore'lu), kodda yok.
// Faz 3 (EK): pot telemetrisi AYRI bir TCP portunda (K9_TELEM_PORT, 8771) ve ayrı çerçeve türüyle yalnız PC'ye gider.
// Telemetri UART0'a ASLA yazılmaz; telemetri istemcisinden gelen bayt UART'a ASLA gitmez (atılır). Röle bölümü
// (aşağıda "Röle") Faz 1 ile aynıdır.
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <k9_telem.h>

#if __has_include("wifi_config.h")
#include "wifi_config.h"
#else
#error "include/wifi_config.h yok: wifi_config.example.h dosyasini wifi_config.h olarak kopyalayip doldur (gitignore'lu)."
#endif

static WiFiServer server(K9_TCP_PORT);
static WiFiClient client;

// --- Faz 3: pot telemetrisi (salt okunur, tek istemci) ---
static WiFiServer telemServer(K9_TELEM_PORT);
static WiFiClient telemClient;

static void potDrive(void*, uint8_t i) {  // üst uç HIGH, alt uç LOW; diğer potların uçları zaten INPUT
  pinMode(k9t::kPotTable[i].lo, OUTPUT);
  digitalWrite(k9t::kPotTable[i].lo, LOW);
  pinMode(k9t::kPotTable[i].hi, OUTPUT);
  digitalWrite(k9t::kPotTable[i].hi, HIGH);
}
static void potRelease(void*, uint8_t i) {  // iki uç da yüksek empedans
  pinMode(k9t::kPotTable[i].hi, INPUT);
  pinMode(k9t::kPotTable[i].lo, INPUT);
}
static uint16_t potRead(void*) { return (uint16_t)analogRead(A0); }
static k9t::Sampler sampler({nullptr, potDrive, potRelease, potRead}, k9t::Config());

static void telemetryService() {
  WiFiClient incoming = telemServer.accept();
  if (incoming) {
    if (telemClient && telemClient.connected()) {
      incoming.stop();
    } else {
      telemClient = incoming;
      telemClient.setNoDelay(true);
      sampler.reset();
    }
  }
  if (!(telemClient && telemClient.connected())) {
    if (telemClient) {
      telemClient.stop();
      sampler.reset();  // bağlantı koptu: pinleri bırak
    }
    return;  // istemci yok: ADC okunmaz, pot pinleri INPUT kalır
  }
  while (telemClient.available() > 0) telemClient.read();  // salt okunur kanal: gelen baytlar ATILIR (UART'a gitmez)
  uint8_t out[k9t::kWireMax];
  size_t n;
  if (sampler.step(millis(), micros(), out, n)) {
    if ((size_t)telemClient.availableForWrite() >= n) telemClient.write(out, n);  // yer yoksa at; seq'te boşluk görünür
  }
}

void setup() {
  for (uint8_t i = 0; i < k9t::kPots; i++) potRelease(nullptr, i);  // açılışta tüm pot uçları INPUT
  Serial.setRxBufferSize(256);
  Serial.begin(K9_UART_BAUD);
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(K9_WIFI_SSID, K9_WIFI_PASS);
  server.begin();
  telemServer.begin();
}

void loop() {
  // --- Röle (Faz 1, DEĞİŞMEDİ) ---
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

  telemetryService();  // Faz 3: röle pompasından SONRA; her çağrıda en çok bir ADC okuması
}
