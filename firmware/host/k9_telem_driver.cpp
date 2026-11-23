// Host test sürücüsü: pot telemetri çekirdeği (k9_telem.h) SİMÜLE ADC ile. Python testi stdin/stdout ile konuşur.
//   C <rate> <settle_us>   örnekleyiciyi yeniden kur        V <idx> <değer>  idx potunun sahte ADC değeri (0..)
//   Z <ms> <us>            saatleri ayarla (ayrı: millis/micros)
//   R <ms>                 100 µs adımlarla ms kadar ilerlet; her çerçeve "F <hex>" satırı
//   X                      reset()  (bağlantı koptu)
// Her komut "OK <ihlal sayısı>" ile biter. İhlal = pot sürme kuralı bozuldu: aynı anda 1'den çok pot sürülüyor,
// okuma sırasında seçili pot sürülmüyor, ya da bekleme süresinden önce okuma.
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "k9_telem.h"

struct Sim {
  bool driven[k9t::kPots] = {false};
  uint16_t val[k9t::kPots] = {512, 512, 512};
  int viol = 0, reads = 0;
  uint32_t now_us = 0, drive_us = 0;
  uint32_t settle = K9_TELEM_SETTLE_US;
  int sel = -1;
};
static Sim g;

static void drive(void*, uint8_t i) {
  for (int k = 0; k < k9t::kPots; k++)
    if (g.driven[k]) g.viol++;  // başka pot hâlâ sürülüyor
  g.driven[i] = true;
  g.sel = i;
  g.drive_us = g.now_us;
}
static void release(void*, uint8_t i) {
  g.driven[i] = false;
  if (g.sel == i) g.sel = -1;
}
static uint16_t readAdc(void*) {
  int n = 0;
  for (int k = 0; k < k9t::kPots; k++) n += g.driven[k];
  if (n != 1 || g.sel < 0 || !g.driven[g.sel]) g.viol++;
  if ((uint32_t)(g.now_us - g.drive_us) < g.settle) g.viol++;
  g.reads++;
  return g.sel >= 0 ? g.val[g.sel] : 0;
}

int main() {
  k9t::Hal hal = {nullptr, drive, release, readAdc};
  k9t::Config cfg;
  k9t::Sampler* s = new k9t::Sampler(hal, cfg);
  uint32_t ms = 0;
  char line[128];
  while (fgets(line, sizeof line, stdin)) {
    unsigned a = 0, b = 0;
    if (line[0] == 'C' && sscanf(line + 1, "%u %u", &a, &b) == 2) {
      delete s;
      cfg.rate_hz = a;
      cfg.settle_us = b;
      g = Sim();
      g.settle = b < 200 ? 200 : (b > 20000 ? 20000 : b);
      s = new k9t::Sampler(hal, cfg);
    } else if (line[0] == 'V' && sscanf(line + 1, "%u %u", &a, &b) == 2 && a < k9t::kPots) {
      g.val[a] = (uint16_t)b;
    } else if (line[0] == 'Z' && sscanf(line + 1, "%u %u", &a, &b) == 2) {
      ms = a;
      g.now_us = b;
    } else if (line[0] == 'X') {
      s->reset();
    } else if (line[0] == 'R' && sscanf(line + 1, "%u", &a) == 1) {
      for (unsigned i = 0; i < a * 10; i++) {
        g.now_us += 100;
        if (i % 10 == 9) ms++;
        uint8_t out[k9t::kWireMax];
        size_t n;
        if (s->step(ms, g.now_us, out, n)) {
          printf("F ");
          for (size_t k = 0; k < n; k++) printf("%02x", out[k]);
          printf("\n");
        }
      }
    }
    printf("OK %d\n", g.viol);
    fflush(stdout);
  }
  return 0;
}
