#include "k9_mcu.h"

#include <string.h>

#include "k9_wire.h"

namespace k9 {

// protocol.py ile aynı değerler
enum : uint8_t {
  T_HEARTBEAT = 0x01, T_HELLO_REQ = 0x02,
  T_HOME = 0x10, T_ENABLE = 0x11, T_DISABLE = 0x12, T_MOVE = 0x13, T_STOP = 0x14, T_ESTOP = 0x15,
  T_CLEAR = 0x16, T_CONFIG = 0x17,
  T_HELLO = 0x81, T_STATUS = 0x82, T_ACK = 0x83, T_FAULT = 0x84
};
enum : uint8_t { S_UNHOMED = 0, S_HOMING = 1, S_HOMED = 2, S_ENABLED = 3, S_ESTOPPED = 4 };
enum : uint8_t { R_NONE = 0, R_HW_BUTTON = 1, R_HOST_CMD = 2, R_WATCHDOG = 3, R_BOOT = 7 };
enum : uint8_t {
  RES_OK = 0, RES_BAD_STATE = 1, RES_BAD_ARG = 2, RES_LIMIT = 3, RES_NO_REFERENCE = 4,
  RES_ESTOP_ACTIVE = 5, RES_HW_LOOP_OPEN = 6, RES_BUSY = 7, RES_UNKNOWN_CMD = 8
};
enum : uint8_t { K_WATCHDOG_MS = 1, K_SOFT_MIN = 2, K_SOFT_MAX = 3, K_FOLLOW_STEPS = 4, K_FOLLOW_MS = 5 };
enum : uint8_t { F_HW_ESTOP = 1, F_WATCHDOG = 2 };
enum : uint16_t { SF_HW_LOOP_OPEN = 0x0001, SF_OPEN_LOOP_ANY = 0x0004, SF_HOST_LINK_OK = 0x0008, SF_BENCH_MODE = 0x0010 };
enum : uint8_t { AF_MOVING = 0x01, AF_HOMED = 0x02, AF_ENABLED = 0x04, AF_OPEN_LOOP = 0x20 };
enum : uint8_t { FLAG_AUTH = 0x01, FLAG_RETRY = 0x02 };

static inline uint16_t rd16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline int32_t rd32(const uint8_t* p) {
  return (int32_t)((uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
}
static inline void wr16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void wr32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

void Mcu::begin(uint16_t bootCount, uint32_t now, TxSink sink, void* ctx) {
  sink_ = sink;
  ctx_ = ctx;
  bootCount_ = bootCount;
  t0_ = now;
  now_ = now;
  nextStatus_ = now;
  state_ = S_UNHOMED;  // açılışta hareketsiz; Faz 1'de NC döngü girişi yok (bkz. FAZ0_PLAN)
  reason_ = R_NONE;
#if K9_BENCH
  if (hal_) {
    benchAllOff();
    if (!hal_->loopClosed()) {  // açılışta döngü açık / bağlı değil: kilitli başla
      loopOpen_ = true;
      state_ = S_ESTOPPED;
      reason_ = R_BOOT;
    }
  }
#endif
  for (uint8_t i = 0; i < K9_AXES; i++) {
    softMin_[i] = hardMin(i);
    softMax_[i] = hardMax(i);
  }
}

// ---------------------------------------------------------------- alım
void Mcu::feed(uint8_t b, uint32_t now) {
  if (b == 0) {
    if (overflow_) {
      overflow_ = false;
    } else if (rxLen_) {
      handleWire(rxLen_, now);
    }
    rxLen_ = 0;
    return;
  }
  if (overflow_) return;
  if (rxLen_ >= K9_MAX_WIRE) {  // ayraçsız çok uzun dizi: bir sonraki 0x00'a kadar at
    cnt_.badFrames++;
    rxLen_ = 0;
    overflow_ = true;
    return;
  }
  rx_[rxLen_++] = b;
}

void Mcu::handleWire(uint8_t n, uint32_t now) {
  int16_t raw = cobsDecodeInPlace(rx_, n);
  if (raw < K9_HDR + 2) { cnt_.badFrames++; return; }
  const uint8_t* r = rx_;
  uint8_t ver = r[0], flags = r[1], type = r[2], plen = r[3];
  if (flags & FLAG_AUTH) { cnt_.badFrames++; return; }  // Uno HMAC doğrulamaz (K5): kimliksiz taşımada reddedilir
  if (raw != K9_HDR + plen + 2) { cnt_.badFrames++; return; }
  if (crc16(r, K9_HDR + plen) != rd16(r + K9_HDR + plen)) { cnt_.badFrames++; return; }
  if (ver != 1 || (flags & ~FLAG_RETRY)) { cnt_.badFrames++; return; }
  cnt_.framesOk++;
  handleFrame(type, flags, rd16(r + 4), r + K9_HDR, plen, now);
}

void Mcu::handleFrame(uint8_t type, uint8_t /*flags*/, uint16_t seq, const uint8_t* p, uint8_t plen, uint32_t now) {
  now_ = now;
  watchdogCheck(now);  // geç gelen çerçeve sessizliği silmez (sim ile aynı sıra)
  // seq izleyici (SeqTracker, strict=false)
  if (!rxHas_) {
    rxHas_ = true;
    rxLast_ = seq;
    rxExpected_ = (uint16_t)(seq + 1);
  } else {
    uint16_t diff = (uint16_t)(seq - rxExpected_);
    if (diff < 0x8000) {
      cnt_.lost += diff;
      rxExpected_ = (uint16_t)(seq + 1);
      rxLast_ = seq;
    } else {
      cnt_.duplicates++;
      return;  // yineleme/eski çerçeve işlenmez
    }
  }
  lastHost_ = now;
  hostSeen_ = true;
  linkOk_ = true;

  if (type == T_HEARTBEAT) return;
  if (type == T_HELLO_REQ) { sendHello(); return; }
  if (type < T_HOME || type > T_CONFIG) {
    if (type >= 0x10 && type <= 0x1F) reply(plen >= 2 ? rd16(p) : -1, RES_UNKNOWN_CMD, 0);
    return;  // MCU->host türü ya da bilinmeyen: yok say
  }
  execute(type, p, plen, plen >= 2 ? (int32_t)rd16(p) : -1);
}

// ---------------------------------------------------------------- komutlar
void Mcu::execute(uint8_t type, const uint8_t* p, uint8_t plen, int32_t cid) {
  uint8_t res, det;
  if (cid >= 0 && cacheFind((uint16_t)cid, &res, &det)) {  // yineleme: yeniden ACK, yeniden işlem YOK
    cnt_.dupCmds++;
    wr16(txPayload(), (uint16_t)cid);
    txPayload()[2] = res;
    txPayload()[3] = det;
    sendFrame(T_ACK, 4);
    return;
  }
  det = 0;
  res = runCommand(type, p, plen, &det);
  cnt_.cmds++;
  reply(cid, res, det);
}

uint8_t Mcu::runCommand(uint8_t type, const uint8_t* p, uint8_t plen, uint8_t* detail) {
  // yük uzunluğu tam eşleşmeli (struct.unpack gibi)
  uint8_t need = (type == T_HOME || type == T_STOP) ? 3 : (type == T_MOVE) ? 15 : (type == T_CONFIG) ? 8 : 2;
  if (plen != need) return RES_BAD_ARG;

  if (type == T_ESTOP) { enterEstop(R_HOST_CMD, false, 255, 0); return RES_OK; }
  if (type == T_CLEAR) {
    if (state_ != S_ESTOPPED) return RES_OK;
#if K9_BENCH
    if (hal_ && (loopOpen_ || !hal_->loopClosed())) return RES_HW_LOOP_OPEN;  // döngü açıkken kilit AÇILMAZ
    homedMask_ = enabledMask_ = 0;  // yeniden sıfırlama gerekir
#endif
    // Faz 1: NC döngü girişi yok, kilit yalnız CLEAR ile açılır. Eksenler hiç referanslanmadı -> UNHOMED.
    reason_ = R_NONE;
    state_ = S_UNHOMED;
    linkOk_ = true;
    return RES_OK;
  }
  if (state_ == S_ESTOPPED) return RES_ESTOP_ACTIVE;
#if K9_BENCH
  if (hal_ && type != T_CONFIG) return benchCommand(type, p, detail);
#endif
  switch (type) {
    case T_STOP:
      return RES_OK;  // hiçbir şey hareket etmiyor
    case T_CONFIG:
      return doConfig(p, detail);
    case T_HOME: {
      if (state_ != S_UNHOMED && state_ != S_HOMED) return RES_BAD_STATE;
      uint8_t mask = p[2];
      if (mask == 0 || (mask >> K9_AXES)) return RES_BAD_ARG;
      // Hiçbir eksende home anahtarı/encoder yok: HARDWARE.md §7 gereği referanslanamaz, HOMED olamaz.
      return RES_NO_REFERENCE;
    }
    case T_ENABLE:
      if (state_ == S_ENABLED) return RES_OK;
      if (state_ != S_HOMED) return RES_NO_REFERENCE;  // referanslanamayan eksen var
      return RES_NO_REFERENCE;
    case T_DISABLE:
      if (state_ != S_ENABLED) return state_ == S_HOMED ? RES_OK : RES_BAD_STATE;
      return RES_BUSY;  // Faz 1'de ulaşılamaz
    case T_MOVE:
      return RES_BAD_STATE;  // yalnız ENABLED'da hareket edilir; Faz 1'de ENABLED'a ulaşılamaz
    default:
      return RES_UNKNOWN_CMD;
  }
}

uint8_t Mcu::doConfig(const uint8_t* p, uint8_t* detail) {
  uint8_t key = p[2], axis = p[3];
  int32_t v = rd32(p + 4);
  if (key == K_WATCHDOG_MS) {
    if (v < K9_WATCHDOG_MIN_MS || v > K9_WATCHDOG_MAX_MS) return RES_BAD_ARG;
    watchdogMs_ = (uint16_t)v;
    return RES_OK;
  }
  if (key < K_WATCHDOG_MS || key > K_FOLLOW_MS) return RES_BAD_ARG;
  if (axis >= K9_AXES) return RES_BAD_ARG;
  if (key == K_SOFT_MIN) {
    if (v < hardMin(axis)) { *detail = 1; return RES_LIMIT; }  // gömülü limit PC'den genişletilemez
    if (v > softMax_[axis]) return RES_BAD_ARG;
    softMin_[axis] = v;
    return RES_OK;
  }
  if (key == K_SOFT_MAX) {
    if (v > hardMax(axis)) { *detail = 1; return RES_LIMIT; }
    if (v < softMin_[axis]) return RES_BAD_ARG;
    softMax_[axis] = v;
    return RES_OK;
  }
  return RES_BAD_ARG;  // FOLLOW_ERR_*: encoder yok
}

// ---------------------------------------------------------------- watchdog, acil dur
void Mcu::watchdogCheck(uint32_t now) {
  if (!hostSeen_) return;
  uint32_t silent = now - lastHost_;
  if (silent > watchdogMs_) {
    linkOk_ = false;
    if (state_ == S_ENABLED || state_ == S_HOMING) {
      enterEstop(R_WATCHDOG, true, 255, (int32_t)silent);
    }
  }
}

void Mcu::enterEstop(uint8_t reason, bool fault, uint8_t axis, int32_t detail) {
  state_ = S_ESTOPPED;
  reason_ = reason;
#if K9_BENCH
  if (hal_) benchAllOff();  // adım üretimi durur, bobin/EN kapanır, kilitli; CLEAR + yeniden sıfır gerekir
#endif
  if (fault) sendFault(reason == R_HW_BUTTON ? F_HW_ESTOP : F_WATCHDOG, axis, detail);
  sendStatus();
}

void Mcu::poll(uint32_t now) {
  now_ = now;
#if K9_BENCH
  if (hal_) benchMonitorLoop(now);
#endif
  watchdogCheck(now);
  if ((int32_t)(now - nextStatus_) >= 0) {
    sendStatus();
    nextStatus_ = now + K9_STATUS_PERIOD_MS;
  }
}

#if K9_BENCH
// ---------------------------------------------------------------- tezgâh (Faz 2a)
void Mcu::benchAllOff() {
  for (uint8_t a = 0; a < K9_AXES; a++) {
    if (!isBenchAxis(a)) continue;
    hal_->halt(a);
    hal_->power(a, false);
  }
  homedMask_ = 0;
  enabledMask_ = 0;
}

// NC döngüsü: HIGH/bağlı değil = açık. 5 ms üst üste açık okunursa ESTOPPED/HW_BUTTON (yalnız gürültü süzgeci;
// asıl koruma EN hattındaki donanımdır, HARDWARE.md §6).
void Mcu::benchMonitorLoop(uint32_t now) {
  if (hal_->loopClosed()) {
    openPending_ = false;
    loopOpen_ = false;
    return;
  }
  if (!openPending_) {
    openPending_ = true;
    openT_ = now;
    return;
  }
  if (!loopOpen_ && (uint32_t)(now - openT_) >= 5) {
    loopOpen_ = true;
    if (state_ != S_ESTOPPED) enterEstop(R_HW_BUTTON, true, 255, 0);
  }
}

uint8_t Mcu::benchCommand(uint8_t type, const uint8_t* p, uint8_t* detail) {
  switch (type) {
    case T_STOP: {
      uint8_t mask = p[2];
      for (uint8_t a = 0; a < K9_AXES; a++)
        if ((mask & (1u << a)) && isBenchAxis(a)) hal_->stop(a);
      return RES_OK;
    }
    case T_HOME: {  // tezgâh sıfırı: durgun + DISABLED eksende mevcut konum 0 olur
      if (state_ != S_UNHOMED && state_ != S_HOMED) return RES_BAD_STATE;
      uint8_t mask = p[2];
      if (mask == 0 || (mask & ~BENCH_MASK)) return RES_BAD_ARG;  // servo eksenleri bu derlemede yok
      for (uint8_t a = 0; a < K9_AXES; a++)
        if ((mask & (1u << a)) && hal_->moving(a)) return RES_BUSY;
      for (uint8_t a = 0; a < K9_AXES; a++) {
        if (!(mask & (1u << a))) continue;
        hal_->setZero(a);
        homedMask_ |= (uint8_t)(1u << a);
      }
      state_ = S_HOMED;
      return RES_OK;
    }
    case T_ENABLE:
      if (state_ == S_ENABLED) return RES_OK;
      if (state_ != S_HOMED || homedMask_ == 0) return RES_NO_REFERENCE;  // sıfırlanmadan ENABLE yok
      if (loopOpen_ || !hal_->loopClosed()) return RES_HW_LOOP_OPEN;
      for (uint8_t a = 0; a < K9_AXES; a++)
        if (homedMask_ & (1u << a)) hal_->power(a, true);  // yalnız sıfırlanan eksenler enerjilenir
      enabledMask_ = homedMask_;
      state_ = S_ENABLED;
      return RES_OK;
    case T_DISABLE:
      if (state_ == S_HOMED) return RES_OK;
      if (state_ != S_ENABLED) return RES_BAD_STATE;
      for (uint8_t a = 0; a < K9_AXES; a++)
        if (isBenchAxis(a) && hal_->moving(a)) return RES_BUSY;  // hareket ortasında kesmek adım kaybettirir: önce STOP
      for (uint8_t a = 0; a < K9_AXES; a++)
        if (isBenchAxis(a)) hal_->power(a, false);
      enabledMask_ = 0;
      state_ = S_HOMED;  // konum korunur (eksen durgundu); yeniden ENABLE mümkün
      return RES_OK;
    case T_MOVE: {
      if (state_ != S_ENABLED) return RES_BAD_STATE;
      uint8_t axis = p[2];
      if (axis >= K9_AXES || !isBenchAxis(axis)) return RES_BAD_ARG;
      if (!(enabledMask_ & (1u << axis))) return RES_BAD_STATE;
      int32_t target = rd32(p + 3);
      uint32_t vmax = (uint32_t)rd32(p + 7), accel = (uint32_t)rd32(p + 11);
      if (vmax == 0 || accel == 0) return RES_BAD_ARG;
      if (vmax > hardVmax(axis)) { *detail = 2; return RES_LIMIT; }    // gömülü hız sınırı
      if (accel > hardAccel(axis)) { *detail = 3; return RES_LIMIT; }  // gömülü ivme sınırı
      if (target < softMin_[axis] || target > softMax_[axis]) return RES_LIMIT;
      if (hal_->moving(axis)) return RES_BUSY;  // önce STOP; hedef değiştirme yok
      hal_->moveTo(axis, target, vmax, accel);
      return RES_OK;
    }
    default:
      return RES_UNKNOWN_CMD;
  }
}
#endif

// ---------------------------------------------------------------- gönderim
void Mcu::sendFrame(uint8_t type, uint8_t plen) {
  if (!sink_) return;
  uint8_t* b = tx_ + 1;
  b[0] = 1;  // sürüm
  b[1] = 0;  // bayraklar
  b[2] = type;
  b[3] = plen;
  wr16(b + 4, txSeq_++);
  wr16(b + 6, rxHas_ ? rxLast_ : 0);
  uint8_t n = K9_HDR + plen;
  wr16(b + n, crc16(b, n));
  n += 2;
  cobsEncodeInPlace(tx_, n);
  sink_(tx_, n + 2, ctx_);
}

void Mcu::sendHello() {
  uint8_t* p = txPayload();
  p[0] = 1;  // proto
  p[1] = K9_FW_MAJOR;
  p[2] = K9_FW_MINOR;
  p[3] = K9_AXES;
  wr16(p + 4, bootCount_);
  static const char id[8] = {'K', '9', '-', 'U', 'N', 'O', 0, 0};
  memcpy(p + 6, id, 8);
  uint8_t o = 14;
  for (uint8_t i = 0; i < K9_AXES; i++) {  // encoder_kipi (0 = yok), bayrak (home/limit anahtarı yok)
    p[o++] = 0;
    p[o++] = 0;
  }
  sendFrame(T_HELLO, o);
}

void Mcu::sendStatus() {
  uint8_t* p = txPayload();
  p[0] = state_;
  p[1] = reason_;
  uint16_t sf = (uint16_t)(SF_OPEN_LOOP_ANY | (linkOk_ ? (uint16_t)SF_HOST_LINK_OK : (uint16_t)0));
#if K9_BENCH
  sf = (uint16_t)(sf | SF_BENCH_MODE | (loopOpen_ ? (uint16_t)SF_HW_LOOP_OPEN : (uint16_t)0));
#endif
  wr16(p + 2, sf);
  wr32(p + 4, millisSince());
  wr16(p + 8, watchdogMs_);
  p[10] = K9_AXES;
  uint8_t o = 11;
  for (uint8_t i = 0; i < K9_AXES; i++) {  // konum = komut sayacı (kestirim), encoder yok
    uint8_t af = AF_OPEN_LOOP;
    int32_t pos = 0;
#if K9_BENCH
    if (hal_ && isBenchAxis(i)) {
      pos = hal_->position(i);
      if (hal_->moving(i)) af |= AF_MOVING;
      if (homedMask_ & (1u << i)) af |= AF_HOMED;
      if (enabledMask_ & (1u << i)) af |= AF_ENABLED;
    }
#endif
    wr32(p + o, (uint32_t)pos);
    wr32(p + o + 4, 0);
    p[o + 8] = af;
    o += 9;
  }
  sendFrame(T_STATUS, o);
}

void Mcu::sendFault(uint8_t code, uint8_t axis, int32_t detail) {
  uint8_t* p = txPayload();
  p[0] = code;
  p[1] = axis;
  wr32(p + 2, (uint32_t)detail);
  wr32(p + 6, millisSince());
  sendFrame(T_FAULT, 10);
}

void Mcu::reply(int32_t cid, uint8_t result, uint8_t detail) {
  uint16_t id = (uint16_t)cid;  // cid < 0 -> 0xFFFF (sim ile aynı)
  if (cid >= 0) cacheStore(id, result, detail);
  uint8_t* p = txPayload();
  wr16(p, id);
  p[2] = result;
  p[3] = detail;
  sendFrame(T_ACK, 4);
  sendStatus();  // durum değiştiyse host hemen görsün
}

// ---------------------------------------------------------------- önbellek
bool Mcu::cacheFind(uint16_t id, uint8_t* res, uint8_t* det) const {
  for (uint8_t i = 0; i < cacheCount_; i++) {
    if (cache_[i].id == id) { *res = cache_[i].res; *det = cache_[i].det; return true; }
  }
  return false;
}

void Mcu::cacheStore(uint16_t id, uint8_t res, uint8_t det) {
  // aynı id zaten varsa yerinde güncelle (sim sözlüğü gibi)
  for (uint8_t i = 0; i < cacheCount_; i++) {
    if (cache_[i].id == id) { cache_[i].res = res; cache_[i].det = det; return; }
  }
  cache_[cacheHead_] = {id, res, det};
  cacheHead_ = (uint8_t)((cacheHead_ + 1) % K9_CACHE);
  if (cacheCount_ < K9_CACHE) cacheCount_++;
}

}  // namespace k9
