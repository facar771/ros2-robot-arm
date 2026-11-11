// Host test sürücüsü: Uno çekirdeğini (k9_mcu.cpp) bilgisayarda çalıştırır. Python testi stdin/stdout ile konuşur.
//   F <hex>   baytları o anki zamanda besle        T <ms>   zamanı ayarla ve poll et
//   N <ms>    zamanı ayarla, poll ETME (geç gelen çerçeve testi)
//   S         sayaçları yaz                        X <n>    durumu zorla (yalnız test)
// Her komuttan sonra çekirdeğin ürettiği baytlar "O <hex>" satırı olarak yazılır (boş olabilir).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

#include "k9_mcu.h"

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

int main() {
  k9::Mcu mcu;
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
    } else if (line[0] == 'X') {
#ifdef K9_HOST_TEST
      mcu.testForceState((uint8_t)atoi(line + 2));
#endif
    }
    flush();
  }
  return 0;
}
