# Faz 3: pot telemetrisi (NodeMCU, HAM ADC)

J1, J2, J7 eksenlerinin mutlak açısını 3 pot (10 kΩ) ile **ham ADC (0..1023)** olarak PC'ye yollar. Kalibrasyon yok (profilde,
Robot_2). Bu bir **ölçümdür ama güvenlik düzeneği değildir**; yazılım acil durdurması donanım acil durdurmanın yerine geçmez.

## Kurallar
* Yalnız **PC'ye**, Wi-Fi'de **ayrı TCP portundan** (8771, salt okunur, tek istemci). Röle 8770'te aynen kalır.
* **Uno UART'ına asla yazılmaz.** Telemetri istemcisinden gelen baytlar atılır (UART'a gitmez). Kalp atışına benzeyen çerçeve yok.
* USB-seri ile telemetri **yok**: UART0 Uno hattıyla ortak.
* Telemetri istemcisi yokken ADC okunmaz, pot pinleri INPUT kalır.

## Bağlantı tablosu (NodeMCU ESP8266)
Üç potun **orta bacağı birleştirilip tek A0'a** gider. Her potun bir ucu "üst" (okunurken 3.3V), öbürü "alt" (okunurken GND).

| Eksen | Kanal | Üst uç (HIGH) | Alt uç (LOW) | GPIO üst/alt |
|---|---|---|---|---|
| J1 | 1 | D0 | D1 | 16 / 5 |
| J2 | 2 | D2 | D5 | 4 / 14 |
| J7 | 7 | D6 | D7 | 12 / 13 |

Kullanma: D3, D4, D8 (boot pinleri), TX/RX (UART0). Okunmayan iki potun **iki ucu da** INPUT (yüksek empedans) olur.
Pot'u çevirince "üst" ucu 3.3V'a yakın tarafa gidiyorsa ham değer artar; yön ters çıkarsa uçları değiştir (kalibrasyon işi değil, kablo).

## Çerçeve (Uno protokolünden ayrı)
COBS + 0x00 ayraç, CRC-16/CCITT-FALSE (little-endian), 14 bayt: `ver=0x54` · `type=0x20` · `seq u16` · `t_ms u32` · `ch u8` ·
`flags u8` (bit0 = GEÇERSİZ) · `raw u16` · `crc u16`. Ham ≤ 8 ya da ≥ 1015 ise GEÇERSİZ (kablo kopması/uçta takılma); değer yine
gönderilir, bayrak "güvenilir sayma" der. Ayrıntı: `firmware/esp_relay/lib/k9telem/k9_telem.h`. Hız toplam 15 Hz (pot başına ~5 Hz).
Hız ve bekleme süresi derleme bayraklarıyla ayarlanır: `-DK9_TELEM_RATE_HZ=10 -DK9_TELEM_SETTLE_US=2000`.

## Canlı bakış
`python tools\pot_view.py <NodeMCU IP>` (PowerShell'de). Kanal başına ham ADC, GEÇERSİZ bayrağı, çerçeve/kayıp/bozuk sayacı.
