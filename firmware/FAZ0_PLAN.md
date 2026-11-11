# K9 Faz 0: karma robot firmware planı (YALNIZ DOKÜMAN, kod yok)

Durum: **Ferhat 11 Kasım'da yanıtladı** (ESP8266 NodeMCU; tüm motorlar Uno'da; NodeMCU yalnız Wi-Fi aktarıcı, aralarında yalnız TX/RX). Faz 1 kodu (hareketsiz) ayrı PR'da: #79. Motor pin numaraları Faz 2 öncesi son kez teyit edilir.
Yol haritası: K9 / K3. Hiçbir şey gerçek donanımda denenmedi; her donanım iddiası "öneri"dir.

## 0. Kapsam (11 Kasım güncellemesi)

- Ferhat'ın eski ekran/joystick/LCD kodu **kapsam dışı**; repoya alınmaz, korunması gerekmez.
- Robot **telefondan** kontrol edilir. Hat: telefon tarayıcısı → Control Hub (PC) → köprü → önce **USB-seri 115200**, sonra **Wi-Fi** → Uno.
- İki firmware: **Uno** (tüm hareket, protokol, watchdog) ve **ESP/NodeMCU aktarıcısı** (şeffaf bayt aktarımı).
- ESP **kalp atışı üretmez**, çerçeve yorumlamaz, komut enjekte etmez (Robot_1, 7 Kasım). Uno watchdog'u (300 ms) yalnız **PC'den gelen geçerli** çerçeveleri sayar.
- Faz 2 (motor) Robot_1'in "tezgâh modu" kararına kadar KAPALI.

## 1. Ferhat'ın donanımı (kendi anlatımı, 11 Kasım; doğrulanmadı)

Bir Arduino Uno + bir NodeMCU, TX/RX ile bağlı. Uno'da 6 servo + A4988 ile sürülen 1 step motor; 2× 28BYJ-48 (ULN2003) var.
**Motor sırası (tabandan uca):**

| Eksen | Mekanik | Motor | Not |
|---|---|---|---|
| 1 | taban | 28BYJ-48 #1 (ULN2003 #1) | step |
| 2 | ana kol | 28BYJ-48 #2 (ULN2003 #2) | step |
| 3 | bir üst | **2× SG90** birlikte (tek biri yetmez), **ters yönlü** | servo çifti |
| 4 | bir üst | **2× SG90** birlikte | servo çifti |
| 5 | bir üst | 1× SG90 | servo |
| 6 | bir üst | 1× SG90 | servo |
| 7 | uç, kıskaç | A4988 ile step | step; yol haritası profilindeki `joint_1..6`'da yok, K8 (Robot_2) kararı |

Toplam: **3 step** (2× 28BYJ-48 + 1 A4988), **6 SG90** (eksen 3 ve 4 çift). 7 eyleyici eksen, protokolde `eksen_sayısı = 7`.
(Bu, yol haritasındaki "2 step SBT0811" bilgisini geçersiz kılar; SBT0811 son listede yok.)

## 2. Pin ve bağlantı tablosu (ONAY BEKLİYOR)

**Varsayılan öneri: tüm motorlar Uno'ya bağlı.** Uno tek hareket denetleyicisi olur, ESP saf aktarıcı kalır.

| Uno pini | Bağlantı | Eksen |
|---|---|---|
| D0 (RX), D1 (TX) | ESP/NodeMCU UART'ı (bkz. §3) | – |
| D2–D5 | ULN2003 #1 IN1–IN4 → 28BYJ-48 | 1 |
| D6–D9 | ULN2003 #2 IN1–IN4 → 28BYJ-48 | 2 |
| A0, A1 | SG90 çifti sinyalleri (iki AYRI pin) | 3 |
| A2, A3 | SG90 çifti sinyalleri (iki AYRI pin) | 4 |
| A4 | SG90 | 5 |
| A5 | SG90 | 6 |
| D10 / D11 / D12 | A4988 STEP / DIR / EN (aktif düşük) | 7 |
| D13 | boş (durum LED'i) | – |

- **Servo çifti:** iki servo aynı sinyal pinine paralel BAĞLANMAZ. Ters yön ve çift akım yüzünden iki ayrı pin; ikinci servoya `toplam − θ` aynalı açı yazılır (kalibrasyon K6b/K7). Servolar Faz 2'de **kullanılmaz**.
- `EN` hattı donanımda çekme direnciyle **varsayılan KAPALI** olmalı (HARDWARE.md §6: `EN_etkin = NC_kapalı AND firmware_izin`).
- Motor ve servo gücü Uno'nun 5 V'undan ALINMAZ; ayrı besleme, ortak GND. NC acil dur butonu motor gücünü/EN hattını yazılımdan bağımsız keser (§6). Servo besleme kesimi K7.
- AccelStepper `HALF4WIRE` için ULN2003 pin sırası (IN1,IN3,IN2,IN4) Faz 2'de doğrulanır.

### Karar (Ferhat, 11 Kasım): tüm motorlar Uno'da
Ferhat önce 28BYJ-48 sürücülerinin 8 pinini NodeMCU'ya bağlamayı düşünüyordu; "tüm motorlar Uno'ya sığıyorsa hepsini taşı" dedi. **Sığıyor** (D2–D9, D10–D12, A0–A5; D0/D1 UART). Böylece
NodeMCU saf aktarıcı kalır (Robot_1, 7 Kasım kararı) ve Uno tek hareket denetleyicisidir. Bu çelişki kapandı.

## 3. UART: Uno'nun tek donanım UART'ı

D0/D1 hem USB-seri çipine hem ESP'ye bağlanamaz (aynı anda iki sürücü). **Uno 921600 baud taşıyamaz**
(donanım UART'ı 16 MHz'de 250000 tam, 115200 ≈ %0,8 hata; SoftwareSerial yaklaşık 38400'ü geçmez). Baud ayarlanabilir
(Hub köprüsünde `--serial-baud` var): **protokol değişmez, yalnız parametre.**

| Seçenek | PC kablosu | ESP hattı | Artı | Eksi |
|---|---|---|---|---|
| B: ESP tek geçit (bırakıldı): PC USB→ESP, Wi-Fi→ESP, ESP↔Uno D0/D1 donanım UART | ESP'nin USB'si | Uno D0/D1 | Kablo ve Wi-Fi aynı yoldan; Uno'da SoftwareSerial yok | ESP çökerse yol kesilir (güvenli yön: Uno watchdog'u durdurur); Uno'ya yazarken D0/D1 telleri sökülür; USB-seri açılırken DTR/RTS ESP'yi resetleyebilir (köprü `dtr=False, rts=False` açmalı; DENENMEDİ) |
| **C (seçildi, kablo yolu):** PC USB→Uno doğrudan; Wi-Fi için ESP tellerini D0/D1'e takıp USB'yi çek | Uno'nun USB'si | yok | ESP olmadan en hızlı ilk hareket | Wi-Fi'ye geçerken D0/D1 ESP'ye taşınır; kablo yolu değişir, iki kez test |
| A: SoftwareSerial ile ESP | Uno USB | yazılım seri | – | Alma sırasında kesmeleri kapatır → step darbesi aksar; D0/D1 yine USB'ye bağlı. **Önerilmez** |

**Karar (Ferhat'ın tasarımı: ESP yalnız Wi-Fi ve TX/RX): iki ayrı yol, AYNI ANDA değil.**
- **Kablo:** PC USB → Uno USB (115200). ESP'nin TX/RX telleri Uno'dan sökülü.
- **Wi-Fi:** ESP8266 TX (GPIO1) → Uno D0; Uno D1 → gerilim bölücü (1 kΩ + 2 kΩ) → ESP8266 RX (GPIO3); Uno harici beslemeden (USB çekili). ESP kalp atışı üretmez.
- Baud 115200 her iki yolda. MOVE çerçevesi ≈ 27 bayt ≈ 2,4 ms; 7 eksenli STATUS (≈ 86 bayt ≈ 7,5 ms) 50 ms'de bir: hattın ~%15'i. Yeter.
- ESP8266 notu: UART0'da açılışta 74880 baud'da çöp bayt çıkar (Uno çözücüsü düşürür); UART0'a günlük yazılmaz. ESP'yi yüklerken D0/D1 tellerini sök.
- Eski öneri B (kablo da ESP'den geçer) bırakıldı: Ferhat'ın tasarımı ESP'yi yalnız Wi-Fi'ye ayırıyor.

## 4. Görev paylaşımı

| ESP/NodeMCU aktarıcısı | Uno firmware'i |
|---|---|
| USB-seriden ve Wi-Fi'den (TCP) gelen baytları **olduğu gibi** Uno'ya, Uno'dan gelenleri geri taşır | Çerçeve ayrıştırma (COBS+CRC-16+seq, HARDWARE.md §2–§5), durum makinesi, `MOVE/STOP/ESTOP/ENABLE/HOME/CONFIG` |
| **Kalp atışı ÜRETMEZ**, çerçeve yorumlamaz, komut enjekte etmez | Adım üretimi, gömülü eksen limitleri ve azami hız (derleme sabiti) |
| Kendi Wi-Fi kopma izlemesi ayrı (bağlantıyı kapatır, bayt üretmez) | Watchdog 300 ms: yalnız PC çerçeveleri; açılışta hareketsiz; geçersiz çerçeve düşer |
| Wi-Fi şifresi/token: gitignore'lu ayrı yapılandırma dosyası, kodda ve günlükte yok | STATUS/ACK/FAULT üretir |

Röle döngüsü bloklayıcı işlem içermemeli (LCD yok, uzun `delay` yok); gecikme Faz 1'de ölçülür, şimdi iddia yok.

## 5. Uno bellek bütçesi (2 KB RAM; tahmin, derlenmedi)

| Kalem | Tahmin |
|---|---|
| Alma tamponu (derleme zamanı `MAX_FRAME`) | HARDWARE.md azami yük 200 B der; Uno için **96 B yük** sınırı önerilir (7 eksenli STATUS ≈ 74 B). Fazlası düşer. ≈ 112 B |
| Gönderme tamponu | ≈ 112 B |
| `cmd_id` önbelleği (son 32) | ≈ 128 B |
| 3 `AccelStepper` + 6 `Servo` durumu | ≈ 150–250 B (ölçülecek) |
| Hareket tamponu | yok: eksen başına tek hedef + hız/ivme |
| Yığın + `Serial` tamponları | ≈ 150–200 B |
| Toplam | ≈ 800–950 B < 2048 B; Faz 1'de `avr-size` ile gerçek değer |

CPU: `AccelStepper.run()` Uno'da toplam ~4 kHz civarı; Faz 2'de eksen başına **azami hız derleme sabiti ve düşük** (örn. 28BYJ-48 için 200 adım/sn; ölçülecek).
I2C/LCD Uno'da YOK (PCA9685 yok), yani "I2C yazımı adım üretimini bozar" riski oluşmaz. `Servo` Timer1'i alır ve kesmeyi kısa süre kapatır; step darbesine etkisi servo fazında ölçülür.

## 6. Faz 1–3 planı

- **Faz 1 (hareketsiz, PR #79):** `firmware/uno/` PlatformIO (`uno`) iskeleti; `firmware/esp_relay/` (ESP8266 aktarıcısı); COBS+CRC-16+seq; hareket kodu `-DK9_MOTION=0` ile KAPALI; yeni `firmware-ci.yml` (`pio run`; `ci.yml` Robot_3'ün, dokunulmaz).
- **Faz 2 (YALNIZ Robot_1 "tezgâh modu" kararından sonra):** 3 step için hareket, gömülü dar limit/düşük hız, watchdog; `tools/bench/` (ROS'suz, her oturumda operatör "motorlar yükten ayrık" yazar). Servo K6b'ye kadar bekler.
- **Faz 3:** ROS yolu, Hub, telefon, karma robot profili (Robot_2/3/4); bana ait değil.

## 7. Sorular

Cevaplandı (Ferhat, 11 Kasım): kart **ESP8266 NodeMCU**; motorlar **Uno'da**; toplam **3 step** (2× 28BYJ-48 + A4988 kıskaç); UART: kablo=Uno USB, Wi-Fi=ESP TX/RX.
Açık (Faz 2 öncesi, kod yazmayı bloklamaz): (1) D2–D12/A0–A5 pin numaraları son teyit; (2) NC acil dur döngüsünün Uno'da okunacağı pin; (3) switch'siz step eksenlerin referans kuralı (Robot_1, #79).

## Yol haritasına öneri

- K9 satırındaki "Ferhat'ın mevcut kodu bozulmaz" ifadesi artık geçersiz (eski panel kodu kapsam dışı). Sahip adı: yol haritası Robot_9 diyor, görev Robot_10'a verildi; müdür günceller.
- Bağlantı çelişkisi: "2 step SBT0811" bilgisi geçersiz; gerçek liste 3 step + 6 SG90 (eksen 3 ve 4 servo çifti).
- `HARDWARE.md` §2: 921600 baud Uno hedefinde geçerli değil (Uno 115200; Robot_4 not düşebilir). Protokol değişmez.
- B3 bulgusu (`HELLO_REQ` seq sıfırlama) Uno firmware'inde Faz 1'de uygulanır; protokole eklemeli öneri Robot_4/Robot_1 onayına bağlı.
- Çift servo ve kıskaç (eksen 7) için profil/protokol kararı K6b (Robot_4) ve K8 (Robot_2) işi.

## Denenmeyenler

Gerçek donanım (Uno, ESP32/ESP8266, Wi-Fi, motor, sürücü, ULN2003, A4988, servo) hiç denenmedi. Derleme yok (kod yok). Pin tablosu, gerilim bölücü, baud,
DTR/RTS reseti, röle gecikmesi, bellek ve hız rakamları tahmindir. Motor/eksen bilgisi Ferhat'ın sözlü anlatımıdır, doğrulanmadı. ESP8266 ve Wi-Fi yolu hiç denenmedi.
