// K9 Faz 3: pot telemetrisi (J1, J2, J7 mutlak açı HAM ADC). Arduino'ya bağlı DEĞİL: bilgisayarda test edilir.
// KURALLAR (Robot_1): yalnız PC'ye gider (ayrı TCP portu); ASLA Uno UART'ına yazılmaz; Uno protokolüyle karışmaz;
// kalp atışı üretmez; kalibrasyon yok (ham değer verilir, kalibrasyon profilde). Bu bir ÖLÇÜMDÜR ama güvenlik değildir.
//
// ÇERÇEVE (COBS + 0x00 ayraç, CRC-16/CCITT-FALSE little-endian; Uno protokolüyle AYNI ALGORİTMALAR, FARKLI ŞEMA):
//   ofs 0  ver   u8   = 0x54 ('T')  Uno çerçevesinin ver'i 1'dir: Uno çözücüsü bunu ver hatası olarak düşürür
//   ofs 1  type  u8   = 0x20 (POT_SAMPLE)
//   ofs 2  seq   u16  telemetri sıra numarası (çerçeve başına +1; Uno seq'iyle ilgisi yok)
//   ofs 4  t_ms  u32  ESP açılışından beri ms (~49 günde sarar; PC farkla çalışmalı)
//   ofs 8  ch    u8   kanal = eksen numarası: 1 (J1), 2 (J2), 7 (J7)
//   ofs 9  flags u8   bit0 = GEÇERSİZ (uca yakın: kablo kopması/uçta takılma); güvenilir veri sayma
//   ofs 10 raw   u16  ham ADC 0..1023 (16 örnek ortalaması)
//   ofs 12 crc   u16  ofs 0..11 üzerinden
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifndef K9_TELEM_PORT
#define K9_TELEM_PORT 8771   // PC bu porta bağlanır (salt okunur, tek istemci). Röle 8770'te kalır.
#endif
#ifndef K9_TELEM_RATE_HZ
#define K9_TELEM_RATE_HZ 15  // TOPLAM çerçeve hızı (3 pot -> pot başına ~5 Hz). Sınır: 1..20
#endif
#ifndef K9_TELEM_SETTLE_US
#define K9_TELEM_SETTLE_US 1000  // pot değişince ADC okumadan önce bekleme. Sınır: 200..20000
#endif
#ifndef K9_TELEM_INVALID_LO
#define K9_TELEM_INVALID_LO 8     // ham <= bu: geçersiz
#endif
#ifndef K9_TELEM_INVALID_HI
#define K9_TELEM_INVALID_HI 1015  // ham >= bu: geçersiz
#endif

namespace k9t {

constexpr uint8_t kVer = 0x54;
constexpr uint8_t kTypePotSample = 0x20;
constexpr uint8_t kFlagInvalid = 0x01;
constexpr size_t kRawLen = 14;          // CRC dahil, COBS öncesi
constexpr size_t kWireMax = kRawLen + 2 + 1;  // COBS (+1 kod baytı) + ayraç; 14 bayt için en çok 16
constexpr uint8_t kAvgSamples = 16;
constexpr uint8_t kPots = 3;

// Arduino pin numaraları = GPIO numaraları (NodeMCU: D0=16 D1=5 D2=4 D5=14 D6=12 D7=13).
// "hi" = pot'un üst ucu (okunurken 3.3V), "lo" = alt ucu (okunurken GND). Orta bacaklar tek A0'a gider.
struct PotPins { uint8_t ch; uint8_t hi; uint8_t lo; };
constexpr PotPins kPotTable[kPots] = {
    {1, 16, 5},   // J1: D0 (üst) / D1 (alt)
    {2, 4, 14},   // J2: D2 (üst) / D5 (alt)
    {7, 12, 13},  // J7: D6 (üst) / D7 (alt)
};

// Kullanılamaz GPIO'lar: UART0 (1,3: Uno hattı), boot pinleri (0 = D3, 2 = D4, 15 = D8), flash (6..11).
constexpr bool pinAllowed(uint8_t g) {
  return !(g == 0 || g == 1 || g == 2 || g == 3 || g == 15 || (g >= 6 && g <= 11)) && g <= 16;
}

inline uint16_t crc16(const uint8_t* d, size_t n) {
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= (uint16_t)d[i] << 8;
    for (uint8_t b = 0; b < 8; b++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
  }
  return c;
}

// COBS kodla; çıktı 0x00 İÇERMEZ. Dönüş: yazılan bayt sayısı (ayraç hariç).
inline size_t cobsEncode(const uint8_t* in, size_t n, uint8_t* out) {
  size_t ci = 0, o = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < n; i++) {
    if (in[i] == 0) {
      out[ci] = code;
      ci = o++;
      code = 1;
    } else {
      out[o++] = in[i];
      if (++code == 0xFF) {
        out[ci] = code;
        ci = o++;
        code = 1;
      }
    }
  }
  out[ci] = code;
  return o;
}

// Bir örneği telgraf olarak paketler: COBS + 0x00. Dönüş: toplam bayt (<= kWireMax).
inline size_t encodeSample(uint8_t out[kWireMax], uint16_t seq, uint32_t t_ms, uint8_t ch, uint8_t flags,
                           uint16_t raw) {
  uint8_t r[kRawLen];
  r[0] = kVer;
  r[1] = kTypePotSample;
  r[2] = (uint8_t)seq;
  r[3] = (uint8_t)(seq >> 8);
  r[4] = (uint8_t)t_ms;
  r[5] = (uint8_t)(t_ms >> 8);
  r[6] = (uint8_t)(t_ms >> 16);
  r[7] = (uint8_t)(t_ms >> 24);
  r[8] = ch;
  r[9] = flags;
  r[10] = (uint8_t)raw;
  r[11] = (uint8_t)(raw >> 8);
  uint16_t c = crc16(r, 12);
  r[12] = (uint8_t)c;
  r[13] = (uint8_t)(c >> 8);
  size_t n = cobsEncode(r, kRawLen, out);
  out[n++] = 0;
  return n;
}

struct Config {
  uint8_t rate_hz = K9_TELEM_RATE_HZ;
  uint16_t settle_us = K9_TELEM_SETTLE_US;
  uint16_t invalid_lo = K9_TELEM_INVALID_LO;
  uint16_t invalid_hi = K9_TELEM_INVALID_HI;
};

// Donanım arayüzü (ESP'de pinMode/digitalWrite/analogRead, testte sahte).
struct Hal {
  void* ctx;
  void (*drive)(void* ctx, uint8_t idx);    // idx: kPotTable indeksi. Üst uç HIGH, alt uç LOW; diğer tüm uçlar INPUT
  void (*release)(void* ctx, uint8_t idx);  // bu potun iki ucu da INPUT (yüksek empedans)
  uint16_t (*read)(void* ctx);              // tek ADC okuması (0..1023 beklenir; taşan değer kırpılır)
};

// Bloklamayan örnekleyici: step() her çağrıda en çok BİR ADC okuması yapar (röle pompasını geciktirmez).
class Sampler {
 public:
  Sampler(const Hal& hal, const Config& cfg) : hal_(hal) {
    cfg_ = cfg;
    if (cfg_.rate_hz < 1) cfg_.rate_hz = 1;
    if (cfg_.rate_hz > 20) cfg_.rate_hz = 20;
    if (cfg_.settle_us < 200) cfg_.settle_us = 200;
    if (cfg_.settle_us > 20000) cfg_.settle_us = 20000;
    period_ms_ = 1000u / cfg_.rate_hz;
  }

  // Bağlantı kesilince/yeniden bağlanınca çağır: pinleri bırakır, zamanlamayı sıfırlar. seq SIFIRLANMAZ.
  void reset() {
    if (state_ != kWait) hal_.release(hal_.ctx, idx_);
    state_ = kWait;
    started_ = false;
  }

  // now_ms: millis(), now_us: micros(); ikisi de sarma güvenli kullanılır. Çerçeve hazırsa out'a yazar ve true döner.
  bool step(uint32_t now_ms, uint32_t now_us, uint8_t out[kWireMax], size_t& n) {
    n = 0;
    switch (state_) {
      case kWait:
        if (started_ && (int32_t)(now_ms - next_ms_) < 0) return false;
        started_ = true;
        start_ms_ = now_ms;
        hal_.drive(hal_.ctx, idx_);
        settle_start_us_ = now_us;
        state_ = kSettle;
        return false;
      case kSettle:
        if ((uint32_t)(now_us - settle_start_us_) < cfg_.settle_us) return false;
        sum_ = 0;
        count_ = 0;
        state_ = kRead;
        return false;
      case kRead: {
        uint16_t v = hal_.read(hal_.ctx);
        if (v > 1023) v = 1023;
        sum_ += v;
        if (++count_ < kAvgSamples) return false;
        hal_.release(hal_.ctx, idx_);
        uint16_t raw = (uint16_t)((sum_ + kAvgSamples / 2) / kAvgSamples);
        uint8_t flags = (raw <= cfg_.invalid_lo || raw >= cfg_.invalid_hi) ? kFlagInvalid : 0;
        n = encodeSample(out, seq_++, now_ms, kPotTable[idx_].ch, flags, raw);
        idx_ = (uint8_t)((idx_ + 1) % kPots);
        next_ms_ = start_ms_ + period_ms_;
        state_ = kWait;
        return true;
      }
    }
    return false;
  }

  uint16_t seq() const { return seq_; }
  uint8_t period_ms() const { return (uint8_t)period_ms_; }

 private:
  enum State : uint8_t { kWait, kSettle, kRead };
  Hal hal_;
  Config cfg_;
  State state_ = kWait;
  bool started_ = false;
  uint8_t idx_ = 0;
  uint8_t count_ = 0;
  uint16_t seq_ = 0;
  uint32_t sum_ = 0;
  uint32_t period_ms_ = 66;
  uint32_t next_ms_ = 0, start_ms_ = 0, settle_start_us_ = 0;
};

}  // namespace k9t
