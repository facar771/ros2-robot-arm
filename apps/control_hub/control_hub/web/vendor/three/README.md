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
  SHA-256'ları ve sürümü burada güncelle. Başka dosya (örnekler, yükleyiciler) EKLENMEZ:
  STL ayrıştırıcı ve yörünge kontrolü `scene3d.js` içindedir.
