# SİM kabul raporu

- **Hub:** python -m control_hub --sim (temiz geçici veri klasörü)
- **Görünüm:** desktop 1280×800
- **Zaman:** 2026-11-25 20:57:18
- **Not:** SİM ölçümleri gerçek robot ölçümü DEĞİLDİR (tahmini).

**Sonuç: 0 GEÇTİ · 1 KALDI · 0 ATLANDI**

## KALDI listesi
- `parametreler.sim_uygula` — Parametreler: taslakta J1 üst limiti düşür → SİM'e uygula → limit dışı hedef reddedilir → Sıfırla: Sıfırla: 10 sn içinde gerçek profile dönülmedi

| Adım | Durum | Ölçülen | Ekran |
|---|---|---|---|
| `parametreler.sim_uygula` Parametreler: taslakta J1 üst limiti düşür → SİM'e uygula → limit dışı hedef reddedilir → Sıfırla | KALDI | J1 üst limit (başlangıç, °): 170.00 ° (beklenen 170.00 ± 0.01)<br>kayıtlı taslak: axes.joint_1.max_deg = 10<br>J1 üst limit (uygulanınca, °): 10.00 ° (beklenen 10.00 ± 0.01)<br>üst çubuk rozeti: TASLAK PROFİL (SİM)<br>limit dışı hedef J1=20°: HTTP 409 joint_limit<br>limit içi hedefe varış J1 (°): 8.00 ° (beklenen 8.00 ± 0.5) | [uygulandi](shots/parametreler_sim_uygula__uygulandi.png) [kaldi](shots/parametreler_sim_uygula__kaldi.png) |
