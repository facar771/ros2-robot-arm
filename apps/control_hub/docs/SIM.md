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
| `POST /api/sim/speed` | `{speed_pct?, scale?}`: % 1–100; `scale` = hız tavanı ölçeği 0,05–1,00 (varsayılan profilin `max_velocity_scale`, karma_robot 0,3). Aralık dışı **400** (kırpılmaz), ACİL DUR **423**, SİM dışı **409 `sim_only`** (P1d) |
| `GET /api/speed/limits[?speed_pct=]` | `{robot, mode, scale, scale_locked, speed_pct, joints:[{name, limit_rad_s, limit_deg_s, effective_rad_s, effective_deg_s}], limiting_joint, example_move}` — SUNUCU hesaplar (P1d) |
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
  Etkin üst hız = **profil limiti × tavan ölçeği × hız %** (rad/s ve °/s). Tavan ölçeği YALNIZ SİM'de, 0,05–1,00
  aralığında ayarlanır (`POST /api/sim/speed {scale}`); profil limiti hiçbir koşulda aşılmaz. Değişiklik bir SONRAKİ
  komutta geçerli, çalışan iş eski planıyla biter; jog tavanı her tikte canlı okunur. Eklem hareketinde (senkron profil)
  tüm eklemler AYNI anda varır: **T = max(|Δq|/(v·ölçek·%)) / (1−rampa)**, kısa hareketlerde ivme sınırı uzatır
  (`duration_for`); jog'da adım en kısıtlı eklemin limitine göre ORANTILI küçülür (yön korunur).
  Ölçüm (J2, 6°, %100; gerçek SİM): ölçek 1,0 → tablo tahmini 2,36 sn / ölçülen 2,41 sn; ölçek 0,3 → 5,82 / 5,90 sn.
  En yavaş eklem J2 (profilde 0,08 rad/s, tahmini) süreyi belirler: 11,2° ~ 10,8 s (%100), ~18 s (%60).
