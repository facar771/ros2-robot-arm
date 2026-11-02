# Paket (zip) yükleme — TASARIM BELGESİ (uygulanmadı)

**Durum:** Bu belge yalnızca tasarımdır. PR-V3 zip yüklemeyi **uygulamaz**: `plugins/` altına yalnızca bu
depodan gelen, gözden geçirilmiş paketler konur ve `PluginManager` onları olduğu gibi çalıştırır
("güvenilir kod"). Üçüncü tarafın yazdığı bir paketi çalıştırmak **bugün desteklenmez**; aşağıdaki
önlemler olmadan denenmemelidir.

## Neden ayrı bir tasarım gerekiyor
V3'teki izin sistemi (`camera:read`, `robot:jobs`, her zaman açık `robot:stop`) paketin eline verilen
*API nesneleriyle* uygulanır; işletim sistemi sınırı değildir. Kötü niyetli bir paket kendi alt sürecinde
dosya okuyabilir, ağa çıkabilir, `127.0.0.1:8080` Robot API'sine doğrudan HTTP atıp `PluginContext`'i
tamamen atlayabilir. Robot API'si kimlik doğrulamasız ve yalnızca loopback'e güvenir. Yani yüklenen paket =
bu makinede kod çalıştırma.

## Tehdit modeli (kısa)
1. Paket robotu güvenli kutu/hız tavanı dışında sürmeye çalışır → Robot API zaten reddeder, ama paket API'yi
   doğrudan çağırabilirse acil dur kilidini `reset` ile açmaya da çalışabilir.
2. Paket dosya sistemini okur/yazar (anahtarlar, veritabanı), ağa veri sızdırır.
3. Paket CPU/bellek/disk tüketerek robotun kontrol sürecini aç bırakır.
4. Paket güncellemesi sırasında farklı bir kodun, onaylanan paketin yerine geçmesi.
5. Paket acil durdan kaçınır (sinyalleri yok sayar, kendini yeniden başlatır).

## Önerilen mimari (uygulanırsa)
**Yükleme hattı (4 adım, hepsi gerekli):**
1. **Karantina.** Zip, `plugins/` dışında ayrı bir dizine iner; boyut sınırı (ör. 5 MB), dosya sayısı sınırı,
   yol kaçışı (`..`, mutlak yol, sembolik bağ) ve sıkıştırma bombası denetimi; yalnızca `package.yaml` +
   `.py`/`.yaml`/`.json` dosyalarına izin; yürütülebilir bit yok.
2. **Özet beyaz listesi.** Dosya ağacının SHA-256 özeti (sıralı dosya adı + içerik) hesaplanır. Çalıştırma
   yalnızca *kullanıcının onayladığı* özetlerle yapılır (`plugins/TRUSTED.json`: ad → sürüm → özet).
   Paket değişirse özet değişir, onay düşer. Depodaki paketler için özet CI'da üretilir.
3. **Kullanıcı onayı.** Hub, manifestten okunan **izinleri, araç listesini ve özeti** gösterir; onay açıkça
   verilmeden hiçbir şey çalışmaz. `robot:jobs` isteyen paket için uyarı ayrıca vurgulanır.
4. **İmza (isteğe bağlı, sonra).** Yayıncı anahtarıyla imza; iptal listesi (revocation).

**Çalıştırma sınırı (ayrı konteyner):**
- Her paket **kendi konteynerinde**: salt-okunur kök dosya sistemi, yalnızca paketin dizini ve boş bir
  `tmpfs`; ağ yok (`--network none`); root olmayan kullanıcı; `no-new-privileges`; tüm yetenekler düşürülmüş;
  CPU / bellek / süreç sayısı sınırı (`cgroup`).
- Konteynerin dışarıyla tek bağlantısı bir **Unix soketidir**; karşısında yönetici tarafında bir *vekil*
  (proxy) durur. Vekil yalnızca izin verilen çağrıları iletir: kare okuma (`camera:read`), iş gönderme /
  iptal / durum (`robot:jobs`) ve **her zaman** acil dur. Robot API'sinin adresi konteynerden *görünmez*;
  `reset` vekilde yoktur. Böylece `PluginContext` atlanamaz.
- Vekil, plugin başına hız/oran sınırı uygular (ör. en çok 5 iş/s, 10 kare/s) ve her çağrıyı denetim
  günlüğüne yazar (paket, özet, çağrı, sonuç).
- **Acil dur:** yönetici konteyneri `SIGKILL` ile sonlandırır (paketin işbirliğine bağlı değil); Robot API'nin
  `/api/stop`'u ayrıca çağrılır. Kilit açılana kadar konteyner yeniden başlatılmaz.

## Açık sorular
- Kare akışı yüksek hızda (320×240 RGB-D ≈ 0.5 MB/kare) soketten geçerken gecikme/bant genişliği.
- GPU/ONNX modelleri için konteynere ne verilir (model dosyası salt-okunur bağlanır, GPU yok).
- Paketler arası bağımlılık ve paylaşılan model dosyaları.
- Windows/WSL'de konteyner yoksa ne yapılır (şimdilik: yüklemeyi reddet).

## Bugün geçerli kural
`plugins/` dizinine yalnızca bu depoya PR ile giren paketler konur. Zip yükleme, uzak indirme ve rastgele
kod yükleme **yoktur**; `PluginManager` yalnızca `plugins_dir` altındaki doğrudan alt dizinleri okur
(sembolik bağlar ve `.`/`_` ile başlayanlar atlanır).
