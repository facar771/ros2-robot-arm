# SİM arka ucu (`--sim`) — sözleşme eki (S1)

`UI_CONTRACT.md` §9-B hareket uçlarının SİM'deki gerçek davranışı. (Bu dosya ayrı tutuldu: §9, #103 ile
geliyor; birleşince §9-B buraya bağlanır.) SİM **gerçek robot değildir**; değerler ölçüm değil, **tahmini**dir.

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
| `POST /api/sim/speed` | `{speed_pct}` 1–100 |
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
