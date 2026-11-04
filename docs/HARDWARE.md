# Donanım hattı: ESP32 ↔ Hub ↔ ROS (B1 tasarımı)

**Durum: B1 (bu belge + `src/robot_hw_bridge` + Hub köprüsü). Donanım YOK; her şey simülatörle denendi.**
B2 (ROS düğümü) ve B3 (ESP32 firmware) sonraki PR'lar. Bu belge ikisinin **uyacağı sözleşmedir**; çalıştırılabilir
belirtimi `robot_hw_bridge.sim.Esp32Sim`'dir (firmware ile simülatör aynı davranmalı; fark = hata).

> **Güvenlik uyarısı (ilk okunacak şey).** Burada anlatılan acil dur ve watchdog mimarisi, iyi niyetli bir
> mühendislik tasarımıdır; **sertifikalı bir güvenlik fonksiyonu değildir** (ISO 13849 / IEC 60204-1 / IEC 61800-5-2
> kapsamında doğrulanmadı). Robotun yakınında insan varsa ya da kuvvetli motor/yüksek güç kullanılıyorsa sertifikalı
> güvenlik rölesi ve acil dur bileşenleri kullanılmalıdır. Yazılım acil dur (komut, watchdog) **hiçbir zaman** donanım
> acil durun yerine geçmez. Bu PR'daki kodun **hiçbiri gerçek ESP32/COM/USB üzerinde çalıştırılmadı.**

## 1. Mimari

```
  Docker (WSL2)                     Windows                                   Robot gövdesi
┌────────────────┐  TCP     ┌──────────────────────────┐   USB-seri    ┌──────────────────────────────┐
│ ROS 2 düğümü   │◄────────►│ Hub .exe: COM↔TCP köprüsü │◄────────────►│ ESP32                         │
│ (B2) HwLink    │ host.    │ (bayt şeffaf, tek istemci)│  COM5, 921600 │  step üretimi, encoder okuma, │
│  kalp atışı    │ docker.  └──────────────────────────┘               │  durum makinesi, watchdog     │
│  komut/ACK     │ internal:8770                                       └───────┬───────────────┬──────┘
└────────────────┘                                                    STEP/DIR │           GPIO │ (okuma)
                                                                    ┌───────────▼───┐   ┌───────▼────────┐
  Acil dur butonu (NC) ───────────────── seri bağlı ──────────────► │ Step sürücüler │   │ NC döngü girişi │
        │                                                           │ EN pini ◄──────┼───┘ (yalnız bilgi)  │
        └─► motor güç hattı (kontaktör / güvenlik rölesi, §6) ──────► └───────────────┘
```

* **Uçtan uca katman ROS düğümü ↔ ESP32'dir:** CRC, seq, watchdog, ACK onların arasındadır. Köprü baytları **yorumlamadan**
  taşır; bu yüzden aynı çerçeveler Wi-Fi'da da (sonra) aynen çalışır.
* Hub (Windows) köprüyü açar çünkü COM portunu Docker konteyneri göremez; konteyner TCP ile `host.docker.internal:8770`'e bağlanır.
* ROS'ta eksen birimi (radyan) ↔ motor adımı dönüşümü B2'nin işidir: `docs/ROBOT_PROFILE.md`'deki `motors` bölümü
  (`direction`, `zero_offset`, `gear_ratio`) buna ayrılmıştır. Bu hat yalnızca **adım** konuşur.

## 2. Taşıma

| Konu | Karar | Neden |
|---|---|---|
| Ana bağlantı | **USB-seri** (CDC ya da CH340/CP210x), 921600 baud, 8N1 | Kablo kopması/çekme açıkça algılanır (COM kaybolur), kimlik doğrulama sorunu yok, gecikme düşük ve öngörülebilir |
| Sonra | Wi-Fi (TCP), **aynı çerçeve şeması** + HMAC + sıkı seq | Şema değişmez; yalnızca `AUTH` bayrağı ve sondaki HMAC açılır |
| Çerçeveleme | **COBS** + `0x00` ayracı | Çerçeve içinde `0x00` olmaz; bayt kaybı/bozulması sonrası bir sonraki `0x00`'da **kendiliğinden yeniden senkron** |
| Bütünlük | **CRC-16/CCITT-FALSE** (poly 0x1021, init 0xFFFF; `"123456789"` → `0x29B1`) | Kısa çerçeveler için yeterli; firmware'de tablo ile ~20 satır |
| Gövde | Sabit uzunluklu **ikili** yükler (little-endian) | ESP32'de JSON ayrıştırıcı yok, deterministik süre, küçük |

## 3. Mesaj şeması v1

Çerçeve (COBS'tan önce): `sürüm u8 | bayraklar u8 | tür u8 | uzunluk u8 | seq u16 | ack u16 | yük[uzunluk ≤ 200] | crc16 u16 | [hmac 8 B]`.
`crc16` ve `hmac` başlık+yük üzerindedir. Bilinmeyen sürüm/bayrak, bozuk CRC/COBS/uzunluk, yanlış HMAC → **çerçeve atılır** (ayrı sayaçlar),
çözücü hiçbir girdide exception fırlatmaz.

* **Bayraklar:** `0x01 AUTH` (sonda 8 B HMAC-SHA256 kesiti; anahtar yoksa/uyuşmuyorsa reddedilir), `0x02 RETRY` (aynı `cmd_id`'nin yeniden gönderimi).
* **seq:** her yön kendi `u16` sayacını **her çerçevede** (kalp atışı dahil) artırır; alıcı boşluğu **kayıp çerçeve** olarak sayar, geri/aynı seq'i
  yineleme sayar ve işlemez; HMAC'li taşımada geri seq = **tekrar saldırısı** (reddedilir). **ack:** göndericinin gördüğü son karşı seq'i taşır
  (kayıp kestirimi için; komut güvenilirliği `cmd_id` ile).
* **Komut güvenilirliği:** her komut yükü `cmd_id u16` ile başlar. Host ACK gelmezse (150 ms) aynı `cmd_id`'yi `RETRY` ile yeniden yollar (en çok 3); MCU
  son 32 `cmd_id`'nin yanıtını saklar: **yineleme yeniden işlenmez, aynı ACK yeniden verilir** (çift hareket olmaz).

| Tür | Yön | Yük |
|---|---|---|
| `0x01 HEARTBEAT` | H→M | boş (her 50 ms) |
| `0x02 HELLO_REQ` | H→M | `proto u8, nonce u32` |
| `0x10 HOME` / `0x14 STOP` | H→M | `cmd_id, eksen_maskesi u8` |
| `0x11 ENABLE` / `0x12 DISABLE` / `0x15 ESTOP` / `0x16 CLEAR` | H→M | `cmd_id` |
| `0x13 MOVE` | H→M | `cmd_id, eksen u8, hedef i32, vmax u32 (adım/sn), ivme u32 (adım/sn²)` |
| `0x17 CONFIG` | H→M | `cmd_id, anahtar u8, eksen u8, değer i32` (`WATCHDOG_MS` 200-500, `SOFT_MIN/MAX`, `FOLLOW_ERR_STEPS/MS`) |
| `0x81 HELLO` | M→H | `proto, fw_major, fw_minor, eksen_sayısı, boot_sayacı u16, cihaz_id[8]`, eksen başına `encoder_kipi u8, bayrak u8 (home/limit anahtarı)` |
| `0x82 STATUS` | M→H | `durum u8, acil_dur_nedeni u8, bayraklar u16, çalışma_süresi_ms u32, watchdog_ms u16, n u8`; eksen başına `konum i32, encoder i32, bayrak u8` (her 50 ms ve her ACK'ten sonra) |
| `0x83 ACK` | M→H | `cmd_id u16, sonuç u8, ayrıntı u8` |
| `0x84 FAULT` | M→H | `kod u8, eksen u8, ayrıntı i32, çalışma_süresi_ms u32` |

**Sonuç kodları:** `OK, BAD_STATE, BAD_ARG, LIMIT (yazılım limiti dışı), NO_REFERENCE (referanslanamayan eksen), ESTOP_ACTIVE, HW_LOOP_OPEN, BUSY, UNKNOWN_CMD`; `LINK_DOWN` yalnızca host tarafında üretilir.
**Acil dur nedenleri:** `HW_BUTTON, HOST_CMD, WATCHDOG, FOLLOWING_ERROR, LIMIT_VIOLATION, INTERNAL, BOOT`.
**STATUS bayrakları:** `HW_LOOP_OPEN` (NC döngü açık), `WATCHDOG_TRIPPED`, `OPEN_LOOP_ANY` (en az bir eksende encoder yok), `HOST_LINK_OK`.

## 4. Durum makinesi

```
              HOME                 hepsi referanslı            ENABLE
   UNHOMED ───────────► HOMING ───────────────────► HOMED ───────────────► ENABLED
      ▲                    │                          ▲  ◄─────DISABLE────────┘ │
      │ CLEAR              │  (herhangi bir durumdan) │                          │
      │ (açık döngü        ▼                          │                          │
      │  eksenler)    ┌──────────┐  ◄──── HW_BUTTON / HOST_CMD / WATCHDOG / FOLLOWING_ERROR / LIMIT_VIOLATION
      └───────────────┤ ESTOPPED │  ─── CLEAR (yalnızca NC döngü KAPALIYKEN) ──► UNHOMED (ya da HOMED: yalnız mutlak encoder)
                      └──────────┘
```

| Durum | Sürücüler | İzinli komutlar | Notlar |
|---|---|---|---|
| `UNHOMED` | kapalı | `HOME`, `CONFIG`, `STOP`, `ESTOP` | açılış durumu (NC döngü kapalıysa); `MOVE/ENABLE` reddedilir |
| `HOMING` | yalnız homing eksenleri, düşük hız | `STOP`, `ESTOP` | bittiğinde tüm eksenler referanslıysa `HOMED`, değilse `UNHOMED` |
| `HOMED` | kapalı | `ENABLE`, `HOME`, `CONFIG` | konum güvenilir |
| `ENABLED` | açık | `MOVE`, `STOP`, `DISABLE` (hareket yokken), `ESTOP` | yalnızca burada hareket edilir; yazılım limitleri dışı hedef `LIMIT` |
| `ESTOPPED` | **kesik, kilitli** | `CLEAR`, `ESTOP` | her şey `ESTOP_ACTIVE` ile reddedilir |

Kurallar:
1. **Açılışta NC döngü açıksa** MCU `ESTOPPED/BOOT` başlar (butona basılıyken enerji vermek robotu hareketlendirmez).
2. **`CLEAR` yalnızca NC döngü kapalıyken** işe yarar (`HW_LOOP_OPEN` döner). Butonu bırakmak kilidi **açmaz**; `CLEAR` ayrı, bilinçli bir komuttur.
3. **ACİL DUR sonrası** encoder'sız/artımlı encoder'lı eksenler `homed` bilgisini kaybeder (adım kaybı bilinemez) → `UNHOMED` → **yeniden homing şart**. Yalnızca mutlak encoder'lı eksenler referansını korur.
4. `ESTOP` komutu **her durumda** kabul edilir ve **ACK beklenmeden art arda 3 kez** gönderilir (bir çerçeve kaybolsa da ulaşır).
5. `DISABLE` hareket varken `BUSY` verir (sürücüyü hareket ortasında kesmek step kaybettirir; acil durumda `ESTOP` kullanılır).
6. `HOME`, sürücüleri **yalnızca homing süresince** ve yalnızca ilgili eksenler için açar (NC döngü kapalıysa).

## 5. İki yönlü watchdog (200-500 ms)

| Parametre | Varsayılan | Aralık | Yer |
|---|---|---|---|
| Host kalp atışı | 50 ms | – | `HwLink` |
| MCU STATUS | 50 ms | – | firmware |
| **MCU watchdog** (host sustu) | **300 ms** | **200-500** (`CONFIG WATCHDOG_MS`) | firmware |
| **Host hat zaman aşımı** (MCU sustu) | **300 ms** | 200-500 | `LinkMonitor` |

* **MCU tarafı:** geçerli **herhangi** bir host çerçevesi (kalp atışı, komut) sayacı sıfırlar. İlk host çerçevesinden sonra devreye girer. Süre dolarsa ve
  durum `ENABLED` ya da `HOMING` ise: hareket **hemen** kesilir, sürücüler kapanır, `ESTOPPED/WATCHDOG` (kilitli) + `FAULT` olayı. Sürücüler zaten kapalıysa kilitlenmez
  (yalnızca `HOST_LINK_OK` düşer). Kurtarma: hat geri gelince `CLEAR` + yeniden homing.
* **Host tarafı:** 300 ms STATUS gelmezse hat **KOPTU**: `on_link_lost` çağrılır; üst katman (B2) bunu **yazılım ACİL DUR** sayar (Hub'ın `estop`'u, ROS'ta hareketi durdurma).
  Kopukken komutlar `LINK_DOWN` döner (başarı sayılmaz). Kalp atışı gönderilmeye devam eder (hat geri gelince toparlanabilsin).
* **Neden 200-500?** Alt sınır: Windows/USB zamanlama jitter'ı ve bir iki kayıp çerçeve (50 ms'lik akışta 300 ms = 5 çerçeve payı) yanlış alarm üretmesin.
  Üst sınır: step'li eksenin ivmesiyle durma mesafesi, bir "kontrolsüz" hareket süresi ~0.5 s'yi geçmesin.
* **Köprü kalp atışı ÜRETMEZ** ve istemci koparsa ESP32'ye komut enjekte etmez: kalp atışı kesildiği için MCU watchdog'u zaten durdurur. (Köprünün kendiliğinden
  kalp atışı yollaması, ROS düğümü ölse bile robotu "canlı" gösterirdi: tehlikeli.)
* COM kopması (USB çekildi): köprü TCP istemcisini **hemen** kapatır (host "hat koptu"yu beklemeden görür), portu yeniden açmayı dener; MCU tarafında kalp atışı kesildiği için
  watchdog devreye girer.

## 6. Donanım acil dur (NC butonla) mimarisi

Hedef: **yazılımdan bağımsız, arıza anında güvenli yönde** durdurma.

1. **NC (normally closed) kontak, sürücü enable hattının SERİSİNDE.** Buton basılınca ya da kablo koptuğunda/gevşediğinde döngü açılır → her step sürücüsünün
   `EN` girişi **donanım olarak** devre dışı kalır. ESP32 çalışsa da çalışmasa da (kilitlenme, reset, firmware hatası) sürücü enerjili kalamaz. Bunu sağlamak için `EN` hattı,
   NC zinciri kapalı değilken sürücünün devre dışı olduğu mantıkla bağlanmalıdır (sürücünün EN polaritesine göre çekme direnci; **ESP32 pinleri boot/reset sırasında yüksek empedanslı
   olduğundan sürücüler varsayılan olarak DEVRE DIŞI olmalıdır**: çekme direnci şart, ESP32 çıkışı yalnızca *ek* bir izin verir: `EN_etkin = NC_kapalı AND ESP32_izin`).
2. **ESP32 aynı döngüyü okur** (optokuplör/gerilim bölücü ile; yalnızca bilgi): `HW_LOOP_OPEN` bayrağı, `ESTOPPED/HW_BUTTON` ve `FAULT` olayı. Okuma, **koruma değil raporlamadır**.
3. **Motor güç hattı** için (özellikle yüksek akım/gerilimde): aynı NC zincirinden sürülen kontaktör ya da **sertifikalı güvenlik rölesi** önerilir; yalnız `EN` kesmek, sürücü arızalanırsa
   yetmez. İnsan robotun yakınındaysa güvenlik rölesi **zorunlu** kabul edilmeli; çift kanallı (iki kontaklı) acil dur butonu tercih edilir.
4. **Otomatik yeniden başlama yok:** buton serbest bırakılınca kilit açılmaz (§4 kural 2-3): `CLEAR` + yeniden homing.
5. **Yazılım acil dur** (komut/watchdog/following error/limit) donanım zincirini kesmez; yalnızca firmware'in sürücüleri ve step üretimini kapatmasıdır. Bu yüzden *ikinci katmandır*, birincisi değildir.

**B3'te gerçek donanımda yapılacak doğrulama listesi (şimdilik hiçbiri yapılmadı):** (a) butona bas: sürücüler anında kesiliyor mu (osiloskop/EN pini)? (b) NC kablosunu sök: aynı mı?
(c) ESP32'yi reset/boot ettir: sürücüler pasif mi? (d) firmware'i kasten kilitle (sonsuz döngü): butona bas, yine de kesiliyor mu? (e) USB'yi çek: ≤ 500 ms'de duruyor mu?
(f) hareket sırasında `DISABLE`/`ESTOP` komutları kayıp çerçeveyle de ulaşıyor mu? (g) kontaktör/güvenlik rölesi zinciri.

## 7. Encoder ve açık döngü kuralları

Eksen başına `encoder: none | incremental | absolute` (HELLO'da bildirilir).

| Kip | Konum bilgisi | Adım kaybı | Homing | ACİL DUR sonrası |
|---|---|---|---|---|
| `none` (açık döngü) | komut sayacı (**tahmin**) | **ALGILANMAZ** | **home/limit anahtarı ŞART** | yeniden homing |
| `incremental` | ölçüm | izlenir: `FOLLOWING_ERROR` | home anahtarı (ya da indeks) şart | yeniden homing |
| `absolute` | ölçüm, referans korunur | izlenir | anahtarsız homing mümkün | `HOMED` kalabilir |

* **Referanslanamayan eksen** (encoder'sız **ve** home anahtarsız) **asla** `HOMED` olamaz: `HOME` → `NO_REFERENCE`, `ENABLE` → `NO_REFERENCE`. Güvenli olmayan çalışmaya izin verilmez.
* **Açık döngü uyarısı:** en az bir eksen `none` ise `STATUS.OPEN_LOOP_ANY` ve eksen bayrağı `OPEN_LOOP` yükselir; arayüz/ROS bunu **kullanıcıya açıkça göstermelidir**
  ("encoder yok: adım kaybı algılanamaz; konum kestirimdir"). Önlemler: ivme/hız marjı düşük, tork marjı yüksek, her oturumda homing, düzenli yeniden homing, mekanik uç
  limit anahtarları.
* **Following error:** encoder'lı eksende `|komut − ölçüm| > FOLLOW_ERR_STEPS` süre `FOLLOW_ERR_MS`'i aşarsa `ESTOPPED/FOLLOWING_ERROR` (varsayılan 200 adım / 100 ms).
* **Limit anahtarı** homing dışında tetiklenirse `ESTOPPED/LIMIT_VIOLATION`. Yazılım limitleri (`SOFT_MIN/MAX`) hedefi **hareketten önce** reddeder (`LIMIT`).

## 8. Simülatör ve test kapsamı

`robot_hw_bridge.sim.Esp32Sim`: saat enjekte edilebilir (testler gerçek zaman beklemez), karışık donanımlı 6 eksen (3 encoder'sız, 2 artımlı, 1 mutlak), NC döngüsü (`press_estop`/`release_estop`),
adım kaybı enjeksiyonu. `HwLink` (host) ve `SimServer`/`SocketTransport` (TCP). Testler: CRC/COBS/HMAC/seq (her tek-bit bozulma reddedilir, gürültüde çözücü düşmez), durum makinesi,
watchdog (MCU ve host yönü, 200-500 aralığı), seq kaybı/yineleme, yinelenen komut, COM kopması, bellek-içi ve TCP hat, gerçek `pyserial.Serial` (POSIX sözde-terminal).

**Simülatörün DOĞRULAMADIĞI şeyler:** gerçek USB/CDC zamanlaması ve tampon davranışı, Windows sürücüleri, step darbesi dinamiği (rezonans, tork), EMI/kablo gürültüsü, ESP32 ISR/FreeRTOS
zamanlaması, gerçek acil dur elektriği. Bunlar B3'te donanımla doğrulanacak.

## 9. Hub köprüsü (Windows)

`ControlHub.exe --serial-bridge COM5 [--serial-baud 921600] [--bridge-listen 127.0.0.1:8770] [--bridge-token ...]` (ya da pencerede "ESP32 donanım köprüsü" bölümü; `--bridge-only`: yalnızca köprü).
Tek istemci; ikinci bağlantı reddedilir. Varsayılan yalnızca `127.0.0.1`; `0.0.0.0` (Docker/başka makine) için **token zorunlu** (istemci ilk satırda `HUBBRIDGE-AUTH <token>\n` yollar): token
şifrelenmeden gider, yalnızca güvenilir ağda kullan; motor komutlarını ağa açmak risklidir. Seri hat yokken gelen istemci reddedilir; hat koparsa istemci kapatılır.

## 10. Sonraki adımlar / açık kararlar

* **B2 (yapıldı, §11):** ROS 2 köprü düğümü, `FollowJointTrajectory`, `/joint_states`, iki yönlü watchdog, Robot API acil dur bağı. Kalan: tek istemci kuralı ve gerçek donanımla doğrulama (B4).
* **B3:** ESP32 firmware (PlatformIO), step üretimi (RMT/LEDC/timer), encoder (PCNT/AS5600...), NC girişi, §6 doğrulama listesi.
* **Açık:** (1) STATUS/heartbeat 50 ms yeterli mi (USB gerçek jitter'ı), (2) tek istemci kuralı B2'de yeterli mi, (3) Wi-Fi anahtar dağıtımı (şimdilik önceden paylaşılan anahtar),
  (4) çok eksenli senkron hareket (şimdilik eksen başına bağımsız `MOVE`; koordineli yörünge B2/B3'te).

## 11. ROS 2 köprü düğümü (`hw_bridge_node`, B2)

Paket `robot_hw_bridge`; ROS'tan bağımsız çekirdek `core.py` (`HwBridge`), Robot API istemcisi `robot_api.py`,
düğüm `node.py`. Eklem↔motor dönüşümü **yalnızca** `robot_core.hardware`'dan gelir (`joints_to_steps`,
`to_steps_int`, `soft_limits`, `hard_limits`); düğümde ikinci bir formül yoktur.

```
ros2 launch robot_hw_bridge hw_stack.launch.py bridge_host:=host.docker.internal bridge_port:=8770
ros2 launch robot_viz web_view.launch.py commands:=true mode:=real use_sim_time:=false
```

| Arayüz | Açıklama |
|---|---|
| `/arm_controller/follow_joint_trajectory` | MoveIt'in kullandığı action; aynı ad, `moveit_controllers.yaml` değişmedi |
| `/joint_states` | `joint_1..joint_6`, konum = MCU komut sayacından (açık döngü: **kestirim**, ölçüm değil) |
| `~/state` (`std_msgs/String`, JSON) | bağlantı, `homed`, MCU durumu, uyarılar |
| `~/home`, `~/clear` (`std_srvs/Trigger`) | homing; NC döngü kapalıyken ACİL DUR kilidini aç |
| parametreler | `hardware_profile`, `bridge_host`, `bridge_port`, `bridge_token` (ya da `HUB_BRIDGE_TOKEN`), `watchdog_ms` (200-500, varsayılan 300), `loop_stall_ms` (50..watchdog, varsayılan 250), `robot_api_url` (yalnızca loopback) |

**Açılış = UNHOMED.** Düğüm hiçbir hedefi homing yapılana kadar kabul etmez (`INVALID_GOAL`). Her acil dur,
arıza ya da hat kaybı bilgiyi geçersiz kılar; yeniden bağlanınca yine homing gerekir. Homing sonrası MCU sayacı,
profilin home konumunun `hardware.py` karşılığına (±3 adım) eşit olmalıdır; değilse düğüm reddeder.

**İki yönlü watchdog.** PC→MCU: kalp atışı 50 ms; düğümün kendi döngüsü `loop_stall_ms` içinde ilerlemezse
kalp atışı kesilir ve MCU watchdog'u (300 ms) robotu durdurur (süreç donsa bile). MCU→PC: telemetri
`watchdog_ms` içinde gelmezse düğüm Robot API'ye `POST /api/stop` (yeniden denemeli) gönderir; Robot API
`estopped` olur, işler iptal edilir.

**Robot API ↔ donanım.** Robot API acil dur kilitlendiyse düğüm donanıma `CMD_ESTOP` yollar (donanım kilidi
`clear` + yeniden homing ister); hedef iptali ise kontrollü `STOP`'tur. Robot API `/api/reset` yalnızca
yazılım kilidini açar; donanım kilidi için `~/clear` ve `~/home` gerekir. **Reset aracı (LLM) yoktur.**

**Önemli: yazılım acil dur donanım acil durdurmanın YERİNE GEÇMEZ.** Bu zincir USB/TCP, PC ve MCU yazılımına
bağlıdır. Güvenlik NC acil dur butonuna (sürücü gücünü donanım düzeyinde keser, §6) dayanır; yazılım yolu
ek bir katmandır.

**Varsayılanlar dardır:** kısa watchdog, `loop_stall_ms` < watchdog, yazılım limitleri profilden (daha dar
olan kazanır), açılışta unhomed, açık döngü uyarısı her zaman görünür.

**Doğrulama (`run_selftest hw`, Esp32Sim ile, fizik/motor yok):** açılış UNHOMED, homing öncesi ret, ölçülen
hareket = `hardware.py` adımları, Robot API acil dur → MCU `ESTOPPED/HOST_CMD`, NC butonu → Robot API
`estopped`, hat kopması, düğüm dondurma (SIGSTOP) → MCU watchdog.
**Doğrulanmadı:** gerçek ESP32/firmware, gerçek motor ve sürücü, Hub köprüsünün gerçek COM portu, gerçek
Windows↔Docker ağı (`host.docker.internal`), uzun süreli jitter.

**Bulgu (B3 için):** MCU (ve Esp32Sim) geri giden `seq`'li çerçeveyi atar; yeniden bağlanan bir istemci
`seq`'i 0'dan başlatırsa `HELLO_REQ` bile duyulmaz. Düğüm kaldığı `seq`'ten sürer, ama düğüm *süreci* yeniden
başlarsa aynı sorun olur. Öneri (v1'e eklemeli): `HELLO_REQ` alındığında MCU `rx` seq izleyicisini sıfırlasın.
