# A1 Algoritmalar sayfası ekran görüntüleri

Algoritmalar PR'ı için kanıt görselleri (`ui-p3-algo`). Ana koda ait değildir; birleştirilmez.

Hub: `python3 -m control_hub --sim --access-mode open --no-qr` (SİM; "SAHTE" bandı yok, `backend = {label: SİM, mock: false}`),
gerçek Chrome (WebGL/SwiftShader), masaüstü 1280×800 ve telefon 390×844. Her dosya `full_page` ekran görüntüsüdür;
`crop_*` yalnız 3B kutudur. Gerçek robot veya TEZGÂH YOK: hiçbir şey ölçülmedi.

## Sayfa: `algo/`

| dosya | durum | özet satırı (masaüstü = telefon) |
|---|---|---|
| `algo_<cihaz>_1_izgara.png` | ızgara 2×3, varsayılanlar | 55 adım · ✓ 49 hareket/alet adımı güvenli kutuda ve ulaşılabilir |
| `algo_<cihaz>_2_daire.png` | daire/yay, varsayılanlar | 13 adım · ✓ 13 hareket/alet adımı güvenli kutuda ve ulaşılabilir |
| `algo_<cihaz>_3_zigzag.png` | zigzag, varsayılanlar | 10 adım · ✓ 10 hareket/alet adımı güvenli kutuda ve ulaşılabilir |
| `algo_<cihaz>_4_ihlal.png` | ızgara 1×3, aralık 30, ilk hücre y=60 → 3. hücre y=120 mm kutu dışı | 28 adım · ✗ 3 ihlal (adım 25, 26, 28): kırmızı satırlar |
| `algo_<cihaz>_4b_ihlal_satir.png` | aynı, liste kırmızı satırlara kaydırılmış (neden yazılı) | |
| `algo_<cihaz>_5_gecersiz_alan.png` | "Aralık" alanına `abc`: alan kırmızı, "Programa ekle" kapalı (istek gitmediği node testinde sınanır) | `Geçersiz değer — Aralık: sayı olmalı` |
| `algo_<cihaz>_6_eklendi.png` | "Programa ekle" sonrası onay + "Program sayfasına git" | `13 adım Program'a eklendi (toplam 13). Önizle ile yeniden denetle.` |

- İhlal durumunda kırmızı satırlar (0 tabanlı satır): `['24', '25', '27']` = adım 25, 26, 28 (3. hücrenin üstü, hücre, üstü); neden:
  "Hedef güvenli çalışma kutusunun dışında (y=0.120 m, izin verilen aralık [-0.1, 0.1]), robot hareket etmedi".
- 3B (gerçek HubScene) nesneleri ihlal durumunda: `['algo:path', 'algo:m3', 'algo:m7', 'algo:m16', 'algo:m25', 'algo:s24', 'algo:b24', 'algo:s25', 'algo:s27', 'algo:b27']` (`algo:b*` kırmızı işaret, `algo:s*` kırmızı parça,
  `algo:m*` numaralı işaret; ihlalli olanın rengi kırmızı). Varsayılan ızgara: `['algo:path', 'algo:m3', 'algo:m7', 'algo:m16', 'algo:m25', 'algo:m34', 'algo:m43', 'algo:m52']`.
- Başka sayfaya geçince `algo:` nesnelerinin hepsi kalkar: `[]`.
- Sayfa hatası yok: masaüstü `[]`, telefon `[]`; yatay taşma yok (ihlal durumu masaüstü True, telefon True).
- Telefon sırası: form → 3B → sonuç (değerin etkisi hemen altında görünür).
- 3B çizgiler WebGL `Line` (1 px) olduğundan ince kalır; ihlalli noktalar bu yüzden ayrıca büyük kırmızı işaretle gösterilir.

## Program sayfasına aktarım: `program/`

| dosya | durum | kanıt |
|---|---|---|
| `program_<cihaz>_7_yay_eklendi_onizleme.png` | geçerli yay eklendi → Önizle | 13 adım, 13 × "yönelim: serbest", `✓ Önizleme geçti (13 hareket denetlendi)`, Çalıştır açık: True |
| `program_<cihaz>_8_ihlalli_program.png` | ihlalli ızgara eklendi → Önizle | `✗ 3 ihlal: kırmızı satırlara bak`, kırmızı adımlar (0 tabanlı) [24, 25, 27], Çalıştır açık: False |

Adsız Program'a eklenirse ad varsayılan olarak üretecin adı olur (ör. "Izgara palet pick-place"); kullanıcının yazdığı ad ezilmez.
