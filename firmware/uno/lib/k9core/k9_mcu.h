// K9: Uno tarafı protokol çekirdeği. Varsayılan derleme HAREKETSİZ (Faz 1); K9_BENCH=1 tezgâh derlemesi (Faz 2a). Arduino başlığı kullanmaz; zaman ve bayt akışı dışarıdan verilir.
// Davranış belirtimi: src/robot_hw_bridge/robot_hw_bridge/sim.py (Esp32Sim) ve docs/HARDWARE.md §3-§5.
#pragma once
#include <stdint.h>

#include "k9_config.h"
#if K9_BENCH
#include "k9_bench.h"
#endif

namespace k9 {

class Mcu {
 public:
  // Çerçeve hazır olunca çağrılır (COBS'lu, sonunda 0x00 ayracı dahil). Seri porta yazan kod burada.
  typedef void (*TxSink)(const uint8_t* data, uint8_t len, void* ctx);

  struct Counters {
    uint16_t framesOk, badFrames, cmds, dupCmds, lost, duplicates;
  };

  void begin(uint16_t bootCount, uint32_t now, TxSink sink, void* ctx);
  void feed(uint8_t byte, uint32_t now);  // PC'den gelen her bayt
  void poll(uint32_t now);                // watchdog + 50 ms'lik STATUS
#if K9_BENCH
  void setHal(BenchHal* hal) { hal_ = hal; }  // begin()'den ÖNCE çağrılmalı
#endif
  const Counters& counters() const { return cnt_; }
  uint8_t state() const { return state_; }

#ifdef K9_HOST_TEST
  void testForceState(uint8_t s) { state_ = s; }  // yalnız host testi: ENABLED'a ulaşan kod yolu Faz 1'de yok
#endif

 private:
  void handleWire(uint8_t n, uint32_t now);
  void handleFrame(uint8_t type, uint8_t flags, uint16_t seq, const uint8_t* p, uint8_t plen, uint32_t now);
  void execute(uint8_t type, const uint8_t* p, uint8_t plen, int32_t cid);
  uint8_t runCommand(uint8_t type, const uint8_t* p, uint8_t plen, uint8_t* detail);
  uint8_t doConfig(const uint8_t* p, uint8_t* detail);
  void watchdogCheck(uint32_t now);
  void enterEstop(uint8_t reason, bool fault, uint8_t axis, int32_t detail);
#if K9_BENCH
  uint8_t benchCommand(uint8_t type, const uint8_t* p, uint8_t* detail);
  void benchMonitorLoop(uint32_t now);
  void benchAllOff();
#endif
  void sendHello();
  void sendStatus();
  void sendFault(uint8_t code, uint8_t axis, int32_t detail);
  void reply(int32_t cid, uint8_t result, uint8_t detail);
  void sendFrame(uint8_t type, uint8_t plen);
  uint8_t* txPayload() { return tx_ + 1 + K9_HDR; }
  uint32_t millisSince() const { return now_ - t0_; }
  bool cacheFind(uint16_t id, uint8_t* res, uint8_t* det) const;
  void cacheStore(uint16_t id, uint8_t res, uint8_t det);

  // --- gönderim
  TxSink sink_ = 0;
  void* ctx_ = 0;
  uint8_t tx_[K9_MAX_RAW + 2];
  uint16_t txSeq_ = 0;
  // --- alım
  uint8_t rx_[K9_MAX_WIRE];
  uint8_t rxLen_ = 0;
  bool overflow_ = false;
  bool rxHas_ = false;
  uint16_t rxExpected_ = 0, rxLast_ = 0;
  // --- durum
  uint8_t state_ = 0;   // protocol.State
  uint8_t reason_ = 0;  // protocol.EstopReason
  bool linkOk_ = false;
  bool hostSeen_ = false;
  uint32_t lastHost_ = 0, t0_ = 0, nextStatus_ = 0, now_ = 0;
  uint16_t watchdogMs_ = K9_WATCHDOG_DEFAULT_MS;
  uint16_t bootCount_ = 0;
  int32_t softMin_[K9_AXES];
  int32_t softMax_[K9_AXES];
#if K9_BENCH
  BenchHal* hal_ = 0;
  uint8_t homedMask_ = 0, enabledMask_ = 0;  // bit = protokol eksen indeksi
  bool loopOpen_ = false, openPending_ = false;
  uint32_t openT_ = 0;
#endif
  // --- komut yanıt önbelleği (halka)
  struct CacheEntry { uint16_t id; uint8_t res, det; };
  CacheEntry cache_[K9_CACHE];
  uint8_t cacheCount_ = 0, cacheHead_ = 0;
  Counters cnt_ = {0, 0, 0, 0, 0, 0};
};

}  // namespace k9
