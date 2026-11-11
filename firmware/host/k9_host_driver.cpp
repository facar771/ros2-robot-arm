// Host test sürücüsü: Uno çekirdeğini (k9_mcu.cpp) bilgisayarda çalıştırır. Python testi stdin/stdout ile konuşur.
//   F <hex>   baytları o anki zamanda besle        T <ms>   zamanı ayarla ve poll et
//   N <ms>    zamanı ayarla, poll ETME (geç gelen çerçeve testi)
//   S         sayaçları yaz                        X <n>    durumu zorla (yalnız test)
// K9_BENCH=1 derlemesinde (sahte HAL; argv[1]=="open": açılışta NC döngü açık):
//   L <0|1>   NC döngüsü kapalı(1)/açık(0)         P <n>    güçlü eksenleri hedefe doğru en çok n adım ilerlet
//   Q         "Q pos target power vmax accel" x 3 eksen (J1,J2,J7) + halt sayısı
// Her komuttan sonra çekirdeğin ürettiği baytlar "O <hex>" satırı olarak yazılır (boş olabilir).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

#include "k9_mcu.h"

#if K9_BENCH
struct FakeHal : k9::BenchHal {
  bool closed = true;
  int32_t pos[7] = {0}, tgt[7] = {0};
  uint32_t vmax[7] = {0}, accel[7] = {0};
  bool pw[7] = {false};
  int halts = 0;
  bool loopClosed() override { return closed; }
  void power(uint8_t a, bool on) override { pw[a] = on; }
  void moveTo(uint8_t a, int32_t t, uint32_t v, uint32_t ac) override { tgt[a] = t; vmax[a] = v; accel[a] = ac; }
  void stop(uint8_t a) override { tgt[a] = pos[a]; }
  void halt(uint8_t a) override { tgt[a] = pos[a]; halts++; }
  void setZero(uint8_t a) override { pos[a] = tgt[a] = 0; }
  int32_t position(uint8_t a) override { return pos[a]; }
  bool moving(uint8_t a) override { return pos[a] != tgt[a]; }
  void advance(int n) {
    for (int a = 0; a < 7; a++)
      for (int i = 0; i < n && pw[a] && pos[a] != tgt[a]; i++) pos[a] += (tgt[a] > pos[a]) ? 1 : -1;
  }
};
static FakeHal g_hal;
#endif
static std::string g_out;

static void sink(const uint8_t* d, uint8_t n, void*) {
  static const char* hx = "0123456789abcdef";
  for (uint8_t i = 0; i < n; i++) {
    g_out += hx[d[i] >> 4];
    g_out += hx[d[i] & 15];
  }
}

static void flush() {
  printf("O %s\n", g_out.c_str());
  fflush(stdout);
  g_out.clear();
}

int main(int argc, char** argv) {
  k9::Mcu mcu;
#if K9_BENCH
  if (argc > 1 && strcmp(argv[1], "open") == 0) g_hal.closed = false;
  mcu.setHal(&g_hal);
#else
  (void)argc;
  (void)argv;
#endif
  uint32_t now = 0;
  mcu.begin(7, 0, sink, 0);
  char line[4096];
  while (fgets(line, sizeof line, stdin)) {
    if (line[0] == 'F') {
      for (char* p = line + 2; p[0] && p[1] && p[0] != '\n'; p += 2) {
        char h[3] = {p[0], p[1], 0};
        mcu.feed((uint8_t)strtoul(h, 0, 16), now);
      }
    } else if (line[0] == 'T') {
      now = (uint32_t)strtoul(line + 2, 0, 10);
      mcu.poll(now);
    } else if (line[0] == 'N') {
      now = (uint32_t)strtoul(line + 2, 0, 10);
    } else if (line[0] == 'S') {
      const k9::Mcu::Counters& c = mcu.counters();
      printf("C ok=%u bad=%u cmds=%u dup=%u lost=%u seqdup=%u state=%u\n", c.framesOk, c.badFrames, c.cmds,
             c.dupCmds, c.lost, c.duplicates, mcu.state());
      fflush(stdout);
      continue;
#if K9_BENCH
    } else if (line[0] == 'L') {
      g_hal.closed = atoi(line + 2) != 0;
    } else if (line[0] == 'P') {
      g_hal.advance(atoi(line + 2));
    } else if (line[0] == 'Q') {
      printf("Q");
      static const int ax[3] = {0, 1, 6};
      for (int i = 0; i < 3; i++) {
        int a = ax[i];
        printf(" %d %d %d %u %u", (int)g_hal.pos[a], (int)g_hal.tgt[a], g_hal.pw[a] ? 1 : 0, (unsigned)g_hal.vmax[a],
               (unsigned)g_hal.accel[a]);
      }
      printf(" %d\n", g_hal.halts);
      fflush(stdout);
      continue;
#endif
    } else if (line[0] == 'X') {
#ifdef K9_HOST_TEST
      mcu.testForceState((uint8_t)atoi(line + 2));
#endif
    }
    flush();
  }
  return 0;
}
