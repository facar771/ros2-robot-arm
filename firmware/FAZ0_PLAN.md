# K9 Faz 0: karma robot firmware planı (YALNIZ DOKÜMAN, kod yok)

Durum: **ONAY BEKLİYOR (Ferhat).** Onaylanmadan firmware kodu yazılmaz. Yol haritası: K9 / K3. Sahibi: Robot_9.
Hiçbir şey gerçek donanımda denenmedi; aşağıdaki her donanım iddiası "öneri" ya da "kaynak koddan okundu"dur.

## 1. Ferhat'ın mevcut kodu (kaynak: yüklediği tek dosya, 400 satır)

**Bulgu 1: bu kod ESP32 içindir, ESP8266 NodeMCU için değil.** `analogReadResolution(12)`, `analogSetPinAttenuation`,
`Wire.begin(21, 22)`, `Serial.printf` ve GPIO 32/34/35 yalnız ESP32'de var; ESP8266'nın tek ADC'si ve bu pinleri yok.
(Derlemeyi denemedim; çıkarım kaynak kodundan.) Bu belgede kart "ESP32 paneli" diye anılır. Yanlışsa Soru 1.

**Bulgu 2: Uno koduna ve motor koduna sahip değiliz.** Dosyada Wi-Fi, motor ve Uno ile konuşma kodu yok.
`ROBOT_MODE` yalnız "Robot Bekleniyor" yazar; robot sürmez.

| Konu | İçerik |
|---|---|
| Ne yapıyor | 2 eksen joystick (X, Y) + 1 pot (Z) ile kartezyen mm hedefi (0–400) üretir; LCD'de gösterir; uzun basış: Robot → Manuel → Ayar → Robot. Mod 1: sapmayla konum, Mod 2: uçta sabit hızla ilerle. Ayar sayfasında adım (mm/100 ham) ve hız ayarlanır. |
| Pinler | LCD I2C 0x27: SDA 21, SCL 22 · buton 23 (INPUT_PULLUP) · joystick X 34, Y 35 · Z pot 32 (11 dB, 12 bit) |
| Kütüphaneler | `Arduino.h`, `Wire.h`, `LiquidCrystal_I2C` |
| Seri | `Serial` 115200, **her 500 ms'de bir hata ayıklama satırı yazar** (aşağıdaki UART planı için önemli) |
| RAM/CPU | Büyük yapı yok (~1 KB global); ESP32'de 520 KB RAM, sorun değil. `loop()` sonunda `delay(20)`, her eksende 16 örnekli ADC okuma, LCD I2C yazımı 200 ms'de bir (bloklayıcı). Bu, **röle döngüsünü geciktirir** (bkz. §4). |
| Üretilen çıktı | Mm cinsinden X/Y/Z hedefi. Eklem açısı ya da adım değil; robota hiçbir yere gönderilmiyor. |

## 2. Pin ve bağlantı tablosu (ONAY BEKLİYOR)

Yol haritasındaki iki çelişen bilgi:
- Karar satırı: 2 step (SBT0811) + 6 SG90, servolar A0–A5, step D2–D9.
- Ferhat'ın son listesi: 2× 28BYJ-48 + ULN2003, **1 motor** A4988 (STEP/DIR), 6 SG90, joystick+buton, pot, 16x2 I2C LCD.

Bu belge **son listeyi** esas alır (daha yeni). SBT0811 son listede yok. **Soru 2: toplam kaç step motor var (2 mi, 3 mü)?**
"2× 28BYJ-48 + 1 A4988 motoru" 3 motor eder; "2 step motor" ise biri fazla. Aşağıdaki tablo 3 motor varsayar.

**Uno (öneri, hepsi ONAY BEKLİYOR):**

| Uno pini | Bağlantı | Not |
|---|---|---|
| D0 (RX), D1 (TX) | ESP32 Serial2 (bkz. §3) | USB ile aynı UART; yükleme sırasında bu iki tel sökülür |
| D2–D5 | ULN2003 #1 IN1–IN4 → 28BYJ-48 (A) | AccelStepper `HALF4WIRE` için IN1,IN3,IN2,IN4 sırası Faz 2'de doğrulanır |
| D6–D9 | ULN2003 #2 IN1–IN4 → 28BYJ-48 (B) | aynı |
| D10 | A4988 STEP | |
| D11 | A4988 DIR | |
| D12 | A4988 EN (aktif düşük) | **Donanımda çekme direnciyle varsayılan KAPALI** olmalı (HARDWARE.md §6: `EN_etkin = NC_kapalı AND firmware_izin`) |
| D13 | boş (yerleşik LED, durum göstergesi) | |
| A0–A5 | SG90 servo sinyalleri (dijital olarak) | **Faz 2'de kullanılmaz**; servo K6b/K7'ye kadar bekler. `Servo` kütüphanesi Timer1'i alır. |

**Eksen eşlemesi (hangi motor hangi eklem):** bilinmiyor. **Soru 3.** Profilde `joint_1..joint_6` var; K8 karma profili Robot_2'nin işi.
Faz 2 için yalnız "firmware motor 0/1/2 = şu fiziksel motor" yeterlidir; eklem eşlemesi profil/Faz 3'tür.

**Güç ve acil dur (Faz 2'den önce Ferhat'ın bilmesi gerekenler, HARDWARE.md §6 / K7):**
- Motor ve servo gücü Uno'nun 5 V'undan ALINMAZ. 28BYJ-48 ve A4988 için ayrı besleme, ortak GND.
- NC acil dur butonu motor gücünü/A4988 EN hattını yazılımdan bağımsız keser. Servo hattı için K7 kuralı gelene kadar servo gücü bağlanmaz.
- Yazılım acil dur ve watchdog donanım acil durdurmanın yerine geçmez.

**ESP32 paneli:** mevcut pinler (21, 22, 23, 32, 34, 35) DEĞİŞMEZ. Uno bağlantısı için **GPIO16 (RX2) ve GPIO17 (TX2)** önerilir; mevcut kod bunları kullanmıyor.

| Hat | Bağlantı |
|---|---|
| ESP32 GPIO17 (TX2) → Uno D0 (RX) | doğrudan (3,3 V mantık, Uno VIH ≈ 3 V: sınırda, denenmedi) |
| Uno D1 (TX) → ESP32 GPIO16 (RX2) | **5 V → 3,3 V: gerilim bölücü şart** (örn. 1 kΩ + 2 kΩ) ya da seviye çevirici. ESP32 pini 5 V'u kaldırmaz. |
| GND – GND | ortak |

## 3. Uno'nun tek donanım UART'ı: seçenekler

Uno'da donanım UART'ı yalnız bir tane (D0/D1) ve USB-seri çipi de ona bağlı. HARDWARE.md 921600 baud varsayıyor; **Uno 921600'ü taşıyamaz**
(donanım UART'ında 16 MHz'de 250000 tam, 115200 ≈ %0,8 hata; SoftwareSerial 921600 değil, ~38400 civarı güvenli). Baud ayarlanabilir
(`--serial-baud` Hub köprüsünde var), yani **protokol değişmez**; yalnız parametre.

| Seçenek | PC kablosu | ESP32 hattı | Artı | Eksi |
|---|---|---|---|---|
| **A** PC↔Uno USB, ESP32↔Uno SoftwareSerial | Uno'nun USB'si | D8/D9 gibi pinlerde SoftwareSerial | Kablo yolu doğrudan | SoftwareSerial alırken kesmeleri uzun süre kapatır → step darbesi aksar; ~38400'ü geçmez; D2–D9 pin planı bozulur; USB'yle ESP32 aynı anda çalışabilir ama iki ayrı kanal, iki ayrı test |
| **B** (öneri) ESP32 tek geçit: PC↔ESP32 (USB) ve Wi-Fi↔ESP32, ESP32 Serial2 ↔ Uno donanım UART'ı | ESP32'nin USB'si | Uno D0/D1, donanım UART | Kablo ve Wi-Fi AYNI yoldan (Uno'ya tek tip bayt akışı); Uno'da SoftwareSerial yok; Uno USB'si yalnız yükleme/hata ayıklama | ESP32 çalışmazsa kablo yolu da yok (güvenli yön: Uno watchdog'u durdurur); Uno'ya yazarken D0/D1 telleri sökülmeli; ESP32 USB seri portu açılırken DTR/RTS ile ESP32'yi resetleyebilir (köprü `dtr=False, rts=False` ile açmalı; DENENMEDİ) |
| **C** Yalnız USB, ESP32 sonra | Uno'nun USB'si | yok | En basit ilk hareket | Wi-Fi yolu (K5) için sonra B'ye geçiş gerekir, iki kez iş |

**Öneri: B**, baud **115200** ile başla (Uno'da en sağlam; 250000 sonra denenir). Hesap: MOVE çerçevesi ≈ 27 bayt ≈ 2,4 ms; 50 ms'de bir STATUS (~40 bayt) hattın ~%7'si. 115200 yeter.
Karşılığı: ESP32'deki mevcut `Serial.printf` günlüğü (500 ms'de bir) PC kablosu röle modundayken **kapatılmalı** (protokol akışını bozar); Faz 1'de derleme bayrağı ile.

## 4. Uno / ESP32 görev paylaşımı

| ESP32 paneli | Uno |
|---|---|
| LCD, joystick, buton, pot (mevcut, değişmez) | Çerçeve ayrıştırma (COBS+CRC-16+seq, HARDWARE.md §2–§5), durum makinesi, `MOVE/STOP/ESTOP/ENABLE/HOME/CONFIG`, adım üretimi, gömülü limitler, watchdog |
| USB-seri ve Wi-Fi'den gelen baytları **olduğu gibi** Uno'ya, Uno'dan gelenleri PC'ye taşır (şeffaf röle) | STATUS/ACK/FAULT üretir |
| **Kalp atışı ÜRETMEZ**, çerçeve yorumlamaz, komut enjekte etmez (Robot_1, 7 Kasım) | Watchdog 300 ms yalnız **PC'den gelen geçerli** çerçeveleri sayar |
| Kendi Wi-Fi kopma izlemesi ayrıdır | Açılışta hareketsiz, geçersiz çerçeve düşer |

Röle zamanlaması: mevcut `loop()` `delay(20)` ve bloklayıcı LCD yazımı içeriyor. Röle bu döngüye konursa gecikme +20–30 ms olur ve 300 ms
watchdog payını yer. Öneri: röle ayrı bir FreeRTOS görevinde (ya da `delay` kaldırılıp seri bayt döngüsü her turda boşaltılarak). Faz 1'de ölçülür; şimdi iddia yok.

## 5. Uno bellek bütçesi (2 KB RAM; tahmin, derlenmedi)

| Kalem | Tahmin |
|---|---|
| Alma tamponu (derleme zamanı `MAX_FRAME`) | HARDWARE.md azami yükü 200 B der; Uno için sınır **96 B yük** önerilir (en büyük gerçek mesaj STATUS: 8 eksenle ~83 B). Daha uzun çerçeve düşer (`BAD_LEN` sayacı). ≈ 112 B |
| Gönderme tamponu | ≈ 112 B |
| `cmd_id` önbelleği (son 32, `cmd_id u16`+sonuç) | ≈ 128 B |
| 3 `AccelStepper` + 6 `Servo` durumu | ≈ 150–250 B (kütüphane sürümüne göre; ölçülecek) |
| Hareket tamponu | **yok**: eksen başına tek hedef + hız/ivme (komut `MOVE` tek hedef taşır) |
| Yığın ve `Serial` tamponları | ≈ 150–200 B |
| Toplam | ≈ 800–950 B < 2048 B. Faz 1'de `avr-size` ile gerçek değer yazılır |

CPU: `AccelStepper.run()` Uno'da toplam ~4 kHz civarı; 28BYJ-48 yarım adımda en çok ~500–700 adım/sn, A4988 düşük hızda yeterli. Faz 2'de **azami hız
derleme zamanı sabiti ve düşük** olur (ör. 28BYJ-48 için 200 adım/sn); ölçülecek. I2C/LCD Uno'da YOK (ESP32'de), yani "I2C yazımı adım üretimini bozar" riski bu mimaride oluşmaz.
Servo (Faz 2'de kapalı) `Servo` Timer1'i kullanır ve kesmeyi kısa süre kapatır; step darbesine etkisi servo fazında ölçülür.

## 6. Faz 1–3 planı

- **Faz 1 (hareketsiz, ayrı PR):** `firmware/uno/` PlatformIO (`uno`), `firmware/esp32_panel/` Ferhat'ın mevcut kodu **aynen taşınır** (`env:esp32dev`, derlemesi bozulmaz), COBS+CRC-16+seq (HARDWARE.md §2–§5), röle (bayrakla, varsayılan kapalı), yeni workflow `firmware-ci.yml` (`pio run` her iki hedef; `ci.yml` Robot_3'ün, dokunulmaz). Hareket kodu `-DK9_MOTION=0` ile KAPALI.
- **Faz 2 (YALNIZ Robot_1 "tezgâh modu" kararından sonra):** step hareketi, gömülü dar limit ve düşük hız, watchdog; `tools/bench/` (ROS'suz, operatör her oturumda "motorlar yükten ayrık" yazar).
- **Faz 3:** ROS yolu (Robot_2/Robot_3/Robot_4 işleri; bana ait değil).

## 7. Sorular (Ferhat; hepsi bir arada ama kısa)

1. Elindeki kart ESP32 mi (kodundaki pinler öyle diyor), yoksa gerçekten ESP8266 NodeMCU mu?
2. Toplam kaç step motor var: 2× 28BYJ-48 **ve** 1 A4988 motoru (3 motor) mu, yoksa 2 motor mu?
3. Hangi motor hangi mekanik eksen (taban, omuz, dirsek...)? Şimdilik motor 0/1/2 sıralaması yeter.
4. Uno'da şu an yüklü bir kod var mı (varsa gönder), yoksa Uno boş mu?
5. UART planı B (ESP32 tek geçit, Uno D0/D1) uygun mu? Alternatif: A veya C (§3).

## Yol haritasına öneri

- `HARDWARE.md` §2 "921600 baud" Uno hedefi için geçerli değil; Robot_4 yalnız not düşebilir (Uno hedefi 115200). Protokol değişmez.
- `HARDWARE.md` bulgusu (B3): `HELLO_REQ` alınınca MCU `rx` seq izleyicisini sıfırlamalı; Uno firmware'i Faz 1'de bunu uygulayacak, protokole eklemeli öneri olarak Robot_4/Robot_1 onayı beklenir.

## Denenmeyenler

Gerçek donanım (Uno, ESP32, Wi-Fi, motor, sürücü, ULN2003, A4988, servo) hiç denenmedi. Kod derlenmedi (bu PR'da kod yok). Pin tablosu, gerilim bölücü,
baud seçimi, DTR/RTS reseti, röle gecikmesi, bellek ve hız rakamları tahmindir. ESP32 tespiti yalnız kaynak kodundan.
