# P2 Program sayfası ekran görüntüleri

PR #104 için kanıt görselleri (SAHTE/mock hareket servisi, --fake). Ana koda ait değildir; birleştirilmez.

## SİM (`--sim`) — program 3B robotu gerçekten oynatır (güncelleme)

`program_sim/` altında. Hub: `python3 -m control_hub --sim`, gerçek Chrome (WebGL/SwiftShader), 1280×800 ve 390×844.
Program: J ile "Ust" noktasına git → L ile 5 cm "Alt" noktasına in → bekle → alet %40 aç. Önizleme zincirli (her adım
bir öncekinin bitişinden) geçti, "SAHTE" bandı görünmüyor (`backend = {label: SİM, mock: false}`).

| an | taban ekseni | TCP (m) | alet |
|---|---|---|---|
| önce | 0° | [0.194, 0, 0.1794] | %0 |
| çalışırken | 15.1° | [0.1707, 0.0459, 0.1938] | %0 |
| sonra | 45° | [0.0985, 0.0985, 0.1625] | %40 |

- `program_sim_desktop_once_sonra.png`, `program_sim_phone_once_sonra.png`: 3B kutunun önce / çalışırken / sonra karşılaştırması.
- `program_sim_<cihaz>_1_once_yuklendi.png`, `_2_calisirken.png`, `_3_sonra_bitti.png`: tam sayfa.
- Sahne nesneleri (gerçek HubScene): `['program:target', 'program:wp1', 'program:wp2', 'program:wp3', 'program:path']`; gizmo `program:target`; seçim katmanları `['workpiece', 'environment']`.
- Sayfa hatası yok: masaüstü `[]`, telefon `[]`; yatay taşma yok (telefon: True).
