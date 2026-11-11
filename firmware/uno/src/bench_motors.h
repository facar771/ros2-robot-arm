// K9 Faz 2a (yalnız K9_BENCH=1): 3 step ekseni için AccelStepper bağlayıcısı (zamanlayıcısız, run() ile).
// Pinler (Ferhat'ın karma robot tablosu, Robot_1 onaylı; firmware/FAZ0_PLAN.md §2):
//   J1 28BYJ-48 / ULN2003 #1: IN1..IN4 = D2..D5      J2 28BYJ-48 / ULN2003 #2: IN1..IN4 = D6..D9
//   J7 A4988 (kıskaç): STEP=D10, DIR=D11, EN=D12 (aktif DÜŞÜK; donanımda çekme direnciyle varsayılan KAPALI)
//   NC acil dur döngüsü okuma: D13, INPUT_PULLUP (LOW = döngü kapalı/OK; HIGH ya da bağlı değil = açık)
// ULN2003 sırası: AccelStepper HALF4WIRE'a (IN1, IN3, IN2, IN4) verilir. Yanlış kablolamada motor DÖNMEZ, titrer;
// bunu yazılım kanıtlayamaz, Ferhat bakar (PR gövdesindeki adımlar).
#pragma once
#include <AccelStepper.h>
#include <Arduino.h>

#include "k9_bench.h"

class UnoBenchHal : public k9::BenchHal {
 public:
  static const uint8_t PIN_EN = 12, PIN_LOOP = 13;

  UnoBenchHal()
      : j1_(AccelStepper::HALF4WIRE, 2, 4, 3, 5, false),
        j2_(AccelStepper::HALF4WIRE, 6, 8, 7, 9, false),
        j7_(AccelStepper::DRIVER, 10, 11) {}

  // setup()'ta, Mcu::begin'den önce. Önce hepsi KAPALI/DÜŞÜK, sonra çıkış yapılır (EN'e kısa darbe gelmesin).
  void begin() {
    digitalWrite(PIN_EN, HIGH);  // girişte HIGH = çekme direnci açık; çıkış yapılınca da HIGH = sürücü KAPALI
    pinMode(PIN_EN, OUTPUT);
    for (uint8_t p = 2; p <= 11; p++) {
      digitalWrite(p, LOW);
      pinMode(p, OUTPUT);
    }
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
  AccelStepper* at(uint8_t a) { return a == 0 ? &j1_ : (a == 1 ? &j2_ : (a == 6 ? &j7_ : 0)); }
  AccelStepper j1_, j2_, j7_;
};
