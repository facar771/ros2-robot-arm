// K9 Faz 2a (yalnız K9_BENCH=1): 3 step ekseni için AccelStepper bağlayıcısı (zamanlayıcısız, run() ile).
// Pin numaraları BURADA YAZILMAZ: hepsi k9_pinmap.h tablosundan gelir. Mevcut değerler (Ferhat tezgâhta doğruladı):
//   J1 28BYJ-48 / ULN2003 #1: IN1..IN4 = D2..D5      J2 28BYJ-48 / ULN2003 #2: IN1..IN4 = D6..D9
//   J7 A4988 (kıskaç): STEP=D10, DIR=D11, EN=D12 (aktif DÜŞÜK; donanımda çekme direnciyle varsayılan KAPALI)
//   NC acil dur döngüsü okuma: D13, INPUT_PULLUP (LOW = döngü kapalı/OK; HIGH ya da bağlı değil = açık)
// ULN2003 sırası: AccelStepper HALF4WIRE'a (IN1, IN3, IN2, IN4) verilir. Yanlış kablolamada motor DÖNMEZ, titrer;
// bunu yazılım kanıtlayamaz, Ferhat bakar (PR gövdesindeki adımlar).
#pragma once
#include <AccelStepper.h>
#include <Arduino.h>

#include "k9_bench.h"
#include "k9_pinmap.h"

template <uint8_t A>
__attribute__((always_inline)) constexpr k9::pinmap::AxisMap M() {  // sabit indeks: tablo derleme zamanında okunur, RAM'e kopyalanmaz
  return k9::pinmap::kAxes[A];
}

class UnoBenchHal : public k9::BenchHal {
 public:
  static const uint8_t PIN_EN = k9::pinmap::kAxes[6].pin[2], PIN_LOOP = k9::pinmap::PIN_ESTOP_LOOP;

  // HALF4WIRE'a (IN1, IN3, IN2, IN4) sırası verilir: tablodaki IN1..IN4 sırası pin[0], pin[2], pin[1], pin[3].
  UnoBenchHal()
      : j1_(AccelStepper::HALF4WIRE, M<0>().pin[0], M<0>().pin[2], M<0>().pin[1], M<0>().pin[3], false),
        j2_(AccelStepper::HALF4WIRE, M<1>().pin[0], M<1>().pin[2], M<1>().pin[1], M<1>().pin[3], false),
        j7_(AccelStepper::DRIVER, M<6>().pin[0], M<6>().pin[1]) {}

  // setup()'ta, Mcu::begin'den önce. Önce hepsi KAPALI/DÜŞÜK, sonra çıkış yapılır (EN'e kısa darbe gelmesin).
  void begin() {
    digitalWrite(PIN_EN, HIGH);  // girişte HIGH = çekme direnci açık; çıkış yapılınca da HIGH = sürücü KAPALI
    pinMode(PIN_EN, OUTPUT);
    lowOut(M<0>());  // yalnız step sürücü pinleri; servo pinlerine DOKUNULMAZ (sabit indeksler: tablo RAM'e kopyalanmaz)
    lowOut(M<1>());
    lowOut(M<6>());
    pinMode(PIN_LOOP, INPUT_PULLUP);
    j7_.setMinPulseWidth(3);  // A4988: STEP en az 1 us; payla birlikte 3 us
    j1_.setCurrentPosition(0);
    j2_.setCurrentPosition(0);
    j7_.setCurrentPosition(0);
  }

  void run() {  // loop() başına olabildiğince sık çağrılır
    j1_.run();
    j2_.run();
    j7_.run();
  }

  bool loopClosed() override { return digitalRead(PIN_LOOP) == LOW; }

  void power(uint8_t a, bool on) override {
    if (a == 6) {
      digitalWrite(PIN_EN, on ? LOW : HIGH);
    } else if (AccelStepper* s = at(a)) {
      if (on) s->enableOutputs(); else s->disableOutputs();  // disable: 4 bobin LOW
    }
  }
  void moveTo(uint8_t a, int32_t t, uint32_t vmax, uint32_t accel) override {
    if (AccelStepper* s = at(a)) {
      s->setMaxSpeed((float)vmax);
      s->setAcceleration((float)accel);
      s->moveTo((long)t);
    }
  }
  void stop(uint8_t a) override { if (AccelStepper* s = at(a)) s->stop(); }
  void halt(uint8_t a) override { if (AccelStepper* s = at(a)) s->setCurrentPosition(s->currentPosition()); }
  void setZero(uint8_t a) override { if (AccelStepper* s = at(a)) s->setCurrentPosition(0); }
  int32_t position(uint8_t a) override { AccelStepper* s = at(a); return s ? (int32_t)s->currentPosition() : 0; }
  bool moving(uint8_t a) override { AccelStepper* s = at(a); return s && s->distanceToGo() != 0; }

 private:
  static void lowPin(uint8_t p) {
    digitalWrite(p, LOW);
    pinMode(p, OUTPUT);
  }
  // Sabit indeksle okunur (dinamik indeks tabloyu RAM'e kopyalatır). A4988'de yalnız STEP, DIR (EN ayrı ele alınır).
  __attribute__((always_inline)) static inline void lowOut(k9::pinmap::AxisMap m) {
    lowPin(m.pin[0]);
    lowPin(m.pin[1]);
    if (m.drv == k9::pinmap::DRV_ULN2003) {
      lowPin(m.pin[2]);
      lowPin(m.pin[3]);
    }
  }
  AccelStepper* at(uint8_t a) { return a == 0 ? &j1_ : (a == 1 ? &j2_ : (a == 6 ? &j7_ : 0)); }
  AccelStepper j1_, j2_, j7_;
};
