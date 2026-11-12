// K9 Uno firmware. Varsayılan derleme (env:uno) Faz 1, HAREKETSİZ: hiçbir motor/servo pinini sürmez (D2..D12, A0..A5 giriş kalır).
// env:uno_bench (-DK9_BENCH=1): Faz 2a TEZGÂH derlemesi, yalnız J1, J2 (28BYJ-48) ve J7 (A4988) hareket eder; servo yok.
// Yaptığı: seri porttan (USB ya da ESP aktarıcısı, 115200 8N1) protokol çerçevelerini okur; HELLO/STATUS/ACK yanıtlar.
// Seri port D0/D1'dir: yükleme sırasında ESP bağlantı tellerini SÖK; günlük/Serial.print KULLANMA (protokolü bozar).
#include <Arduino.h>
#include <EEPROM.h>

#include "k9_mcu.h"
#if K9_BENCH
#include "bench_motors.h"
static UnoBenchHal hal;
#endif

static k9::Mcu mcu;

static void txSink(const uint8_t* data, uint8_t len, void*) { Serial.write(data, len); }

void setup() {
  Serial.begin(115200);
  // açılış sayacı (HELLO'da boot_count): EEPROM 0-1, açılışta bir kez yazılır
  uint16_t boots = 0;
  EEPROM.get(0, boots);
  if (boots == 0xFFFF) boots = 0;
  boots++;
  EEPROM.put(0, boots);
#if K9_BENCH
  hal.begin();
  mcu.setHal(&hal);
#endif
  mcu.begin(boots, millis(), txSink, 0);
}

void loop() {
  uint32_t now = millis();
#if K9_BENCH
  hal.run();
  while (Serial.available() > 0) {
    mcu.feed((uint8_t)Serial.read(), now);
    hal.run();  // çerçeve çözme sırasında adım darbesi gecikmesin
  }
  mcu.poll(now);
  hal.run();
#else
  while (Serial.available() > 0) mcu.feed((uint8_t)Serial.read(), now);
  mcu.poll(now);
#endif
}
