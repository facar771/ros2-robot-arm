// K9: EKSEN -> PİN EŞLEMESİ, TEK YAPILANDIRMA TABLOSU. Firmware'de başka hiçbir yerde pin numarası yazılmaz.
// Saf veri: Arduino başlığı yok, host testinde de derlenir (firmware/host/pinmap_check.cpp).
//
// DURUM (Robot_1, 2026-11-19):
//  * Step eksenleri (J1, J2, J7) pinleri Ferhat'ın tezgâhında gerçek donanımda ÇALIŞTI (`confirmed = 1`).
//  * Servo eksenleri (J3..J6) pinleri ONAYSIZDIR (`confirmed = 0`): eşleme Ferhat teyit edene kadar DEĞİŞEBİLİR.
//    Servo FİRMWARE'İ YAZILMADI; bu tablo servo için yalnız VERİDİR. Servo aktifleştirme mekanik gelene kadar KAPALI
//    (K9_SERVO=0; 1 yapılırsa derleme bilerek durur).
//  * Ferhat'ın son sözü: J3 = 2x SG90 (aynalı çift), J4 = 1x SG90 (yerinde döner, yükü az), J5 = 2x SG90 (aynalı çift),
//    J6 = 1x SG90. Toplam 6 SG90. (Eski plan çiftleri J3/J4 sanıyordu; bu tablo düzeltilmiş halidir.)
//  * Servo sinyal pinleri A0..A5 (Uno'da 14..19). Hangi servo hangi pine: ÖNERİ, teyit bekliyor (bkz. firmware/FAZ0_PLAN.md).
//  * Aynalı çiftte ikinci servo `toplam - açı` ile sürülür (kalibrasyon sonraki aşama; burada yalnız `mirrored = 1`).
#pragma once
#include <stdint.h>

#ifndef K9_SERVO
#define K9_SERVO 0
#endif
#if K9_SERVO
#error "K9_SERVO=1: servo firmware'i yazılmadı (Faz 2b). Eşleme teyidi ve mekanik gelene kadar kapalı."
#endif

namespace k9 {
namespace pinmap {

enum Drv : uint8_t { DRV_NONE = 0, DRV_ULN2003 = 1, DRV_A4988 = 2, DRV_SERVO = 3 };

const uint8_t NO_PIN = 255;
const uint8_t A0 = 14, A1 = 15, A2 = 16, A3 = 17, A4 = 18, A5 = 19;  // Uno analog pinleri (dijital numara)

struct AxisMap {
  uint8_t drv;        // Drv
  uint8_t n;          // kullanılan pin sayısı
  uint8_t pin[4];     // ULN2003: IN1..IN4 (sırayla). A4988: STEP, DIR, EN. SERVO: [0]=birinci servo, [1]=aynalı ikinci servo
  uint8_t confirmed;  // 1 = donanımda doğrulandı, 0 = ONAYSIZ öneri
  uint8_t mirrored;   // 1 = servo çifti (pin[1] ikinci, aynalı servo)
};

// İndeks = protokol eksen indeksi (J1=0 ... J7=6). K9_AXES (k9_config.h) = 7 ile aynı olmalı.
const uint8_t AXIS_COUNT = 7;
constexpr AxisMap kAxes[AXIS_COUNT] = {
    /* J1 */ {DRV_ULN2003, 4, {2, 3, 4, 5}, 1, 0},
    /* J2 */ {DRV_ULN2003, 4, {6, 7, 8, 9}, 1, 0},
    /* J3 */ {DRV_SERVO, 2, {A0, A1, NO_PIN, NO_PIN}, 0, 1},
    /* J4 */ {DRV_SERVO, 1, {A2, NO_PIN, NO_PIN, NO_PIN}, 0, 0},
    /* J5 */ {DRV_SERVO, 2, {A3, A4, NO_PIN, NO_PIN}, 0, 1},
    /* J6 */ {DRV_SERVO, 1, {A5, NO_PIN, NO_PIN, NO_PIN}, 0, 0},
    /* J7 */ {DRV_A4988, 3, {10, 11, 12, NO_PIN}, 1, 0},
};

// Eksen dışı pinler.
const uint8_t PIN_ESTOP_LOOP = 13;  // NC döngü okuma, INPUT_PULLUP (LOW = kapalı/OK)

}  // namespace pinmap
}  // namespace k9
