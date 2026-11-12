# Tezgâh bağlantısı (Faz 2a) — Ferhat için tek sayfa

Amaç: Uno'ya **üç step motoru** bağlayıp yükten ayrık, masada, yavaş yavaş denemek. Servolar (SG90) bu aşamada **yok**, bağlama.
Bu sayfa `firmware/FAZ0_PLAN.md` pin tablosuna ve `firmware/uno/src/bench_motors.h` koduna göre yazıldı.

Etiketler: **[KESİN]** koddan/plandan okunmuş. **[GENEL BİLGİ]** üretici/genel bilgi, senin kartında doğrula. **[ÖNERİ]** benim tavsiyem, zorunlu değil. **[BİLİNMİYOR]** bende yok, sana soruyorum (en altta).

> **Güvenlik:** Motorlar kola/yüke bağlı DEĞİL, mil serbest. Motor güç kaynağının fişine/anahtarına elin yetişsin. Yazılım acil durdur, gerçek acil durdurmanın yerini TUTMAZ.

---

## 1. Hangi kart
**Arduino Uno** (tek kart). NodeMCU/ESP8266 bu denemede **kullanılmıyor**; D0/D1 pinlerine tel bağlıysa **sök** (yükleme ve USB-seri bunları kullanır). **[KESİN]**

## 2. Hangi pine ne gider (Uno tarafı) **[KESİN]**

| Uno pini | Nereye | Ne işe yarar |
|---|---|---|
| D2 | ULN2003 #1 **IN1** | J1 (taban, 28BYJ-48) |
| D3 | ULN2003 #1 **IN2** | J1 |
| D4 | ULN2003 #1 **IN3** | J1 |
| D5 | ULN2003 #1 **IN4** | J1 |
| D6 | ULN2003 #2 **IN1** | J2 (ana kol, 28BYJ-48) |
| D7 | ULN2003 #2 **IN2** | J2 |
| D8 | ULN2003 #2 **IN3** | J2 |
| D9 | ULN2003 #2 **IN4** | J2 |
| D10 | A4988 **STEP** | J7 (kıskaç) adım darbesi |
| D11 | A4988 **DIR** | J7 yön |
| D12 | A4988 **EN** (+ çekme direnci, §4) | J7 sürücüyü aç/kapa. **Açılışta KAPALI** olmalı |
| D13 | Acil dur döngüsü, öbür ucu **GND** | §5 |
| GND | ULN2003 GND'leri, A4988 GND, motor güç kaynağı eksisi | **hepsi ortak GND** |
| USB | Bilgisayar | hem güç hem yükleme/haberleşme |

Kullanma: A0–A5 (servo, sonraki aşama), D0/D1 (USB-seri).

IN1..IN4 sırasını **karıştırma**: kod bu sıraya göre yazıldı (`bench_motors.h`: kod ULN2003'e IN1, IN3, IN2, IN4 sırasıyla sürer; sen kabloları IN1→D2, IN2→D3, IN3→D4, IN4→D5 diye düz bağla).

## 3. Bağlantı şeması (ASCII)

```
                 BİLGİSAYAR ──USB──► [ ARDUINO UNO ]
                                       D2 D3 D4 D5   D6 D7 D8 D9   D10  D11  D12  D13  GND
                                       │  │  │  │    │  │  │  │    │    │    │    │    │
                                      IN1 IN2 IN3 IN4 IN1 IN2 IN3 IN4 STEP DIR  EN   │    │
                                     [ULN2003 #1]    [ULN2003 #2]   [   A4988   ]    │    │
                                       │ 5 pinli       │ 5 pinli       │ 1A 1B 2A 2B   │    │
                                    28BYJ-48 (J1)   28BYJ-48 (J2)    J7 step motoru   │    │
                                                                                      │    │
          NC buton / jumper  ◄────────────────────────────────────────────────────────┘    │
          (D13 ile GND arasında, KAPALI = devre tam)  ──────────────────────────────────────┘

   MOTOR GÜCÜ (Uno'dan DEĞİL, ayrı kaynak; fişi elinin altında):

      [Ayrı güç kaynağı +] ──┬─► ULN2003 #1 "+"   ──► ULN2003 #2 "+"        (gerilim: §4, [BİLİNMİYOR])
                             └─► A4988 VMOT  (+ 100 µF kondansatör VMOT–GND, §4)
      [Ayrı güç kaynağı –] ──┬─► ULN2003 "–" (iki kart)
                             ├─► A4988 GND (motor tarafı)
                             └─► Uno GND   (ORTAK GND: bunu unutma)

      A4988 mantık gücü: VDD ◄── Uno 5V,  GND ◄── Uno GND.  (Motor akımını Uno'nun 5V'undan ÇEKME)
```

## 4. ULN2003 ve A4988 besleme / GND / Vref

**Genel kural [KESİN, plandan]:** Motor gücü Uno'nun 5 V'undan alınmaz; ayrı besleme, **ortak GND**.

**ULN2003 kartları (J1, J2):**
- Motorun 5 pinli beyaz fişi karta takılı olmalı (fiş tek yöne girer).
- Kartın kendi güç girişi vardır ("+" / "–"). Standart 28BYJ-48 **5 V**'tur **[GENEL BİLGİ]**; 12 V'luk varyantı da var, kutusuna/etiketine bak, yanlış gerilim motoru ısıtır/yakar. Hangisi olduğunu bana yaz (§8).
- Karttaki güç jumper'ı/anahtarı varsa **açık** (LED'ler yanabilsin).
- Kartın "–"si Uno GND'sine bağlı olmalı.

**A4988 (J7):**
- VMOT = motor gücü ("+"), yanında **100 µF** civarı kondansatör VMOT–GND arasında olmalı **[GENEL BİLGİ]** (yoksa sürücü ölebilir). Gerilim aralığı kartın üstünde/satıcıda yazar; tipik 8–35 V **[GENEL BİLGİ, doğrula]**.
- VDD = Uno 5V, GND = Uno GND. İkinci GND'si motor kaynağı eksisine.
- **RESET ve SLEEP pinleri birbirine bağlı** olmalı (çoğu kartta tel gerekir) **[GENEL BİLGİ]**.
- **EN:** D12'ye bağlı ve ayrıca **EN ile 5 V arasına bir çekme direnci (10 kΩ)** koy; böylece Uno resetlenirken/açılırken sürücü KAPALI kalır **[ÖNERİ, mantık KESİN: EN aktif düşük, yüksek = kapalı]**.
- **MS1/MS2/MS3:** mikroadım ayarı. Boş bırakırsan çoğu kartta tam adımdır **[GENEL BİLGİ]**. Senin kartta nasıl olduğunu bilmiyorum → bana yaz (§8). Firmware adımları sayar, mikroadımı bilmez.
- **Motor kablosu:** step motorun iki bobin çifti **1A-1B** ve **2A-2B**'ye gider. Hangi iki tel hangi bobin: multimetreyle direnç ölç, aynı bobinin telleri arasında birkaç ohm görürsün, farklı bobinlerde açık devre **[GENEL BİLGİ]**. **Güç açıkken motoru takma/sökme** (sürücü ölür).
- **Vref (akım ayarı):** kart üstündeki küçük potansiyometre. Formül kart üzerindeki direnç değerine bağlıdır (çoğu kartta `Akım ≈ Vref / (8 × Rsense)`) **[GENEL BİLGİ]**. Rsense değerini bilmiyorum; **mevcut Vref'i (multimetre, pot ile GND arası) ve motorun etiket akımını bana yaz**, ben hesaplayıp önerelim. Önce **düşük** başla; ayarı yaparken motoru **bağlı** tut.

## 5. D13 acil dur döngüsü **[KESİN]**
- D13 ile GND arasına **normalde kapalı (NC) buton** ya da bu deneme için bir **jumper** bağla. Devre **tam (kapalı)** = her şey yolunda.
- Devre **açılırsa ya da hiç bağlı değilse**: firmware `ESTOPPED` olur, motorlar durur, bobinler kapanır; **kilit açılana kadar hareket yok**.
- Yani jumper takılı değilken `status` "ESTOPPED / HW_LOOP_OPEN" gösterir; bu **hata değil**, güvenlik davranışı.
- Bu pin sadece **bildirimdir**. Gerçek güvenlik için **motor güç kaynağının fişini/anahtarını** kullan **[ÖNERİ]**.

## 6. Kablolama kontrol listesi (güç VERMEDEN önce, hepsini işaretle)
- [ ] Motorlar yükten ayrık, miller serbest
- [ ] D0/D1'de tel yok (ESP varsa söküldü)
- [ ] D2..D5 → ULN2003 #1 IN1..IN4 (sırayla), D6..D9 → ULN2003 #2 IN1..IN4
- [ ] 28BYJ-48 fişleri kartlara tam oturdu
- [ ] D10→STEP, D11→DIR, D12→EN; EN'de 10 kΩ çekme (5 V'a)
- [ ] A4988: RESET-SLEEP bağlı, VDD=5V, GND=GND, VMOT'ta 100 µF
- [ ] Motor kaynağı eksisi = Uno GND = ULN2003 "–" = A4988 GND (ortak)
- [ ] Motor kaynağı artısı yalnız ULN2003 "+" ve A4988 VMOT'a gidiyor, **Uno'ya gitmiyor**
- [ ] Gerilimler motorlara uygun (ULN2003/28BYJ-48: etikete bak; A4988 VMOT: kart aralığında)
- [ ] D13–GND jumper/NC buton takılı
- [ ] Motor güç kaynağının fişi/anahtarı elinin altında
- [ ] Sadece USB takılı, motor kaynağı **henüz kapalı**

## 7. Yükleme ve ilk deneme

Önce kodu al (**PowerShell'de**, repo klasöründe):
```powershell
git pull
pip install platformio pyserial
```
Portu bul (**PowerShell'de**, Uno takılıyken; çıktı örn. COM3):
```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```
Yükle (**PowerShell'de**; COM3'ü kendi portunla değiştir):
```powershell
pio run -d firmware\uno -e uno_bench -t upload --upload-port COM3
```
Beklenen: sonda `SUCCESS`. Motor kaynağı kapalıyken motorlar zaten kıpırdamaz.

Hat kontrolü (**PowerShell'de**):
```powershell
python tools\bench\bench.py --port COM3 status
```
Beklenen: `durum UNHOMED`, bayraklarda `BENCH_MODE`, `HW_LOOP_OPEN` **yok**, üç eksen `konum 0`. İlk bağlanışta Uno resetlendiği için araç ~2 sn bekler, normal.

İlk hareket (önce **motor kaynağını aç**; **PowerShell'de**):
```powershell
python tools\bench\bench.py --port COM3 shell
```
`bench>` isteminde sırayla:
```
zero j1
enable
move j1 20
move j1 -20
disable
quit
```
- `zero j1` senden `EVET` yazmanı ister: yalnız motor yükten ayrık ve elin güçteyse yaz.
- Beklenen: `move j1 20` ile milin **yavaşça, düzgün** hafif dönmesi (20 yarım adım ≈ 2° **[GENEL BİLGİ: ~4096 yarım adım/devir varsayımı]**), ardından `vardı: konum 20`. İlk hareket en çok 56 adım olabilir, araç fazlasını reddeder.
- Önce **yalnız J1**; J1 düzgünse J2'yi (`zero j2`...), en son J7'yi dene. J7'de derece yerine **adım** kullan (adım/devir bilinmiyor); ilk hareket en çok 10 adım.
- Çıktıyı bana yapıştır.

## 8. Sık hatalar
| Belirti | Büyük ihtimalle | Ne yap |
|---|---|---|
| Motor **titriyor/vızıldıyor ama dönmüyor** (ULN2003) | IN kablo sırası yanlış ya da bir tel gevşek | Güç kes. D2..D5 sırasını (IN1→D2, IN2→D3, IN3→D4, IN4→D5) ve fişi kontrol et |
| Hiç kıpırdamıyor, ULN2003 LED'leri yanmıyor | Karta güç gelmiyor / GND ortak değil | Kaynak "+/–" ve ortak GND'yi kontrol et |
| LED'ler yanıyor, motor yine dönmüyor | `enable` yapılmadı ya da eksen `zero`lanmadı | `status` bak: eksende `homed, enabled` yazmalı |
| `status`: **ESTOPPED / HW_LOOP_OPEN** | D13–GND bağlı değil | Jumper/NC butonu tak, `clear`, sonra tekrar `zero` |
| Hareket sırasında aniden durdu, kilitlendi | Watchdog (haberleşme kesildi) ya da D13 açıldı | `status` bak, `clear`, tekrar `zero` ve `enable` |
| `Hat kurulamadı` | Yanlış COM portu, kablo, firmware yüklenmedi, D0/D1'de tel | Portu bul (§7), kabloyu çıkar-tak, tekrar yükle |
| Yükleme hatası | D0/D1'de tel var ya da başka program portu tutuyor | Telleri sök, seri monitörleri kapat |
| J7 hiç dönmüyor | EN çekme direnci hatalı, Vref sıfıra yakın, motor bobin çifti yanlış, VMOT yok | Güç kes, §4'ü baştan kontrol et |
| J7 ters yöne dönüyor | Bobin kablosu ters | Güç kes, bir bobinin iki telini yer değiştir (yazılımda ters çevirme yok) |
| A4988 ya da motor çok ısınıyor | Vref (akım) yüksek | Güç kes, Vref'i düşür, değerini bana yaz |
| Motor çok yavaş/yüksek sesli | Beklenen: hız kasıtlı düşük (J1/J2 100 adım/sn, J7 50 adım/sn) | Normal |

Ters giderse sırayla: terminalde `estop` yaz **veya** motor güç kaynağının fişini çek → D13 jumper'ını sök → USB'yi çek.

## 9. Sana soruyorum (tek mesajda yanıtla; bunlar olmadan derece ve akım hesabı yapamam)
1. **28BYJ-48:** 5 V mu 12 V mu (etikette yazar)? Dişli 1/64 mü?
2. **J7 motoru:** marka/model ya da etiket, **adım/devir** (200 mü 400 mü), etiket akımı (A) ve gerilimi.
3. **A4988:** MS1/MS2/MS3 pinleri boş mu, VDD/GND'ye mi bağlı? Kart üstündeki akım-ölçme direnci yazısı (R050, R100 gibi) ve şu anki **Vref** (volt).
4. **Motor güç kaynağı:** J1/J2 için kaç volt/amper, A4988 VMOT için kaç volt/amper (aynı kaynak mı?).
5. Uno'nun **COM portu** (§7 komutunun çıktısı).
