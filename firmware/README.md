# firmware/ (K9 Faz 1: HAREKETSİZ; Faz 2a: tezgâh derlemesi aşağıda)

**Bu fazda hiçbir motor/servo sürülmez.** Uno hiçbir motor pinine yazmaz; `K9_MOTION=1` derlemesi bilerek durur (hareket kodu Faz 2'dir ve Robot_1'in "tezgâh modu" kararını bekler).
Gerçek donanımda hiçbir şey denenmedi. Plan ve pin tablosu: [`FAZ0_PLAN.md`](FAZ0_PLAN.md) (PR #77).

| Klasör | Ne |
|---|---|
| `uno/` | Arduino Uno firmware'i: COBS+CRC-16+seq çerçeveleri, HELLO/STATUS/ACK, watchdog (300 ms, yalnız PC çerçeveleri). PlatformIO `env:uno` |
| `esp_relay/` | ESP8266 NodeMCU **şeffaf aktarıcı**: Wi-Fi TCP (port 8770, tek istemci) ↔ UART. Kalp atışı ÜRETMEZ. PlatformIO `env:nodemcuv2` |
| `host/` | Uno çekirdeğini bilgisayarda `robot_hw_bridge` kodeğiyle karşılaştıran testler (g++, pytest) |
| `tools/k9_probe.py` | Duman testi: bağlanır, HELLO/STATUS okur, **hiçbir hareket komutu göndermez** |

## Kablolama (Faz 1, yalnız haberleşme)
- **Kablo yolu (ilk adım):** PC USB → Uno USB. ESP bağlı DEĞİL (ESP'nin TX/RX tellerini Uno'dan sök).
- **Wi-Fi yolu:** ESP TX(GPIO1) → Uno D0; Uno D1 → **gerilim bölücü (1 kΩ + 2 kΩ)** → ESP RX(GPIO3); GND–GND. Uno'yu USB'den DEĞİL, harici beslemeden çalıştır (D0/D1 aynı anda USB ile paylaşılamaz). ESP'yi harici 5 V (VIN) ile besle.
- Uno'ya program yüklerken D0/D1 tellerini sök.

## Güvenlik notları
- Wi-Fi yolunda kimlik doğrulama (HMAC) YOK (K5). Yalnız kendi ağında ve Faz 1'de (hareket yok) kullan.
- Wi-Fi şifresi `esp_relay/include/wifi_config.h` içindedir (gitignore'lu); asla commit etme.
- Uno, `AUTH` bayraklı (HMAC'li) çerçeveyi reddeder; HMAC Wi-Fi tarafında K5 ile çözülecek.
- Uno gömülü eksen limitleri 0..0'dır; PC `CONFIG SOFT_MIN/MAX` ile genişletemez (`LIMIT` döner).

## Testler
```
PYTHONPATH=src/robot_hw_bridge python3 -m pytest firmware/host -q     # g++ gerekir
```

## Faz 2a: tezgâh derlemesi (`env:uno_bench`, `K9_BENCH=1`)

Yalnız **yükten ayrık** motorla, operatör başında, güç kesmeye hazırken. `pio run -e uno_bench -t upload --upload-port COM3`.
Bayraksız `env:uno` bugünkü hareketsiz hali korur.

* Eksenler: J1, J2 (28BYJ-48/ULN2003, yarım adım, AccelStepper HALF4WIRE; pin sırası IN1,IN3,IN2,IN4), J7 (A4988 STEP=D10, DIR=D11, EN=D12 aktif düşük). Servolar yok (Faz 2b).
* NC döngü: **D13**, `INPUT_PULLUP`; LOW = kapalı/OK. HIGH ya da bağlı değil = `HW_LOOP_OPEN` + `ESTOPPED` (açılışta `BOOT`). Yani D13'ü GND'ye bağlamadan hareket yok.
* Gömülü limitler (PC yalnız daraltır, `LIMIT` döner): J1,J2 ±256 yarım adım, ≤300 adım/sn, ≤300 adım/sn²; J7 ±50 adım, ≤100 adım/sn, ≤100 adım/sn² (J7 ivmesi önerilen değerdir).
* `HOME` = tezgâh sıfırı (yalnız durgun + DISABLED eksen; mevcut konum 0). `ENABLE` yalnız sıfırlanan eksenleri enerjiler. `MOVE` yalnız ENABLED, hareket varken `BUSY` (önce `STOP`).
* Watchdog 300 ms, host `ESTOP`, döngü açılması: adım üretimi durur, bobin/EN kapanır, kilitli; `CLEAR` + yeniden sıfır gerekir. Döngü açıkken `CLEAR` → `HW_LOOP_OPEN`.
* Seri trafiğin adım darbesine etkisi (simavr **benzetimi**, gerçek ELF, 16 MHz): `python3 firmware/tools/simavr_jitter/run_jitter.py <fw.elf> <jitter>` (kaynak: `tools/simavr_jitter/`).
  Gerçek donanımda ölçülmedi.
