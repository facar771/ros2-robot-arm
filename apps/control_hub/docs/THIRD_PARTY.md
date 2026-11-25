# Üçüncü taraf bileşenler (Control Hub)

Bu dosya yalnız **STEP/IGES dönüştürücüsünü** (S2b) anlatır; Hub'ın öteki bağımlılıkları `requirements.txt`'tedir.
Parçalar sayfası dönüştürücü kuruluyken şunu gösterir: *OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)*.

## OpenCASCADE Technology (OCCT)

| | |
|---|---|
| Ne için | STEP/IGES dosyasını üçgen mesh'e çevirir (yalnız Parçalar sayfası, yükleme anında) |
| Sürüm | **8.0.1** — cascadio 0.1.1 içindeki ikililerden okundu (Linux `libTK*.so.8.0.1`; Windows `TKernel` DLL'inde `8.0.1.dev-37dd5686`) |
| Lisans | **LGPL-2.1 + Open CASCADE İstisnası 1.0** (OCCT'nin kendi lisansı) |
| Lisans metni | LGPL-2.1: [`third_party/LGPL-2.1.txt`](third_party/LGPL-2.1.txt) (GNU'nun metni, değiştirilmemiş). İstisna metni OCCT kaynağında `OCCT_LGPL_EXCEPTION.txt`; resmî sayfa: <https://dev.opencascade.org/resources/licensing> |
| Kaynak kod | <https://github.com/Open-Cascade-SAS/OCCT> (sürüm sayfası: <https://dev.opencascade.org/release>) |
| Değişiklik | Yok: OCCT, `cascadio` tekerleğindeki hazır ikili olarak kullanılır; Hub koduna statik bağlanmaz |

**Nasıl gelir:** `cascadio==0.1.1` (aşağıda) OCCT'yi ayrı paylaşımlı kitaplıklar olarak getirir (Windows'ta
`cascadio.libs/*.dll`, `.exe` içine aynen gömülür). Hub bunları **ayrı bir alt süreçte** çağırır
(`hub_core/step_convert.py`); Hub süreci OCCT'yi yüklemez.

**LGPL gereği, kendi OCCT sürümünü kullanma hakkı:** kaynaktan çalıştırırsan (`python -m control_hub`)
`pip install` ile kurduğun `cascadio`'yu kendi derlediğin OCCT'li bir sürümle değiştirebilirsin. Hub yalnız
`cascadio==0.1.1` sürümünü kabul eder (alt süreç başka sürümü reddeder; neden: birim tuzağı, aşağıda).

## cascadio 0.1.1

| | |
|---|---|
| Ne için | OCCT'yi Python'dan çağırır (`to_glb_bytes`) |
| Sürüm | `cascadio==0.1.1` (**sabit**: `requirements-step.txt`) |
| Lisans | MIT — Telif © 2024 Michael Dawson-Haggerty. Tam metin tekerleğin `dist-info/licenses` klasöründe; `.exe`'ye `copy_metadata` ile gömülür |
| Kaynak | <https://github.com/trimesh/cascadio> · <https://pypi.org/project/cascadio/0.1.1/> |

**Birim tuzağı (neden sürüm sabit):** cascadio 0.1.1, dosyanın bildirdiği birimden bağımsız olarak STEP için
**metre**, IGES için **milimetre** çıktı verir. Hub bunu `GLB_TO_MM` ile sabitler ve `tests/test_step_convert.py`
her sürümde üç küple (mm / inç / m) sınar; sürüm değişirse test kırılır, elle güncellemeden yükseltilemez.
