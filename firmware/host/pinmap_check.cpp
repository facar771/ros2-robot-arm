// k9_pinmap.h tutarlılık denetimi (host). Çıktı: hata satırları, hata yoksa "OK".
#include <stdio.h>
#include "k9_config.h"
#include "../uno/src/k9_pinmap.h"

using namespace k9::pinmap;

int main() {
  int bad = 0;
  if (AXIS_COUNT != K9_AXES) { printf("AXIS_COUNT != K9_AXES\n"); bad++; }
  bool used[256] = {false};
  used[PIN_ESTOP_LOOP] = true;
  int servos = 0;
  for (int a = 0; a < AXIS_COUNT; a++) {
    const AxisMap& m = kAxes[a];
    for (int i = 0; i < m.n; i++) {
      uint8_t p = m.pin[i];
      if (p == NO_PIN) { printf("J%d pin[%d] NO_PIN ama n=%d\n", a + 1, i, m.n); bad++; continue; }
      if (p <= 1) { printf("J%d D0/D1 (USB-seri) kullanıyor\n", a + 1); bad++; }
      if (p > 19) { printf("J%d pin %d Uno'da yok\n", a + 1, p); bad++; }
      if (used[p]) { printf("pin %d birden fazla yerde\n", p); bad++; }
      used[p] = true;
    }
    if (m.drv == DRV_SERVO) {
      servos += m.n;
      if (m.confirmed) { printf("J%d servo 'onaylı' görünüyor (Ferhat teyit etmedi)\n", a + 1); bad++; }
      for (int i = 0; i < m.n; i++) if (m.pin[i] < A0) { printf("J%d servo pini A0..A5 dışında\n", a + 1); bad++; }
      if ((m.n == 2) != (m.mirrored == 1)) { printf("J%d mirrored ile n uyumsuz\n", a + 1); bad++; }
    }
  }
  if (servos != 6) { printf("toplam servo %d, 6 olmalı\n", servos); bad++; }
  // Ferhat'ın sözü: J3 ve J5 aynalı çift, J4 ve J6 tek.
  const int want[4] = {2, 1, 2, 1};
  for (int i = 0; i < 4; i++)
    if (kAxes[2 + i].drv != DRV_SERVO || kAxes[2 + i].n != want[i]) { printf("J%d servo sayısı yanlış\n", 3 + i); bad++; }
  // Donanımda doğrulanan step pinleri KİLİTLİ: değişirse bu test kırılır, bilerek güncellenmeli.
  const uint8_t j1[4] = {2, 3, 4, 5}, j2[4] = {6, 7, 8, 9}, j7[3] = {10, 11, 12};
  for (int i = 0; i < 4; i++) if (kAxes[0].pin[i] != j1[i] || kAxes[1].pin[i] != j2[i]) { printf("J1/J2 pinleri değişmiş\n"); bad++; break; }
  for (int i = 0; i < 3; i++) if (kAxes[6].pin[i] != j7[i]) { printf("J7 pinleri değişmiş\n"); bad++; break; }
  if (kAxes[0].drv != DRV_ULN2003 || kAxes[1].drv != DRV_ULN2003 || kAxes[6].drv != DRV_A4988 || !kAxes[0].confirmed ||
      !kAxes[1].confirmed || !kAxes[6].confirmed) { printf("step eksen sürücü/onay bayrağı yanlış\n"); bad++; }
  if (PIN_ESTOP_LOOP != 13) { printf("D13 değişmiş\n"); bad++; }
  if (!bad) printf("OK\n");
  return bad ? 1 : 0;
}
