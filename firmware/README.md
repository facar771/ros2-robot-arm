# firmware/ (K9 Faz 1: HAREKETSİZ)

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
