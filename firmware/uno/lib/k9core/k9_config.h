// K9 Faz 1: derleme zamanı sabitleri. HAREKET YOK.
// Bu dosya donanımdan bağımsızdır (Arduino başlığı yok): AVR'de ve bilgisayarda (host testi) aynı derlenir.
#pragma once
#include <stdint.h>

// Hareket kodu Faz 2'dir ve Robot_1'in "tezgâh modu" kararı gelmeden AÇILMAZ.
// Bu bayrak 1 yapılırsa derleme bilerek durur (hareket kodu henüz yok).
#ifndef K9_MOTION
#define K9_MOTION 0
#endif
#if K9_MOTION
#error "K9_MOTION=1: step hareketi Faz 2'dir; Robot_1 tezgah modu karari gelmeden desteklenmez."
#endif

// Eksen sayısı: tabandan uca 7 (1,2: 28BYJ-48; 3,4: SG90 çifti; 5,6: SG90; 7: A4988 kıskaç). docs: firmware/FAZ0_PLAN.md
#define K9_AXES 7

// Uno RAM'i için küçültülmüş sınırlar (HARDWARE.md azami yük 200 B der; daha uzun çerçeve DÜŞER).
#define K9_MAX_PAYLOAD 96
#define K9_HDR 8
#define K9_MAX_RAW (K9_HDR + K9_MAX_PAYLOAD + 2)   // başlık + yük + crc16
#define K9_MAX_WIRE (K9_MAX_RAW + 4)               // COBS payı dahil, 0x00 ayracı hariç
#define K9_CACHE 32                                // son 32 cmd_id'nin yanıtı (yineleme yeniden işlenmez)
#define K9_STATUS_PERIOD_MS 50
#define K9_WATCHDOG_DEFAULT_MS 300
#define K9_WATCHDOG_MIN_MS 200
#define K9_WATCHDOG_MAX_MS 500
#define K9_FW_MAJOR 0
#define K9_FW_MINOR 1

namespace k9 {

// Gömülü (derleme zamanı) eksen limitleri, adım cinsinden. PC bunları GENİŞLETEMEZ, yalnız daraltabilir.
// Faz 1'de hepsi 0..0: hiçbir hedef geçerli değil. Faz 2'de Ferhat'ın ölçümüyle ve Robot_1 kararıyla doldurulur.
inline int32_t hardMin(uint8_t /*axis*/) { return 0; }
inline int32_t hardMax(uint8_t /*axis*/) { return 0; }

}  // namespace k9
