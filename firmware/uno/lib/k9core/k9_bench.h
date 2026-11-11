// K9 Faz 2a (yalnız K9_BENCH=1): motor/pin erişim arayüzü. Çekirdek (k9_mcu.cpp) Arduino'dan bağımsız kalır:
// AVR'de AccelStepper bağlayıcısı (src/bench_motors.h), host testinde sahte HAL bunu uygular.
// Eksen numarası protokol indeksidir (J1=0, J2=1, J7=6); başka indeks gelmez (çekirdek süzer).
#pragma once
#include <stdint.h>

namespace k9 {

class BenchHal {
 public:
  virtual bool loopClosed() = 0;                  // D13 LOW = NC döngü kapalı/OK. HIGH ya da bağlı değil = açık.
  virtual void power(uint8_t axis, bool on) = 0;  // bobin/EN: on=false -> bobinler ve A4988 EN kapalı
  virtual void moveTo(uint8_t axis, int32_t target, uint32_t vmax, uint32_t accel) = 0;
  virtual void stop(uint8_t axis) = 0;            // ivmeli durdur (hedef = durma noktası)
  virtual void halt(uint8_t axis) = 0;            // ANINDA dur (adım üretimi biter)
  virtual void setZero(uint8_t axis) = 0;         // mevcut konum = 0 (yalnız durgunken çağrılır)
  virtual int32_t position(uint8_t axis) = 0;     // sayılan adım (kestirim)
  virtual bool moving(uint8_t axis) = 0;
};

}  // namespace k9
