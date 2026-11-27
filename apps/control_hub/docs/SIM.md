# SİM arka ucu (`--sim`) — sözleşme eki (S1)

`UI_CONTRACT.md` §9-B hareket uçlarının SİM'deki gerçek davranışı. (Kesin sözleşme §9-B; bu dosya SİM ayrıntısı.) SİM **gerçek robot değildir**; değerler ölçüm değil, **tahmini**dir.

## Çalıştırma
```
python -m control_hub --sim [--robot karma_robot] [--fake-speed 10]   # --fake ile birlikte verilemez (çıkış 2)
python -m control_hub --sim --check-config                            # SİM'i açar, FK'yı robot_core ile karşılaştırır
```
`--robot` verilmezse `generated/<robot>/` klasörü olan robot seçilir. `--fake` CI'da aynen durur.

## Uçlar
| Uç | Anlam |
|---|---|
| `POST /api/move` | 202 `{ok,job_id}`; gövde: `{type:'joint', joints_deg:[…], speed_pct?}` · `{type:'linear', pos_m:[x,y,z], rpy_deg?:[w,p,r], speed_pct?}` · `{type:'tool', …}` |
| `POST /api/move/check` | Aynı doğrulama, **hareket etmez** → `{ok, violations:[{kind,at_pct,message}]}` |
| `POST /api/move/stop` · `pause` · `resume` | Duraklat/devam; e-stop kilitliyse 423 |
| `GET /api/move/{job_id}` | İş durumu |
| `POST /api/sim/speed` | `{speed_pct?, scale?, accel_scale?, accel_linked?}` (P1e): SİM'de **sınırsız**; TEK sınır: sayı sonlu (NaN/inf yok), > 0, `speed_pct` ≤ 100000, ölçekler ≤ 1000. Dışı **400** (kırpılmaz, hiçbiri uygulanmaz); `null` alan 400. `scale` / `accel_scale` ACİL DUR'da **423**, SİM dışı **409 `sim_only`** (gerçek/tezgâh/sahte: `speed_pct` 1–100 ve ölçek HİÇ ayarlanamaz — bugünkü kısıt birebir). Profil limitinin üstüne çıkış/dönüş Günlük'e olay (`sim_over_profile`) |
| `GET /api/speed/limits[?speed_pct=]` | `{robot, mode, scale, accel_scale, accel_linked, speed_pct, above_profile, over_speed, over_accel, jog:{gain, requested_gain, capped}, joints:[{name, limit_rad_s, limit_deg_s, effective_rad_s, effective_deg_s, accel_limit_rad_s2, accel_effective_rad_s2, over_speed, over_accel}], limiting_joint, example_move, example_moves:[6°, 15°]}` — SUNUCU hesaplar (P1d/P1e) |
| `POST /api/sim/reset` | Başlangıç duruşu; e-stop kilitliyse 423 |

Hata gövdesi `{ok:false,error,message,at_pct?}`. Kodlar: `joint_limit`, `outside_safe_zone`, `path_blocked`,
`unreachable` (409); `bad_request` (400); `busy` (409); `estop_latched` (423); gövde > 8 KB → 413.
E-stop hareketi anında keser; **sıfırlama otomatik home yapmaz**.

## Zaman
Tick 50 Hz; `time_scale` (`--fake-speed`, 10 = 10× hızlı). Testlerde `SimClock` ile deterministik.

## Snapshot (`robot`)
Gerçek eklem açıları (`rad/deg/min_deg/max_deg/near_limit` — aralığın %5'i içinde), robot_core FK ile `tcp`
(`position`, `wpr_deg`, `pos_m`, `rpy_deg`), `gripper.open_pct`, `safe_zone`, `speed_pct`, `maturity:'tahmini'`,
`note`.

## Doğrusal hareket (S1.1)
* IK çok tohumludur (önce önceki örnek, olmazsa limit ortası + sabit-rastgele tohumlar; en yakın çözüm seçilir).
* `orientation:'free'`: yalnız konum hedeflenir, yönelimi çözücü belirler (`rpy_deg` ile birlikte **400**).
  Home yakınında yönelim sabitken (`fixed`, varsayılan) kutunun çoğu ulaşılamaz; dokun-git için `free` kullan.
  Arayüzde (Parçalar → Dokun-git → **Yönelim**) varsayılan SİM'de **Serbest**, gerçek robotta/tezgâhta **Sabit**'tir; arayüz
  her `/api/move/check` ve `/api/move` çağrısına `orientation` yazar (Serbest'te `rpy_deg` GÖNDERİLMEZ). Ölçüm (SİM, ev
  TCP'sinden yana ±90 mm, plaka üstü z = 90 mm, kutu içi): Sabit → `unreachable`, Serbest → geçer ve TCP hedefe varır
  (robot yönelimi kendi seçer, alet yüzeye dik olmayabilir). Görüntüler: `docs/screenshots/orient_*.png`.
* Yolda eklem sıçraması (> 0,35 rad) **409 `path_blocked`**; eklem-uzayı hareketine OTOMATİK düşülmez.
* Hız: `speed_pct` x tavan ölçeği (`max_velocity_scale`, karma_robot 0,3) x eklem hız sınırı; yüzde TEK kez uygulanır.
  Etkin üst hız = **profil limiti × tavan ölçeği × hız %** (rad/s ve °/s); etkin ivme = **profil ivmesi × ivme ölçeği ×
  hız %** (rad/s²). P1e: bunlar YALNIZ SİM'de **sınırsızdır** (ölçek 1,0 + %400 → profil limitinin 4 katı; "varsayımsal");
  tek sınır sayı sonlu, > 0, % ≤ 100000, ölçek ≤ 1000. İvme ölçeği varsayılan olarak hız ölçeğiyle BİRLİKTE hareket eder
  (`accel_linked`); bağı kaldırınca bağımsız girilir. Gerçek/tezgâhta eski kısıt aynen: ölçek ayarlanamaz (409), % ≤ 100,
  profil limiti aşılamaz. Değerler yalnız bellekte: Hub yeniden başlayınca varsayılana döner, profil/taslak dosyalarına
  HİÇBİR şey yazılmaz. Konum limitleri (eklem aralığı, güvenli kutu) hız ne olursa olsun zorlanır. Değişiklik bir SONRAKİ
  komutta geçerli, çalışan iş eski planıyla biter. Eklem hareketinde (senkron profil) tüm eklemler AYNI anda varır:
  **T = max(|Δq|/(v·ölçek·%)) / (1−rampa)**, kısa hareketlerde ivme sınırı uzatır
  (**T ≥ √(max(|Δq|/(a·ivme ölçeği·%)) / (rampa(1−rampa)))**, `duration_for`); süre en az 0,2 sn (tek karede ışınlanma
  yok). Jog'da adım en kısıtlı eklemin limitine göre ORANTILI küçülür (yön korunur); yüksek %'de jog tavanı o oranda
  çarpılır (**kazanç = max(1, %/100) × max(1, ölçek)**) ama tek tikte en hızlı eklem ≤ 0,1 rad ilerleyecek şekilde
  kıstırılır (`jog.capped`): sayı sınırsız kabul edilir, jog hızı fiziksel olarak çıldırmaz. `speed_pct` ipucu
  `/api/jog` gövdesinde SİM'e gider, gerçek/sahte arka uca iletilmez.
  Ölçüm (J2, 6°, %100; gerçek SİM): ölçek 1,0 → tablo tahmini 2,36 sn / ölçülen 2,41 sn; ölçek 0,3 → 5,82 / 5,90 sn.
  En yavaş eklem J2 (profilde 0,08 rad/s, tahmini) süreyi belirler: 11,2° ~ 10,8 s (%100), ~18 s (%60).
  **P1e ölçümü** (J2, 15°; gerçek SİM, `SimClock` tick 20 ms; kart tahmini = `example_moves[15°]`, ölçülen = iş bitene dek):

  | ölçek | % | ivme ölçeği | kart tahmini (s) | ölçülen (s) | sapma |
  |---|---|---|---|---|---|
  | 0,3 | 100 | bağlı | 14,54 | 14,54 | −0,0 % |
  | 1,0 | 100 | bağlı | 4,36 | 4,38 | +0,5 % |
  | **1,0** | **400** | bağlı | **1,87** | **1,88** | **+0,5 %** |
  | 1,0 | 400 | 4,0 (bağsız) | 1,09 | 1,10 | +0,9 % |
  | 1,0 | 400 | 0,5 (bağsız) | 2,64 | 2,66 | +0,8 % |
  | 1,0 | 1000 | bağlı | 1,18 | 1,20 | +1,7 % |
  | 2,0 | 400 | bağlı | 1,32 | 1,34 | +1,5 % |

  Ölçek 1,0 + %400'de süre İVME sınırlıdır (hız sınırıyla 1,09 sn olurdu): ivme ölçeği hız ölçeğiyle birlikte hareket
  ettiği için tahmin ve ölçüm ±%2 içinde uyuşur (kabul: ±%25).
  **ACİL DUR kesme gecikmesi** (gerçek saat, SİM iş parçacığı 20 ms tick, n = 20; `POST /api/estop` yolu aynı
  `EStop.trip`'i kullanır): iş/jog "durdu" durumuna geçene dek —

  | durum | medyan (ms) | en kötü (ms) | trip sonrası ek eklem yolu (rad, en çok) |
  |---|---|---|---|
  | eklem hareketi %100, ölçek 0,3 | 4,3 | 20,4 | 0,0000 |
  | eklem hareketi %400, ölçek 1,0 | 4,7 | 20,2 | 0,0000 |
  | eklem hareketi %100000, ölçek 1000 (2,9 rad/s'de iken) | 4,2 | 20,1 | 0,0000 |
  | basılı-tut jog %100, ölçek 0,3 | 0,8 | 20,9 | 0,0013 |
  | basılı-tut jog %400, ölçek 1,0 | 0,8 | 21,0 | 0,0052 |
  | basılı-tut jog %100000, ölçek 1000 | 0,8 | 21,0 | 0,0199 |

  Gecikme tek tiktir (≤ 21 ms) ve hızdan bağımsızdır: iş tiki bayrağı hareketten ÖNCE okur; jog tiki ACİL DUR'u görünce
  `_end` çağırır. Ek yol yalnız o tikte (jog'da kazançla orantılı, 0,02 rad'ı geçmez). Gerçek robotta kesme gecikmesi
  robotun kontrolcüsüne bağlıdır; bu ölçüm yalnız SİM'dir.
