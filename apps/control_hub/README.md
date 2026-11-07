# Robot Kontrol Merkezi (Control Hub)

Robot **senaryolarını** bilgisayardan ve telefondan (aynı Wi-Fi) çalıştıran web arayüzü. Robot
yığınından (ROS) ayrı çalışır, mevcut paketlere dokunmaz.

> Durum: **H1** çekirdek + web arayüzü · **H2** gerçek robota bağlantı (Robot API v1) (ikisi de birleşti) ·
> **H3** Windows `.exe` (bu PR) · sırada: ESP32 cihazları + kural motoru (H4).

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

## Gerçek robota bağlanmak (Robot API v1)

`--fake` olmadan Hub robotun HTTP API'sine bağlanır (`docs/INTERFACES.md`, "Robot API v1"):

```bash
# Terminal 1 — WSL'de (robot yığını: Gazebo + MoveIt + algı + komutlar açık web_view; "Hazır." yazısını bekle)
cd ~/ros2-robot-arm && docker compose up --build demo
```
```bash
# Terminal 2 — WSL'de (Hub; robot API'si Docker'ın yayımladığı 127.0.0.1:8080'de)
cd ~/ros2-robot-arm/apps/control_hub
python -m control_hub --robot-url http://127.0.0.1:8080 --pin 123456
```
Windows'ta (`.exe` gelene dek) aynısı PowerShell'de: `python -m control_hub --robot-url http://127.0.0.1:8080`
(Docker Desktop portu Windows'un `127.0.0.1`'ine de yayımlar). `HUB_ROBOT_URL` ortam değişkeni de olur.

| Hub'da | Robot API'sinde |
|---|---|
| senaryo adımı (`robot: above green 0.25`) | `POST /api/jobs` → `GET /api/jobs/<id>` yoklaması (≤ 4 Hz) |
| senaryoyu iptal et | `POST /api/jobs/<id>/cancel` (kolu durdurur, **kilitlemez**) |
| **ACİL DUR** | `POST /api/stop` (kol durana dek; robot hızdan **doğrular**, ≤ 5 s); 3 deneme |
| "Kilidi aç" | `POST /api/reset`; robot reddederse (kol hâlâ hareketli/bilinmiyor) Hub da kilitli kalır |
| canlı durum | `GET /api/state` (0,5 sn yoklama, önbellekten sunulur) |

`POST /api/command` **kullanılmaz** (sözleşmede kullanımdan kaldırıldı; iş API'si onun üst kümesi).

* **ACİL DUR sonucu arayüzde görünür:** "Robotun durduğu doğrulandı (0.33 sn)" ya da kırmızı yanıp sönen
  **"ROBOTUN DURDUĞU DOĞRULANAMADI"** (robot 5 s içinde durmadı, hız okunamadı ya da robota ulaşılamadı;
  kilit yine de Hub'da ve robotta geçerli). Doğrulanamazsa **fiziksel acil durdurmayı kullan**.
* **Robot tarafındaki kilit Hub'a yansır:** başka bir istemci/düğme robotu durdurduysa Hub da "ACİL DUR
  aktif (robot tarafında)" der ve senaryo başlatmaz; kilidi açmak yine `/api/reset` ister.
* `mode: real` ise arayüz sürekli uyarır: bu **yazılım** acil durdur, donanım acil durdurmanın yerine geçmez.
* Sürüm denetimi: `api_version` 1 değilse Hub komut göndermez ("robot API sürümü ... uyumsuz").
* Robot API'si yalnızca `127.0.0.1` kabul eder (`Host` başka bir şeyse `403`) ve kimlik doğrulaması yoktur:
  **robot adresini ağa açma**; ağa açılan tek şey Hub'dır (PIN'li).
* Robot kapalıyken Hub açılır, "Robot bağlı değil" der ve yeniden dener.

Seçenekler: `--robot-url`, `--port`, `--host`, `--access-mode open|pin|locked`, `--pin`, `--config-dir`,
`--fake-speed 0.2` (sahte robotu 5 kat hızlandırır), `--allow-host`, `--check-config`.

## Windows'ta kurulum (ControlHub.exe)

Tek dosya, kurulum yok, Python gerekmez, ROS yok; web arayüzü ve varsayılan senaryolar içine gömülü.
`.exe`'yi GitHub Actions üretir (workflow: **Hub .exe**).

### 1. İndir
GitHub → repo → **Actions** → **Hub .exe** → son başarılı çalıştırma → sayfanın altındaki **Artifacts** →
`ControlHub-windows` (zip) → indir, zip'ten çıkar. Bütünlük için (isteğe bağlı), `PowerShell'de`:
```powershell
Get-FileHash .\ControlHub.exe -Algorithm SHA256     # ControlHub.exe.sha256 dosyasındakiyle aynı olmalı
```

### 2. İlk açılış: SmartScreen / Defender
`.exe` **imzasız** (kod imzalama sertifikası yok). İlk çift tıklamada "Windows kişisel bilgisayarınızı korudu"
çıkabilir: **Daha fazla bilgi → Yine de çalıştır**. PyInstaller ile üretilen imzasız programları bazı
antivirüsler yanlışlıkla işaretleyebilir (yanlış alarm); işaretlenirse dosyayı SHA256 ile karşılaştır, bu
repodaki workflow'un ürettiği olduğundan emin ol, sonra izin ver.

### 3. Çalıştır
**Çift tıkla:** bir pencere açılır (siyah konsol penceresi kendiliğinden gizlenir).
* **Robot adresi:** varsayılan `http://127.0.0.1:8080` (Docker'daki robotun yayımlanan portu). Robot yokken
  arayüzü denemek için "Sahte robot"u işaretle.
* **PIN:** boş bırakırsan her açılışta rastgele üretilir ve pencerede büyük yazar. Kendi PIN'ini yazabilirsin
  (4-12 rakam); "PIN'i hatırla" işaretliyse ayar dosyasına **düz metin** yazılır.
* **Başlat** → pencerede *Bilgisayar* ve *Telefon* adresi, PIN, QR kod ve robot durumu görünür.
* **Senaryo/arayüz dosyalarını aç…**: varsayılan `scenarios.yaml` / `ui.config.json` dosyalarını
  `%APPDATA%\ControlHub\config\` altına kopyalar (varolanı ezmez) ve klasörü açar. Bu klasördeki dosyalar,
  `.exe`'nin içindekilerin yerine geçer; düzenleyip Hub'ı yeniden başlat.
* Ayarlar `%APPDATA%\ControlHub\settings.json`, günlük `%APPDATA%\ControlHub\hub.log`.

**PowerShell'den** (aynı program; bayrak verince konsolda çalışır):
```powershell
.\ControlHub.exe --robot-url http://127.0.0.1:8080 --pin 123456   # gerçek robot
.\ControlHub.exe --fake --pin 123456                               # sahte robot (robot yokken dene)
.\ControlHub.exe --fake --check-config                             # yapılandırmayı doğrula ve çık
.\ControlHub.exe --help
```

### 4. Gerçek robotla birlikte
```bash
# Terminal 1 — WSL'de (robot yığını; "Hazır." yazısını bekle)
cd ~/ros2-robot-arm && docker compose up --build demo
```
```powershell
# Terminal 2 — PowerShell'de (Hub .exe; Docker Desktop 8080'i Windows'un 127.0.0.1'ine yayımlar)
.\ControlHub.exe --robot-url http://127.0.0.1:8080 --pin 123456
```
(Pencereyi tercih ediyorsan `.exe`'yi çift tıkla, "Robot adresi" zaten `http://127.0.0.1:8080`.)

### 5. Telefondan bağlanma ve Windows güvenlik duvarı
Telefon ve bilgisayar **aynı Wi-Fi**'da olmalı. Telefon tarayıcısında pencerede yazan *Telefon* adresini aç
(ya da QR'ı okut), PIN'i gir. Bağlanmazsa büyük olasılıkla Windows güvenlik duvarı gelen bağlantıyı engelliyordur;
izin vermenin iki yolu var. **Hiçbiri kendiliğinden yapılmaz, onayını ister.**

**a) Pencereden (tek tık):** *Telefon erişimine izin ver…* → ne ekleneceğini (ad, TCP, port, yalnızca Özel ağ,
tam komut) gösteren onay kutusu → *Evet* → Windows yönetici izni (UAC) → kural eklenir; pencere "Kural: VAR"
gösterir. Kaldırmak için *Kuralı kaldır…* (o da onay + UAC ister).

**b) PowerShell ile:** *Yönetici olarak* açılmış PowerShell'de (Başlat → "PowerShell" → sağ tık → Yönetici olarak çalıştır):
```powershell
# PowerShell'de (YÖNETİCİ): izin ver — yalnızca TCP 8765, yalnızca "Özel" ağ profili
New-NetFirewallRule -DisplayName "Robot Kontrol Merkezi (Hub)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
# kontrol et
Get-NetFirewallRule -DisplayName "Robot Kontrol Merkezi (Hub)" | Format-List DisplayName,Enabled,Profile,Action
# kaldır
Remove-NetFirewallRule -DisplayName "Robot Kontrol Merkezi (Hub)"
```
Farklı port kullanıyorsan `.\ControlHub.exe --firewall-help --port <port>` komutları o port için **yazdırır**
(hiçbir şey eklemez).

Notlar: kural yalnızca **Özel** ağda geçerlidir; Wi-Fi'ın Windows'ta "Genel" görünüyorsa Ayarlar → Ağ ve
İnternet → Wi-Fi → ağın özellikleri → "Özel ağ"ı seç (genel/halka açık ağlarda Hub'ı açık bırakma). Hub'ı
internete açma, router'da port yönlendirme yapma. `ipconfig` (PowerShell'de) ile Wi-Fi IPv4 adresini
görebilirsin; pencere aynı adresi zaten gösterir.

### Sorun giderme
| Belirti | Neden / çözüm |
|---|---|
| "port dolu" hatası | Hub zaten açık (başka pencere) ya da başka program: başka port seç |
| Pencerede "Robot: bağlı değil" | Robot yığını ("docker compose up --build demo") açık değil ya da adres yanlış; Hub açık kalır ve yeniden dener |
| Telefon bağlanmıyor, bilgisayar bağlanıyor | Güvenlik duvarı (yukarıdaki 5) ya da farklı Wi-Fi/misafir ağı (cihazlar birbirini görmeyebilir) |
| Pencere hiç açılmıyor | `%APPDATA%\ControlHub\hub.log`'a bak; PowerShell'den `.\ControlHub.exe --fake` ile dene |

### Kendin derlemek
```powershell
# PowerShell'de (apps\control_hub içinde)
pip install -r requirements.txt pyinstaller
pyinstaller hub.spec --noconfirm        # çıktı: dist\ControlHub.exe
```
**Neden PyInstaller `--onefile`?** Windows'ta en yaygın ve Actions'ta sorunsuz çalışan seçenek (Python +
bağımlılıklar tek dosyada); Nuitka C derleyicisi ister, derleme çok daha uzun ve kırılgan; `--onedir` birden
çok dosya demek (istenen tek dosya değil). Bedeli: ilk açılış birkaç saniye (içeriği geçici klasöre açar) ve
antivirüs yanlış alarmı riski (yukarıda). Konsol uygulaması olarak derlenir (`console=True`) ki PowerShell'de
çıktı görünsün; çift tıkta konsolu kendisi gizler (`desktop/winconsole.py`: yalnızca konsolu kendisi
açtıysa; PowerShell'den açıldıysa terminale dokunmaz).

## ESP32 donanım köprüsü (USB-seri ↔ TCP)

Hub, ESP32'ye bağlı **COM portunu** açıp TCP üzerinden tek bir istemciye (B2'de Docker'daki ROS düğümü) sunabilir.
Köprü baytları yorumlamadan taşır; CRC/seq/watchdog uçlar arasındadır. Mimari, mesaj şeması, durum makinesi, watchdog ve
acil dur: **`docs/HARDWARE.md`**. Şimdilik donanım yok: `src/robot_hw_bridge` içindeki **simüle ESP32** ile denenir.

```powershell
# PowerShell'de (Hub .exe; gerçek ESP32 takılıyken)
.\ControlHub.exe --list-serial-ports                                   # COM3, COM5 ...
.\ControlHub.exe --fake --serial-bridge COM5 --bridge-listen 127.0.0.1:8770   # Hub + köprü
.\ControlHub.exe --bridge-only --serial-bridge COM5                    # yalnızca köprü
```
Pencerede "ESP32 donanım köprüsü" bölümünden de açılır (Başlat'a basınca). `--bridge-listen 0.0.0.0:8770` için
`--bridge-token` **zorunludur** (motor komutlarını ağa açar; token şifrelenmeden gider, yalnızca güvenilir ağda).
Docker'dan bağlanma adresi: `host.docker.internal:8770`.

Donanımsız deneme (hepsi WSL'de, 3 terminal; `robot_hw_bridge` paketi `src/robot_hw_bridge`'te):
```bash
# Terminal 1 — WSL'de: simüle ESP32 (TCP)
cd ~/ros2-robot-arm/src/robot_hw_bridge && python3 -m robot_hw_bridge sim --listen 127.0.0.1:8771
# Terminal 2 — WSL'de: Hub + köprü (seri port yerine simülatörün socket:// adresi)
cd ~/ros2-robot-arm/apps/control_hub && python -m control_hub --fake --pin 123456 \
    --serial-bridge socket://127.0.0.1:8771 --bridge-listen 127.0.0.1:8770
# Terminal 3 — WSL'de: örnek istemci (home → enable → move → ACİL DUR)
cd ~/ros2-robot-arm/src/robot_hw_bridge && python3 -m robot_hw_bridge demo --connect 127.0.0.1:8770
```

## Yapay zekâ ajanı (sohbet paneli)

Hub'a doğal dille robot komutu veren bir sohbet paneli eklenir. `robot_agent` **kütüphane olarak Hub
sürecinde** çalışır ve Robot API'ye HTTP ile bağlanır (**Robot API'ye LLM ucu eklenmez**). Sağlayıcılar:
Claude (Anthropic) ve OpenAI-uyumlu uç; model adı ve OpenAI uç adresi **yapılandırma değeridir** (koda
gömülü değil). `offline` sağlayıcı anahtarsız yerleşik kural çözücüdür (LLM değil; denemek için).

```bash
# WSL'de — ilk deneme: sahte robot, KURU ÇALIŞTIRMA (hareket robota gitmez), anahtar gerekmez
cd ~/ros2-robot-arm/apps/control_hub
pip install -r requirements.txt
python -m control_hub --fake --pin 123456 --agent-provider offline --agent-dry-run
# tarayıcı: http://localhost:8765  → "Yapay zekâ ajanı" kartı → "yeşil nesnenin üstüne git"
```
Gerçek LLM için (anahtar **argümana/ortam değişkenine konmaz**, gizli sorulur ve yalnızca bellekte tutulur):
```bash
# WSL'de
python -m control_hub --fake --agent-provider openai --agent-model <MODEL> \
       --agent-base-url https://<UÇ-ADRESİ>/v1 --agent-key-prompt openai --agent-dry-run
```

**Güvenlik modeli (dıştan içe):**

| Konu | Davranış |
|---|---|
| ACİL DUR | Ajandan **bağımsız yoldan** çalışır (`/api/estop`: oturumsuz, ajan kilidi/kuyruğu yok). Ajan buna engel olamaz; yalnızca dinleyici olarak turunu keser ve bekleyen onayları reddeder. Ajanın `emergency_stop` aracı onaysızdır, günlük limit/hareket tavanı ona uygulanmaz ve Hub kilidini de koyar. Kilidi açma aracı **yoktur**. |
| Onay kuyruğu | Robotu ya da kamerayı hareket ettiren her araç Hub arayüzünde **Onayla/Reddet** ister. **Zaman aşımında (varsayılan 60 sn) REDDEDİLİR.** ACİL DUR, iptal ya da kapanışta bekleyen onaylar hemen reddedilir; ACİL DUR kilitliyken onay verilemez (`423`). |
| Sınırlar | Tur başına hareket tavanı (varsayılan 3) ve günlük LLM isteği sınırı (varsayılan 200; bellekte, yeniden başlatınca sıfırlanır: maliyet freni, güvenlik sınırı değil). Her mesaj bağımsız bir turdur (konuşma geçmişi LLM'e taşınmaz). |
| API anahtarı | Varsayılan **yalnızca bellekte**. "Beni hatırla" işaretlenirse işletim sisteminin anahtar deposuna (`keyring`; Windows'ta Credential Manager); depo yoksa anahtar yine bellekte kalır ve uyarı verilir. `settings.json`'a, olay günlüğüne, Hub günlüğüne ya da HTTP yanıtına **asla** yazılmaz; sunucu yalnızca `{ayarlı, hatırlanıyor}` bayraklarını döner. |
| Anahtar/ayar girişi | **Yalnızca masaüstü penceresinden ya da `127.0.0.1`'den** (bağlantı loopback + `Host` loopback + vekil başlığı yok). Telefon (LAN) anahtarı giremez, silemez, göremez; sağlayıcı/model/**uç adresi** de değiştiremez (uç adresi anahtarın gideceği yerdir). Docker/NAT arkasından gelen bağlantı yerel sayılmaz (kapalı kalır). |
| CSRF | `/api/agent/*` POST'ları PIN oturumuna ek olarak oturuma bağlı `X-CSRF-Token` ister (`GET /api/agent/status` verir); `Origin`/`Content-Type` denetimleri aynen geçerli. |
| Robot tarafı | `robot_agent`'ın beyaz liste araçları, şema denetimi, güvenli kutu, kuru çalıştırma ve anahtar sızıntı koruması aynen geçerli. Eklenti araçları (`run_plugin`) Hub'da **kapalı**. |

`robot_agent` ve `robot_core` depo çıkışında `src/` altından otomatik bulunur; kurulu paket/`PYTHONPATH` varsa
o kullanılır. Bulunamazsa panel kapalı kalır ve nedeni gösterilir (Hub çalışmaya devam eder). Docker `hub-fake`
imajında `robot_agent` yoktur: panel kapalıdır. **`ControlHub.exe` ajanı içerir** (`robot_agent`, `robot_core` alt kümesi, `keyring` + Windows Credential Manager
arka ucu `hub.spec` ile gömülü; `robot_plugins` yok). Öz-sınama: `ControlHub.exe --agent-selftest` (sahte yerel LLM,
kuru çalıştırma, canary, anahtar deposu; gerçek depo çalışmazsa `KEYRING-REAL: SKIPPED <neden>` yazar).

Kaynaklar (resmî dokümandan teyit, 2026-11-04): OpenAI Chat Completions —
developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create ve
/api/docs/guides/function-calling (uç `/chat/completions`, `Authorization: Bearer`, `max_completion_tokens`,
`tools[].function`, `tool_calls[].function.arguments` JSON dizgesi, `tool` rolü + `tool_call_id`). Anthropic
Messages API — platform.claude.com/docs/en/api/overview ve /api/messages/create: güncel genel bakışta
`Authorization: Bearer` birincil, `x-api-key` "eski yedek, hâlâ destekleniyor", `anthropic-version`
zorunlu (örnek değer `2026-10-21`; eski sayfalarda `2023-06-01`). `robot_agent.llm.AnthropicLLM` (A1)
`x-api-key` + `2023-06-01` kullanır; bu PR'da değiştirilmedi.

## Yapı

```
control_hub/
  hub_core/   GUI'siz, ROS'suz çekirdek (tüm mantık burada, test edilir)
    backend.py        RobotBackend protokolü (execute / emergency_stop / snapshot ...)
    fake_backend.py   donanımsız simülasyon (--fake)
    robot_api_backend.py  robotun HTTP API v1'i (iş/stop/reset/state), yalnızca standart kütüphane
    commands.py       robot komut beyaz listesi (home | objects | above <renk> [m])
    scenarios.py      scenarios.yaml + ui.config.json okuma/doğrulama
    runner.py         ScenarioRunner: tek anda tek senaryo, iptal edilebilir
    hub.py            Hub: hepsini birleştirir + ACİL DUR kilidi
    access.py         open|pin|locked, PIN, oturum, deneme sınırı
    events.py         olay günlüğü
  server/     FastAPI + WebSocket (çekirdeği HTTP'ye açar)
  web/        düz HTML/CSS/JS, derleme yok, mobil öncelikli, PWA manifest
  desktop/    Windows .exe'nin pencere + servis + güvenlik duvarı mantığı (Tk yalnızca gui.py'de)
  hw/         ESP32 köprüsü: COM↔TCP (bayt şeffaf), yeniden bağlanma, token
  agent/      LLM ajanı: AgentService (robot_agent'ı sarar), anahtar deposu, onay kuyruğu, sahte Robot API portu
  paths.py    gömülü/kullanıcı yolları (PyInstaller _MEIPASS dahil)
config/       scenarios.yaml (butonlar), ui.config.json (yerleşim + tema)
hub.spec, exe_entry.py, assets/   PyInstaller tek dosya derlemesi
tools/        geliştirme araçları (üretimde kullanılmaz): robot_stub.py, e2e_check.py, smoke_check.py
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
| Ajan | `/api/agent/*`: PIN oturumu + CSRF belirteci; anahtar/ayar yalnızca yerelden; ayrıntı "Yapay zekâ ajanı" bölümünde. |
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

### Jog paneli (J3)

Robot API'sinin `POST /api/jog` + `GET/PUT /api/frames` uçlarını (J2) Hub **geçirir**; kurallar robottadır
(kiralama, TTL ≤ 500 ms, 409/423). Hub `frames.yaml`'a **yazmaz** (`PUT` robota gider, dosyayı robot yazar).

| Uç nokta | Yetki | Anlam |
|---|---|---|
| `POST /api/jog` | kontrol | komut = heartbeat (`ttl_ms` 50-500, varsayılan 250; `0` bırakır). `423` Hub'da robota sormadan; `twist` (basılı-tut) loopback dışı istemciye `403`; ağ hatası `502` |
| `GET /api/jog` | görüntüleme | robot jog durumu + `hold_allowed`/`hold_reason` (Hub'ın eklediği: neden kapalı) |
| `GET /api/frames`, `PUT /api/frames` | görüntüleme / kontrol | çerçeve listesi; yazma robota gider (jog etkinken `409`) |

Panel: **artımlı varsayılan** (0.1 / 1 / 10 mm ya da °). Basılı-tut yalnızca `hold_allowed` ise açılır ve
kullanıcı kutuyu işaretlerse çalışır; kapalıysa nedeni panelde yazar (gerçek mod, `jog_hold` kapalı, telefon,
Docker NAT'ta robotun reddi: `403`). `409` = başka istemci/iş, `423` = ACİL DUR kilidi, `502` = robota ulaşılamıyor.
**Robot görünümü (L9c):** `GET /api/model` (robotun `view_model.json`'u, salt-okunur geçiş) + durumdaki `joints` ile
bağımlılıksız Canvas 2D, ortografik, sürükleyerek döner; link = çubuk/kapsül, turuncu kesikli kutu = modeldeki etkin
güvenli kutu, beyaz nokta = çizilen zincirin ucu, yeşil halka = robotun bildirdiği TCP, eksenler = jog'da seçili çerçeve.
**Bayatlık:** WS'ten gelen son mesaj ya da robot verisi 1 s'den eskiyse görünüm griye döner ve kırmızı "ACİL DUR" bandı
çıkar (Hub WS'i durum değişmese de en geç 0,4 s'de bir `tick` yollar; WS yokken HTTP yoklaması görünümü "taze" yapmaz).
`mode: real`'de çizim "KESTİRİM" etiketlidir (API eksen bazında encoder bilgisi vermez, hepsi kestirim sayılır);
`--fake`'de "SAHTE ROBOT" (duruş kinematik değil). Model alınamazsa ya da eklem adları uyuşmazsa "Görünüm kullanılamıyor:
<neden>" kartı çıkar. Görünüm komut yolu içermez ve ölçüm/güvenlik işlevi değildir; ACİL DUR Hub'ın kendi düğmesidir.

**Düzlem öğretme sihirbazı (L3):** "Düzlemler" kartı. Orijin, +X yönü ve XY düzlemi noktaları robot jog ile
götürülüp "Şu anki TCP'yi kaydet" ile alınır (sihirbaz robotu hareket ettirmez); "Düzlemi kaydet" taze listeyi
okuyup yenisini ekleyerek **tüm listeyi** `PUT /api/frames` ile yollar (dosyayı robot yazar). Geçersiz nokta
(20 mm'den yakın, aynı doğru) robotun hata metniyle gösterilir; 409/423/403 aynen. Kayıtlı düzlemler listelenir,
"Jog'da seç" ile jog çerçevesi olur, çift tıkla (onaylı) silinir.

Tuş bırakılınca, sekme gizlenince, pencere odağı kaybolunca ve sayfa kapanınca kiralama bırakılır; bunlar
yalnızca kibarlıktır, **asıl dead-man robot tarafındaki TTL'dir**. Yazılım dead-man donanım acil durdurmanın
yerine geçmez. `--fake` robotunda jog kartezyen toplamadır (kinematik/güvenli kutu yok).

## Testler

```bash
# WSL'de
cd ~/ros2-robot-arm/apps/control_hub
pip install -r requirements-dev.txt
ruff check . && python -m pytest
```
`tests/test_robot_api_contract.py`, Hub'ı robotun **gerçek HTTP sunucu koduna** (`robot_core.webview` /
`CommandRunner` / `JobManager` / `Supervisor`, kol simüle) karşı sınar (`tools/robot_stub.py`; ROS gerekmez,
yalnızca `numpy` ve repodaki `src/robot_core`; yoksa dosya atlanır). Aynı sunucuyu elle denemek için:
`python -m tools.robot_stub --port 8080 --speed 0.3` (WSL'de), sonra Hub'ı `--robot-url http://127.0.0.1:8080` ile aç.

**Gerçek yığına karşı uçtan uca** (robot HAREKET EDER: home, demo turu, iptal, ACİL DUR, reset):
```bash
# Terminal 1 — WSL'de
cd ~/ros2-robot-arm && docker compose up --build demo
```
```bash
# Terminal 2 — WSL'de ("Hazır." göründükten sonra)
cd ~/ros2-robot-arm/apps/control_hub && python -m tools.e2e_check --robot-url http://127.0.0.1:8080
```
Ayrıca CI'daki `hub` işi Docker imajını derleyip `/health`'i ve PIN korumasını dener.

## Bilinen sınırlar

* `--fake` robotunda eklem değerleri yalnızca görsel; gerçek kinematik yok (jog dahil).
* Canlı durum 5 Hz yoklanır (200 ms'den kısa olaylar arayüzde görünmeyebilir).
* Hub'ın senaryoları adım adım iş gönderir; robotun kendi `demo_scenario` komutundaki **TF ile uç konum
  doğrulaması** (≤ 1,5 cm) Hub senaryolarında yoktur. Doğrulama gerekirse `demo_scenario` adım türü sonra.
* Robot API'sinin acil durdu yalnızca `web_view`'in başlattığı hareketleri durdurur (INTERFACES.md "Kapsam"):
  ayrıca çalışan bir `cli_moveit` kilitlenmez. `mode: real`'de donanım acil durdurmanın yerine geçmez.
* `hub-fake` Docker servisi yalnızca sahte robotla çalışır: robot API'si `127.0.0.1` dışındaki `Host`'u
  reddettiği için Hub konteyneri robot konteynerine ağdan bağlanamaz (bkz. PR gövdesi, "Robot_3'ten istek").
