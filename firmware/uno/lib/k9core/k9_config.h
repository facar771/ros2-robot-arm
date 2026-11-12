// K9: derleme zamanı sabitleri. Varsayılan derleme (K9_BENCH=0) HAREKETSİZDİR (Faz 1).
// Bu dosya donanımdan bağımsızdır (Arduino başlığı yok): AVR'de ve bilgisayarda (host testi) aynı derlenir.
#pragma once
#include <stdint.h>

// K9_BENCH=1: Faz 2a TEZGÂH derlemesi (Robot_1 kararı). YALNIZ 3 step ekseni (J1, J2: 28BYJ-48; J7: A4988) hareket eder,
// motorlar yükten ayrık, operatör başında, güç kesmeye hazır. Servolar Faz 2b'dir (bu derlemede de sürülmez).
// Bayraksız derleme bugünkü hareketsiz davranışı aynen korur (HOME/ENABLE -> NO_REFERENCE).
#ifndef K9_BENCH
#define K9_BENCH 0
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

#if K9_BENCH
// Tezgâh eksenleri (protokol eksen indeksi, 0 tabanlı): J1=0, J2=1, J7=6. Diğerleri (servo) bu derlemede YOKTUR.
const uint8_t BENCH_MASK = 0x43;
inline bool isBenchAxis(uint8_t a) { return a == 0 || a == 1 || a == 6; }

// Gömülü (derleme zamanı) limitler, YARIM ADIM/ADIM cinsinden. PC bunları GENİŞLETEMEZ, yalnız daraltabilir.
// J1,J2 (28BYJ-48 yarım adım): +-256, <=300 adım/sn, <=300 adım/sn^2.  J7 (A4988): +-50 adım, <=100 adım/sn.
// J7 ivme sınırı Robot_1 mesajında verilmedi: ÖNERİLEN <=100 adım/sn^2 (onay bekliyor, bkz. PR).
inline int32_t hardMin(uint8_t a) { return (a == 0 || a == 1) ? -256 : (a == 6 ? -50 : 0); }
inline int32_t hardMax(uint8_t a) { return (a == 0 || a == 1) ? 256 : (a == 6 ? 50 : 0); }
inline uint32_t hardVmax(uint8_t a) { return (a == 0 || a == 1) ? 300 : (a == 6 ? 100 : 0); }
inline uint32_t hardAccel(uint8_t a) { return (a == 0 || a == 1) ? 300 : (a == 6 ? 100 : 0); }
#else
// Faz 1: hepsi 0..0, hiçbir hedef geçerli değil.
inline int32_t hardMin(uint8_t /*axis*/) { return 0; }
inline int32_t hardMax(uint8_t /*axis*/) { return 0; }
#endif

}  // namespace k9
