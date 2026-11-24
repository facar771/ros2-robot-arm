# Three.js (yerel kopya, CDN YOK)

* Sürüm: **0.187.0** (npm paketi `three@0.187.0`, `build/` içindeki resmi dosyalar, DEĞİŞTİRİLMEDEN).
* Lisans: MIT (`LICENSE`).
* SHA-256:
  * `three.module.js`  `104f56145899722094556490d787aae0617cc2fc3382f31df02d724ea1aee341`
  * `three.core.js`    `613e370f76638fdef83926642bf256dc9615684922de1ce1f63a8583e5d78578`
* Neden yerel: CSP `default-src 'self'` bozulmaz; Hub internetsiz atölyede de çalışır.
* Yükleme: yalnız `import('/vendor/three/three.module.js')` (ES modülü, aynı kaynak). `three.module.js`
  `./three.core.js`'i kendisi içe aktarır; ikisi birlikte durmalı.
* Yenileme: `npm pack three@<sürüm>`; `package/build/three.module.js`, `three.core.js`, `LICENSE` kopyala,
  SHA-256'ları ve sürümü burada güncelle. Başka dosya (örnekler, yükleyiciler) EKLENMEZ, **tek istisna** aşağıdaki
  `TransformControls.js` (S2, gizmo): STL ayrıştırıcı ve yörünge kontrolü `scene3d.js` içindedir.

## TransformControls.js (S2: 3B taşı/döndür gizmosu)

* Kaynak: `three@0.187.0` npm paketi, `package/examples/jsm/controls/TransformControls.js` (MIT, yukarıdaki `LICENSE`).
  Sürüm, `three.module.js` ile AYNI olmalıdır (ikisi birlikte yenilenir).
* **Tek satır değişti**: `} from 'three';` → `} from './three.module.js';` (CSP `script-src 'self'` altında import map
  kullanılamaz; çıplak `'three'` adı çözülmez). Başka hiçbir satır değiştirilmedi.
* SHA-256:
  * özgün (npm'deki)   `151befe25bb0d68626f9a6b033625b7b0cf6848e39ba71623b7bed8e3df565d6`
  * burada (yamalı)    `6c099bd9080a57ba70b0a30ecf6e6b313f13000bf2c8ecfbaea6e3ec539d065d`
* Yenileme: `npm pack three@<sürüm>`; dosyayı kopyala, yukarıdaki tek satırı yamala, iki SHA-256'yı güncelle
  (`tests/test_parts_page.py::test_transform_controls_is_pinned` yamanın tek satır olduğunu da denetler).
* Yükleme: yalnızca `scene3d.js` (ES modülü) ilk gizmo isteğinde `import('/vendor/three/TransformControls.js')` ile
  yükler; gizmo kullanılmazsa hiç indirilmez. Boyut: ~52 KB (`.exe` boyutuna etkisi ihmal edilebilir).
