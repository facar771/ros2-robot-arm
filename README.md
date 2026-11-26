# Kanıt: R8b — Parçalar mesaj yazıcısı ortak HubPages.say'e geçti (ui-r8b-parts-say)
Yalnız kanıt; kod `ui-r8b-parts-say` dalında. `masaustu/` 1280×800, `telefon/` 390×844.
`onceki/` = temiz main (ee31893); `sonraki/` = PR dalı. Aynı 10 durum, aynı dosya adları (Parçalar sayfası):
00_bos, 01_yuklendi (ok), 02_kaydedildi (ok), 03_gecersiz_alan (bad), 04_olcek_hata (bad), 05_gimbal_uyari (warn),
06_gimbal_temiz, 07_kontrol_gecti (ok), 08_hareket_hata (bad, Playwright route ile 500), 09_hareket_ok (ok).
`mutasyon/masaustu/` = `HubPages.say`'in durum-sınıfı kolu bilerek kapatılınca (ok/warn → yalnız bad) aynı senaryo.
`kabul/` = `python3 -m tools.sim_acceptance` (masaüstü ve `--viewport phone`) raporları: 13 GEÇTİ · 0 KALDI.
