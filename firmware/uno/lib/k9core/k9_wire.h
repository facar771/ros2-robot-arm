// Tel biçimi yardımcıları: CRC-16/CCITT-FALSE ve yerinde COBS. Şema: docs/HARDWARE.md §2-§3,
// referans: src/robot_hw_bridge/robot_hw_bridge/{crc,cobs,codec,protocol}.py (host testi bunlarla karşılaştırır).
#pragma once
#include <stdint.h>

namespace k9 {

// poly 0x1021, init 0xFFFF, yansıtma yok. crc16("123456789") == 0x29B1. Tablosuz (flash/RAM tasarrufu).
inline uint16_t crc16(const uint8_t* d, uint8_t n) {
  uint16_t crc = 0xFFFF;
  while (n--) {
    crc ^= (uint16_t)(*d++) << 8;
    for (uint8_t i = 0; i < 8; i++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

// buf[0..n-1] COBS verisi (ayraçsız). Yerinde çözer; çözülen uzunluk ya da -1 (bozuk).
inline int16_t cobsDecodeInPlace(uint8_t* buf, uint8_t n) {
  uint8_t i = 0, o = 0;
  while (i < n) {
    uint8_t code = buf[i];
    if (code == 0) return -1;
    uint16_t end = (uint16_t)i + code;
    if (end > n) return -1;
    for (uint8_t j = i + 1; j < end; j++) buf[o++] = buf[j];
    i = (uint8_t)end;
    if (code != 0xFF && i < n) buf[o++] = 0;
  }
  return o;
}

// buf[1..n] ham veri (n <= 253). buf[0] kod baytı olur, buf[n+1] = 0x00 ayracı. Toplam n+2 bayt gönderilir.
inline void cobsEncodeInPlace(uint8_t* buf, uint8_t n) {
  uint8_t codeIdx = 0;
  for (uint8_t i = 1; i <= n; i++) {
    if (buf[i] == 0) {
      buf[codeIdx] = (uint8_t)(i - codeIdx);
      codeIdx = i;
    }
  }
  buf[codeIdx] = (uint8_t)(n + 1 - codeIdx);
  buf[n + 1] = 0;
}

}  // namespace k9
