# Robot Kontrol Merkezi (Control Hub)

Robot **senaryolarını** bilgisayardan ve telefondan (aynı Wi-Fi) çalıştıran web arayüzü. Robot
yığınından (ROS) ayrı çalışır, mevcut paketlere dokunmaz.

> Durum: **PR-H1** — çekirdek + web arayüzü, sahte robotla (`--fake`). Gerçek robota bağlantı PR-H2,
> Windows `.exe` PR-H3, ESP32 cihazları + kural motoru PR-H4.

## Hızlı başlangıç

```bash
# WSL'de
cd ~/ros2-robot-arm/apps/control_hub
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m control_hub --fake
```
Terminalde adresler, **PIN** ve (aynı Wi-Fi'dan) telefon için **QR kod** çıkar. Bilgisayarda
`http://localhost:8765`, telefonda `http://<bilgisayarın IP'si>:8765`. Docker ile:
`docker compose up --build hub-fake` (PIN'i loglarda yazar; sabitlemek için `HUB_PIN=123456 docker compose up hub-fake`).

Seçenekler: `--port`, `--host`, `--access-mode open|pin|locked`, `--pin`, `--config-dir`,
`--fake-speed 0.2` (sahte robotu 5 kat hızlandırır), `--allow-host`, `--check-config`.

## Yapı

```
control_hub/
  hub_core/   GUI'siz, ROS'suz çekirdek (tüm mantık burada, test edilir)
    backend.py        RobotBackend protokolü (execute / emergency_stop / snapshot ...)
    fake_backend.py   donanımsız simülasyon (PR-H2: RobotApiBackend eklenir)
    commands.py       robot komut beyaz listesi (home | objects | above <renk> [m])
    scenarios.py      scenarios.yaml + ui.config.json okuma/doğrulama
    runner.py         ScenarioRunner: tek anda tek senaryo, iptal edilebilir
    hub.py            Hub: hepsini birleştirir + ACİL DUR kilidi
    access.py         open|pin|locked, PIN, oturum, deneme sınırı
    events.py         olay günlüğü
  server/     FastAPI + WebSocket (çekirdeği HTTP'ye açar)
  web/        düz HTML/CSS/JS, derleme yok, mobil öncelikli, PWA manifest
config/       scenarios.yaml (butonlar), ui.config.json (yerleşim + tema)
tests/
```

## Arayüzü değiştirmek = küçük dosya değişikliği

* **Yeni senaryo / buton:** `config/scenarios.yaml`'a blok ekle, sonra `config/ui.config.json`'daki
  `layout.sections[].scenarios` listesine id'yi yaz. Adımlar `robot: <komut>` ya da `wait: <sn>`.
* **Buton boyutu, sütun sayısı, ACİL DUR'un yeri (üst/alt), panel sırası, renkler:** `ui.config.json`.
* Hatalı dosya Hub'ı **açılışta** durdurur ve tüm hataları birlikte listeler:
  `python -m control_hub --fake --check-config`.

## Güvenlik

| Konu | Davranış |
|---|---|
| Erişim kipi | `pin` (varsayılan): PIN'siz hiçbir şey görünmez. `open`: kimlik yok (yalnızca güvenilir ağ). `locked`: arayüz salt okunur, kontrol uç noktaları `403`. |
| PIN | Her açılışta rastgele 6 hane (ya da `--pin`); yalnızca terminalde/masaüstünde gösterilir, API'de asla. Oturum çerezi `HttpOnly; SameSite=Strict`, 12 saat. |
| Deneme sınırı | İstemci başına 5 hatalı PIN → 60 sn kilit; tüm istemciler toplamı 20 hata → 5 dk. (Docker'da istemciler ağ geçidi adresiyle aynı görünebilir; o durumda kilit herkese uygulanır.) |
| Başka siteden saldırı | `Host` yalnızca IP / `localhost` / `*.local` / `--allow-host` (DNS rebinding); `/api` POST'ları `application/json` ister; yabancı `Origin`/`cross-site` `403`; sıkı CSP. |
| **ACİL DUR** | Her kipte ve **oturumsuz** çalışır; senaryoyu keser, kilitler (`423`), kilidi açmak (`/api/estop/reset`) kontrol yetkisi ister. Bedeli: ağdaki herkes robotu durdurabilir; bilinçli seçim. |

**İnternete açma, router'da port yönlendirme YAPMA.** Hub ev ağı içindir; PIN, şifreli olmayan HTTP
üzerinden gider. Dışarıdan erişim gerekiyorsa bir VPN (ör. WireGuard/Tailscale) kullan.

## HTTP API

| Uç nokta | Yetki | Anlam |
|---|---|---|
| `GET /health` | yok | `{ok, version, backend}` |
| `GET /api/session` | yok | kip, `authenticated`, `can_control`, başlık, tema |
| `POST /api/login` `{pin}` | yok | `200` + çerez / `401` (`attempts_left`) / `429` (`retry_after_s`) |
| `POST /api/logout` | — | oturumu kapatır |
| `GET /api/ui`, `/api/state`, `/api/events?since=` | görüntüleme | arayüz yapılandırması, canlı durum, olaylar |
| `POST /api/scenarios/{id}/run` | kontrol | `202` / `404` / `409` meşgul / `423` ACİL DUR kilitli |
| `POST /api/scenarios/cancel` | kontrol | çalışan senaryoyu iptal eder (kilitlemez) |
| `POST /api/estop` | **yok** | ACİL DUR |
| `POST /api/estop/reset` | kontrol | kilidi açar (`409` senaryo henüz durmadıysa) |
| `WS /ws` | görüntüleme | `{"type":"state"}` (değişince) ve `{"type":"events"}` akışı |

Hata gövdesi: `{"ok": false, "error": "<kod>", "message": "<Türkçe>"}`.

## Testler

```bash
# WSL'de
cd ~/ros2-robot-arm/apps/control_hub
pip install -r requirements-dev.txt
ruff check . && python -m pytest
```
Ayrıca CI'daki `hub` işi Docker imajını derleyip `/health`'i ve PIN korumasını dener.

## Bilinen sınırlar (PR-H1)

* Robot **sahte**: eklem değerleri yalnızca görsel; gerçek kinematik yok.
* Canlı durum 5 Hz yoklanır (200 ms'den kısa olaylar arayüzde görünmeyebilir).
* Gerçek robot API'sinde şu an hareketi ortasında kesen bir uç nokta yok (`/api/stop` ayrı küçük bir
  PR olacak); o zamana dek gerçek robotta ACİL DUR yalnızca **sonraki adımları** engeller. Backend
  bunu `can_interrupt` ile bildirir ve arayüz uyarır.
