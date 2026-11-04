# Jog çekirdeği (J1)

ROS'suz, Supervisor'dan bağımsız hesap katmanı: **ölçülen eklem vektörü + istenen hız → bir sonraki eklem
komutu önerisi**. Hiçbir şeyi kendisi hareket ettirmez; öneriyi Robot API/controller (J2) mevcut yoldan
(Supervisor, ACİL DUR, hız tavanı) yollar. **Bu PR'daki hiçbir şey gerçek robotta ya da Gazebo/MoveIt'te
çalıştırılmadı** (bkz. "Denenmeyenler").

Modüller (`src/robot_core/robot_core/`): `se3.py` (dönüşler + W-P-R), `kinematics.py` (FK/Jacobian, robot
profilinden), `frames.py` (3 noktadan çerçeve + `FrameStore`), `jog.py` (`JogEngine` arayüzü + `DlsJogEngine`).
Testler: `tests/test_se3.py`, `test_frames.py`, `test_jog.py`.

## 1. Yönelim sözleşmesi (tek dönüştürücü: `se3.py`)
İçeride kuaterniyon (**xyzw**, `w ≥ 0` kanonik) ve matris; API'de SI (m, rad). Ekranda **FANUC W-P-R**, derece:
**sabit eksen XYZ**, `R = Rz(R) · Ry(P) · Rx(W)` (önce sabit X etrafında W, sonra sabit Y etrafında P, sonra
sabit Z etrafında R) — URDF `rpy` ile aynı (roll = W, pitch = P, yaw = R).
- `matrix_from_wpr` / `wpr_from_matrix` (rad), ekran için `display_wpr` / `matrix_from_wpr_display` (derece).
- **P ≈ ±90° (gimbal):** W ve R ayrışmaz. Çıkarımda R = 0 verilir ve dönüş W'ye yazılır (matris gidiş-dönüşü
  yine tam). `display_wpr` `|P|`, ±90°'ye `GIMBAL_WARN_DEG` (öneri: 10°) ya da daha yakınsa **uyarı metni** döndürür;
  hareket etkilenmez, yalnız gösterim belirsizdir.
- **Jog'da Euler hızı yoktur.** Dönme jog'u, seçili çerçevenin eksenleri cinsinden **açısal hız vektörü**dür ve
  **TCP noktası etrafında** döner (TCP yerinde kalır).

## 2. Çerçeveler (`frames.py`)
| Çerçeve | Anlam |
|---|---|
| `World` | URDF `world` linki. Profilde montaj dönüşümü yok: bugün taban çerçevesiyle aynıdır. |
| `Tool` | TCP çerçevesi = **son bağlantının (link_6/flanş) çerçevesi**, **geçici**: profile TCP alanı J4'te gelecek. Her tick TCP'yi izler. |
| `User` | 3 noktadan öğretilir, `FrameStore`'da durur. |

**3 nokta:** orijin `O`, +X üzerindeki `Px`, XY düzleminden (+Y tarafı) `Pv`. Noktalar **base çerçevesinde TCP
konumudur** (eklem açısı değil). `x = (Px−O)/|Px−O|`, `z = x × (Pv−O)` birim, `y = z × x`.
- **Reddedilir:** `|Px−O| < min_x_distance`; `sin(açı(x, Pv−O)) < min_sin`; sonlu olmayan/|koordinat| > 5 m (mm yazılmış?).
- **Uyarı** ("düşük doğruluk"): `sin < warn_sin`.
- Üretilen matris için `RᵀR = I`, `det = +1` doğrulanır.
- Ad: harfle başlar, harf/rakam/`_`, ≤ 32 karakter, **benzersiz** (büyük/küçük harf duyarsız), `World`/`Tool` ayrılmış. **En çok 10 çerçeve.**

**`FrameStore(path)`**: yol parametredir (veri dizininde `frames.yaml`; repoda yalnızca
`src/robot_core/config/frames.example.yaml` ve `frames.schema.json`). Dosya: `frames_version: 1` + çerçeve
listesi (üç nokta = kaynak, türetilmiş poz). **Atomik yazma:** aynı dizinde geçici dosya → `fsync` → `os.replace`
(yazma yarıda kesilirse eski dosya sağlam kalır, geçici dosya silinir). **Yüklemede poz noktalardan yeniden
hesaplanır** ve kayıtlıyla uyuşmalı (elle bozulmuş dosya reddedilir); sürüm/bilinmeyen anahtar/tekrarlı ad/10'dan
fazla çerçeve de reddedilir. Hata mesajı dosya adını söyler. `resolve_rotation(ad, store)` jog çerçevesinin
dönüşünü verir (`Tool` için `None` = her tick TCP'den).

## 3. Jog motoru (`jog.py`)
`JogEngine` arayüzü: `set_frame(R | None)`, `reset()`, `begin_increment(q, index, miktar)`, `step(q, twist, dt) →
JogStep`. `twist = [vx vy vz wx wy wz]` seçili çerçeve eksenlerinde (m/s, rad/s). Çağıran her tick **ölçülen q**'yu
verir; `JogStep.q_next = q + dq` yollanacak komuttur, `status`/`messages` kullanıcıya gösterilecek nedendir.

Her tick (`DlsJogEngine`):
1. **Hız tavanı:** büyüklükler `max_* × etkin ölçek` ile kırpılır; etkin ölçek = `min(speed_scale,
   SafetyConfig.max_velocity_scale)` (mevcut hız tavanı aynen geçerli).
2. **Tekillik:** Jacobian'ın `σ_min`'i. `σ_min < sigma_stop` → o yönde **jog durur**, "tekillik" mesajı. `sigma_stop…sigma_slow`
   arasında hız yumuşakça (smoothstep) sıfıra iner ve sönüm `λ = damping_max·(1 − (σ/σ_slow)²)` büyür → `dq`'de sıçrama olmaz.
3. **DLS:** `dq = V·diag(s/(s²+λ²))·Uᵀ·(v + K·e)` (SVD). `e`, **içeride entegre edilen Kartezyen hedef poz** ile ölçülen poz
   arasındaki hatadır (drift birikmez). Hedef, tick sonunda **gerçekten verilen** hızla (kırpmalardan sonra) ilerletilir.
   Komut yokken hedef bırakılır (başka bir hareketle robotu geri çekmez). Hata `max_position_error/max_angle_error`'u aşarsa jog durur (`tracking`).
4. **Eklem hız kırpması:** adım, `profil hız limiti × ölçek`'i aşıyorsa **tüm adım** orantılı küçültülür (yön korunur).
5. **Güvenli kutu (ihlalden ÖNCE):** sonraki TCP konumu **FK ile hesaplanır**; kutu duvarına (marj `box_margin`) dışarı doğru
   giden eksen bileşeni sıfırlanır (**duvar boyunca kayma**), yetmezse adım reddedilir (`box_stop`, hareket yok).
   **Kutu içindeki TCP kutudan asla çıkmaz.** Kutu *dışındaki* TCP yalnız ihlal edilen eksenlerde kutuya doğru gidebilir
   (dik duran home pozu z = 1.24 m ile 0.7 m tavanın üstündedir).
6. **Eklem limiti:** limite `joint_limit_margin` kala daha dışarı iten adım reddedilir (`joint_limit`, eklem adıyla).

**Artımlı mod** (`begin_increment`): X/Y/Z (m) ya da W/P/R eksenleri **etrafında** dönme (rad), **aynı `step`** üzerinden;
son tick hedefe tam oturur, bitince `done`, ilerlenemezse (duvar, tekillik, limit) `aborted`/ilgili durum ve mesajla biter.
`Tool` çerçevesinde eksenler artım başında sabitlenir (düz çizgi). UI 0.1/1/10 mm ve °'yi SI'ya çevirip verir.

### Ayarlanacak sayılar — hepsi ÖNERİ, sabit değil
Simülasyonda ayarlanmak üzere yapılandırma değeridir (`JogConfig`, `FrameLimits`, `se3.GIMBAL_WARN_DEG`); varsayılanlar dar tarafta.

| Değer | Öneri | Nerede |
|---|---|---|
| X noktası mesafesi | ≥ 20 mm | `FrameLimits.min_x_distance` |
| Çerçeve reddi / düşük doğruluk | sin < 0.17 (~10°) / < 0.5 | `FrameLimits.min_sin / warn_sin` |
| P gimbal uyarısı | ±90°'ye 10° | `GIMBAL_WARN_DEG` |
| Jog hızı | %20 (`0.1 m/s`, `0.5 rad/s` tepe × 0.2) | `JogConfig.speed_scale / max_*_speed` |
| Tekillik eşikleri (karışık birim) | `σ_stop` 0.02, `σ_slow` 0.06, `λ_max` 0.05 | `JogConfig` |
| Eklem/kutu marjı | 0.02 rad / 5 mm | `joint_limit_margin / box_margin` |
| Geri besleme / izleme sınırı | 5 1/s; 10 mm, 0.1 rad | `feedback_gain / max_*_error` |

## 4. Güvenlik yolu
Tek yol: jog çıktısı **öneridir**; J2 onu `GuardedMotion`/Supervisor üzerinden yollar, ACİL DUR kilidi ve hız tavanı
değişmez. Bu modülün çalışma zamanı self-collision denetimi **yoktur** (v2). `SafeZone` yalnız TCP konumunu sınırlar.

## 5. Bilinen sınırlar
- Tekillikte Kartezyen jog durur ve kurtarmaz; çıkmak için eklem jog'u gerekir. **Dik duran home pozu (q = 0) bir bilek
  tekilliğidir** (J4 ve J6 ekseni üst üste): oradan Kartezyen jog "tekillik" der.
- `σ_min` karışık birimlidir (m/rad ve rad/rad); eşikler bu robotun Jacobian'ına göre öneridir, başka kol için yeniden ayarlanır.
- Güvenli kutu bugünkü robotun erişimine göre küçüktür (kutu içindeki başlangıç konfigürasyonları dardır).
- Geri besleme yalnız hedef poz hatasını düzeltir; kinematik kalibrasyon hatasını (gerçek robot ≠ URDF) bilmez.

## 6. Denenmeyenler
- **Gazebo/MoveIt/gerçek robotta hiçbir şey denenmedi**; testler mükemmel (ya da sentetik gürültülü) bir eklem
  oturucusuyla kapalı döngüdür. Gerçek controller gecikmesi, titreşim, ölçüm gürültüsü, jog tuşu bırakma davranışı denenmedi.
- Jog çağrı aralığı (`dt`) testlerde 10–20 ms; farklı/değişken `dt` ve kaçırılan tick'ler için kalite ölçülmedi.
- Eşikler (σ, marjlar, hız) ve `Tool` = flanş varsayımı simülasyonda ayarlanmadı; TCP ofseti (J4) yok.
- Robot API, controller, Hub, UI entegrasyonu ve `FrameStore`'un çok süreçli (iki yazar) kullanımı yok; kilit yalnız süreç içidir.
- `FrameStore` yalnız POSIX/Linux'ta denendi; Windows'ta `os.replace` aynı dizinde atomiktir ama burada çalıştırılmadı.
- Self-collision, hız/ivme üst sınırı dışındaki dinamik etkiler (tork), kutu dışındaki başlangıçtan çıkış stratejileri kapsam dışı.
