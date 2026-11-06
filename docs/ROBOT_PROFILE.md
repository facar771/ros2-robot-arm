# Robot profili

Robotun tüm fiziksel tanımı **tek bir YAML**'dadır:
`src/robot_description/profile/six_axis_robot.yaml` (şema v3, yorumlu). Planlayıcının okuduğu dosyalar
ondan **üretilir**; bugünkü robot profilin ilk örneğidir ve üretilen dosyalar el yazması
dosyalarla birebir aynı robotu verir.

## Günlük kullanım
Profili düzenle → yeniden üret → test et. **WSL'de** (ya da `dev` kabuğunda, `/workspace` içinde):

```bash
cd ~/ros2-robot-arm/src/robot_core
python3 -m robot_core.profile_gen            # üretilen dosyaları yaz
python3 -m robot_core.profile_gen --check    # hepsi güncel mi? (değilse çıkış kodu 1)
```

Üretilen dosyalar commit'lidir ve başlıklarında "ÜRETİLDİ ... ELLE DÜZENLEME" yazar.
`pytest` (`test_profile_gen.py::test_generated_files_are_up_to_date`) profil ile dosyalar ayrışırsa
kırılır; CI `core` işi bunu zaten koşar.

| Profil bölümü | Üretilen dosya |
|---|---|
| `links`, `joints`, `materials` | `robot_description/urdf/robot.urdf` |
| `joints` | `robot_moveit_config/config/six_axis_robot.ros2_control.xacro`, `joint_limits.yaml`, `ros2_controllers.yaml`, `moveit_controllers.yaml` |
| `moveit` (+ `joints`, `mount`) | `six_axis_robot.srdf` (grup, pozlar, sanal eklem, çarpışma çiftleri), `initial_positions.yaml` |
| `safety` | `safe_zone.yaml` |
| `tcp` | hiçbir dosya: yalnız jog zinciri (`robot_core.kinematics.Chain`) okur; URDF/MoveIt'e girmez |
| `hardware.profile.yaml` (**ayrı dosya**) | `robot_description/profile/generated/robot_limits.h` (firmware C başlığı); URDF/MoveIt'e **girmez** |

**Elle kalanlar:** xacro sarmalayıcı (`six_axis_robot.urdf.xacro`: `world`, `masa`, Gazebo eklentisi),
`kinematics.yaml`, `pilz_cartesian_limits.yaml`, launch ve RViz dosyaları. Firmware kaynak kodu ve ROS köprü düğümü (B2/B3) sonraki PR'lar.

## Şema özeti
- **`links.<ad>.length`** (m): hem geometriyi (silindir boyu / kutu z, `length/2`'de ortalı) hem sonraki
  eklemin ofsetini sürer. Bir sayıyı değiştir, link, çarpışma şekli ve sonrası birlikte kayar; atalet,
  `inertia` yazılmadıysa kütle + şekilden türetilir (bugünkü URDF'in kullandığı katı-cisim formülü).
  Taban gibi ortalı geometri için `shape.length` / 3'lü `size` / `origin` açıkça yazılır.
- **`joints.<ad>`**: `type: revolute`, `parent`/`child`, birim `axis`, `limits.position` (fiziksel limit),
  isteğe bağlı `velocity`/`acceleration`/`effort`, isteğe bağlı `offset: [dx, dy, dz]`. Eklem
  `(0, 0, üst linkin length'i) + offset` konumundadır.
- **`moveit`**: grup, adlandırılmış pozlar, başlangıç pozu, hız/ivme ölçeği, `collision.allow_extra`.
  Pozlar: `home`, `cnc`, `pc`, `dik` ve **`ready`** `[0, −0.1, 1.65, 0, 1.2, 0]` (jog için başlangıç: güvenli kutunun içinde,
  σ_min ≈ 0.23, her eklem limitinden ≥ 0.35 rad uzakta; testle denetlenir). SRDF'e `group_state` olarak üretilir.
- **`safety`**: `box` (omuz merkezli erişim küresinin içinde olmalı), `max_velocity_scale`.
- **`tcp`** (isteğe bağlı): `offset: [x, y, z]` (m) ve `wpr: [W, P, R]` (rad, `R = Rz(R)·Ry(P)·Rx(W)`, FANUC W-P-R,
  `robot_core.se3`), **araç linkinin (son link, flanş) çerçevesine göre**. Yazılmazsa TCP = flanşın kendisi
  (en kısa/dar seçim). Doğrulama: sonlu sayı, |offset| ≤ 1 m (mm yazılmış mı?), |açı| ≤ 4π (derece yazılmış mı?),
  bilinmeyen anahtar → hata. Etkisi: `Chain` (jog FK/Jacobian, güvenli kutu denetimi, `Tool` çerçevesi) TCP'yi kullanır.
  **Türetilmiş güvenli kutu TCP'den etkilenmez** (varsayılan hep dar); açıkça yazılan kutu doğrulanırken erişim,
  araç linkinden uzun çıkan ofset kadar genişler. **MoveIt/poz hedefleri hâlâ link_6 (flanş) çerçevesine gider, TCP'ye değil**;
  URDF'e TCP linki eklenmedi.
- **`tools`** + **`active_tool`** (Q1, isteğe bağlı): değiştirilebilir takımlar (`robot_core/profile.py`: `Tool`, `apply_tool`). Yoksa ya da
  `active_tool` yazılmadıysa **takım yok = flanş** (en dar seçim) ve üretilen dosyalar takımsız robotla **bayt bayt aynıdır**.
  Her takım (`tools.<ad>`): `tcp: {offset, wpr}` (flanş çerçevesine göre), `mass` (kg, **zorunlu**, ≤ 20), `com` ve `inertia`
  (isteğe bağlı; yoksa geometri sınırlarının merkezi ve o sınırların katı kutu ataleti), `collision` (**zorunlu**, 1–8 şekil:
  `cylinder`/`box`/`sphere`, flanş çerçevesinde `origin`+`wpr`), `box_margin`, `material`, `sensors`.
  - **Sensörler:** `camera` (`name`, `pose: {offset, wpr}` flanşa göre, `resolution: [w, h]`, `fov` = YATAY açı, rad, z = optik eksen;
    `CameraSensor.intrinsics()` kare piksel/bozulmasız kamera_info sayılarını türetir), `laser` (`name`, `origin`, birim `axis`, flanş çerçevesinde).
    Sensörler şimdilik yalnız profilde doğrulanır/okunur; **URDF/SDF'e yazılmazlar** (URDF dallanırsa `RobotConfig` seri zinciri reddeder; Gazebo kamera/lazer = P1).
  - **Tek geçiş, birlikte güncellenir:** `apply_tool(profile, ad | None)` (ya da YAML `active_tool` / `load_profile(yol, tool=ad)`) TCP'yi
    (`Chain`/jog/`Tool` çerçevesi), güvenli kutuyu (her duvardan `box_margin` kadar daralır) ve çarpışma geometrisini+kütleyi (üretilen URDF'te
    flanşa sabit `tool_<ad>` linki, SRDF'te `tip_link`–`tool_<ad>` komşu çifti) beraber değiştirir. Saf fonksiyondur; çalışma zamanında takım
    değiştirme (robot durgunken, ACİL DUR) Q3'tür.
  - **Daha dar varsayılan:** `box_margin` yazılmazsa takım geometrisinin TCP çevresindeki en uzak noktası (sınırlayıcı küre). Bu, takım ucunun
    zemine değmesini engeller; bilerek küçültmek için `box_margin` açıkça yazılır. Kutu payla boşalıyorsa hata verir.
  - Üst düzey `tcp:` ile `active_tool` birlikte kullanılamaz (TCP takımdan gelir; ikisi de sıfırsa serbest). Açık kutunun erişim denetimi
    takımsız (flanş/`tcp:`) erişimle yapılır, takım ofseti erişimi genişletmez.
  - **`tip_link`:** takım aktifken URDF'in son linki `tool_<ad>` olur (çerçevesi flanşla aynı, sıfır ofsetli sabit eklem); MoveIt poz hedefleri
    yine flanş çerçevesidir, TCP değil.
- Motor/sürücü/encoder **bu dosyada yok** (v3): `hardware.profile.yaml` (aşağıda, "Donanım profili").

## Doğrulayıcının denetledikleri (`robot_core/profile.py`; hata mesajı YAML'daki yeri söyler)
- **Birim sağduyusu:** konum ±4π rad üstü (derece yazılmış?), uzunluk > 5 m (mm yazılmış?) ya da < 1 mm,
  hız > 50 rad/s, kütle ≤ 0 → hata. Sonlu olmayan sayı → hata.
- **Kapsam:** yalnız seri zincir + döner eklem; prizmatik, mimic ve dallanma net bir mesajla reddedilir.
- Zincir sırası, birim eksen, poz/başlangıç değerleri limit içinde, `allow_extra` gerekçeli ve komşu olmayan
  çift, güvenli kutu zemin üstünde ve erişim içinde.

## "Daha dar" kuralları (varsayılan her zaman güvenli yön)
Güvenli varsayılanı olmayan değerler **zorunludur** (konum limiti, eksen, uzunluk, kütle).
Yazılmazsa: hız/ivme 0.5, efor 10, `max_velocity_scale` 0.5;
`safety.box` yoksa omuz merkezli, yarıçapı erişimin yarısı olan kürenin içindeki kutu (z ≥ 0.02,
x ≥ 0.05). Çarpışma: yalnız parent–child çiftleri kapalı; başkası ancak gerekçeyle `allow_extra`'da.

## Kanıt: üretilen = el yazması robot
`tests/golden/today_robot.json`, el yazması dosyalardan (üretici gelmeden önce) alınmış anlık görüntüdür;
`test_generated_robot_equals_the_original_hand_written_robot` üretilen dosyalardan aynısını çıkarıp
karşılaştırır: eklemler/limitler/ofsetler/eksenler, link geometrisi + atalet, **11 konfigürasyonda ileri
kinematik** (URDF'ten bağımsız hesaplanır), **çarpışma matrisi**, pozlar, güvenli kutu, hız/ivme,
başlangıç pozu, kontrolcü eklem listeleri. Bu golden dosyayı yalnızca robotu bilerek değiştirirken güncelle.

## `view_model.json` (L9a): robotun salt-okunur görünüm modeli
`robot_description/profile/generated/view_model.json`, aynı üreticiyle profilden üretilir (`python3 -m robot_core.profile_gen`); **elle düzenlenmez**,
`test_generated_files_are_up_to_date` (CI'daki pytest) üretim = commit'i denetler. Hub'daki 3B/2B görünümün (L9b `GET /api/model`, L9c Canvas) tek kaynağıdır;
yalnız **çizmek** için gerekenleri taşır, komut/kimlik/yol içermez, mesh yoktur.

Şema (`view_model_version: 1`; sonraki sürümlerde yalnız alan eklenir): birimler m ve rad.
- `_generated` (metin), `view_model_version`, `robot`, `units {length, angle}`, `frames` (kural metni), `root` (taban linki; `world == base_link`, ofsetsiz sabit montaj).
- `joints[]` zincir sırasında: `name`, `parent`, `child`, `origin [x,y,z]` (üst link çerçevesinde), `axis [x,y,z]` (birim), `limit {lower, upper, velocity}`.
  FK: çocuk çerçeve = üst çerçeve · Öteleme(`origin`) · Dönme(`axis`, q); sıfır konfigürasyonda tüm çerçeveler hizalı. İstemci FK'si çekirdek FK'sıyla testle eşleşir.
- `links[]`: `name`, `joint` (onu hareket ettiren eklem; taban için `null`), `shape` = `{type: cylinder, radius, length}` ya da `{type: box, size: [x,y,z]}` (silindir ekseni ve kutu z'si link z'si boyunca),
  `origin` (şekil merkezi, link çerçevesinde), `material`, `color [r,g,b,a]`.
- `tcp {offset, wpr, frame}`: aktif TCP (W-P-R, `R = Rz(R)·Ry(P)·Rx(W)`) `frame` (flanş linki) çerçevesine göre.
- `tool`: `null` (takım yok = flanş) ya da `{name, link, parent, material, color, shapes[]}`; `shapes[]` = `link` ile aynı `shape` biçimi (+ `sphere {radius}`) ve `origin`, `wpr` (flanş çerçevesinde).
- `safe_box {frame: "world", x: [lo, hi], y, z}`: **etkin** kutu (aktif takımda payla daralmış).
- `named_poses {ad: [6 eklem değeri]}` (ör. `ready`).
Sensörler, kütle/atalet ve çarpışma matrisi bilerek yok.

## Donanım profili (`hardware.profile.yaml`, `robot_core/hardware.py`)
Gerçek robotun motor/sürücü/encoder/homing bilgisi **ayrı dosyada**; yalnızca köprü ve firmware kullanır.
URDF'e ve MoveIt dosyalarına **girmez**: bir motorun yönünü ters çevirmek simülasyonu değiştiremez
(testle kanıtlı: üretilen robot dosyalarında `direction`, `gear_ratio`... hiç geçmez ve çıktıları aynı kalır).
Dosyadaki sayılar **eklem birimindedir** (rad, rad/s, rad/s²); doğrulayıcı dönüştürür. Ad ve sıra
robot profilindeki eklemlerle aynı olmalı. Kanal sözleşmesi: `docs/HARDWARE.md` (bu belge onu değiştirmez).

```
motor_rad = yön × dişli × (eklem − ofset)              (yön ±1, dişli = motor turu / eklem turu)
adım      = motor_rad / 2π × adım_per_devir × mikroadım  (motor sıfırından itibaren mikroadım)
```

| Alan (eksen başına) | Anlam |
|---|---|
| `motor: {type: step, steps_per_rev, model}` | step motor, tam adım/devir |
| `driver: {microstep, model}` | mikroadım 1–256 |
| `direction`, `zero_offset`, `gear_ratio` | yukarıdaki formül; `zero_offset` = sayaç 0 iken eklem açısı |
| `encoder: none\|incremental\|absolute` | isteğe bağlı, varsayılan `none` (açık döngü) |
| `limits: {position, velocity, acceleration}` | sürücünün ulaşabildiği değerler (eklem birimi) |
| `margin: {position, velocity}` | varsayılan **0.05 rad / 0.05 rad/s** (dar) |
| `homing: {method, direction, speed, position, backoff}` | `switch` \| `index` \| `absolute`; hız varsayılan 0.1 rad/s, geri çekilme 0.05 rad |

**Doğrulayıcı (hata mesajı YAML'daki yeri söyler):** eklem (URDF) konum limiti ⊆ motor limiti − marj; eklem
hızı ≤ motor hızı − marj; eklem ivmesi ≤ motor ivmesi; yön yalnız tam sayı ±1; `gear_ratio > 0`; birim
sağduyusu; homing konumu eklem limitleri içinde, homing hızı eklem/motor hızını aşmaz; adım değerleri tel
biçimine sığar (hedef `i32`, hız/ivme `u32`, en az 1 adım/sn). **Referans kuralı (HARDWARE.md §7):** encoder yoksa
`homing.method` yalnız `switch` olabilir (home/limit anahtarı ŞART); artımlı encoder: `switch` ya da `index`;
mutlak encoder: hepsi. Referanslanamayan eksen profilde yazılamaz.

### AÇIK DÖNGÜ UYARISI (encoder yoksa)
`encoder: none` olan eksende **adım kaybı algılanmaz**; konum, komut sayacından bir **kestirimdir**. Bu yüzden
her oturumda homing, düzenli yeniden homing, mekanik uç limit anahtarı, geniş tork/hız/ivme marjı şart.
Uyarı üç yerde otomatik çıkar: firmware başlığında (`!!! AÇIK DÖNGÜ UYARISI !!!` yorumu + `ROBOT_OPEN_LOOP_ANY`,
`ROBOT_OPEN_LOOP_MASK`, `ROBOT_OPEN_LOOP_WARNING`), `HardwareProfile.warnings` ve Hub okuma fonksiyonunda.
Bu, `HARDWARE.md §7`'deki `OPEN_LOOP_ANY` bayrağıyla aynı kuraldır.

### Firmware başlığı (C)
```
cd src/robot_core && python3 -m robot_core.hardware_gen [--check]     # WSL/Docker kabuğunda
```
Çıktı: `src/robot_description/profile/generated/robot_limits.h` (commit'li, "ÜRETİLDİ ... ELLE DÜZENLEME").
**Tüm değerler motor tarafı mikroadımdır** (yön, dişli, ofset işlenmiş; firmware yönü bir daha uygulamaz).
Eksen başına: `steps_per_rad`, `encoder` (protokoldeki `EncoderMode` ile aynı 0/1/2), homing yöntemi ve motor
işaretli arama yönü, `soft_min/max` (eklem/URDF limitleri → `CONFIG SOFT_MIN/MAX`), `hard_min/max` (motor
limitleri), `max_speed`, `max_accel`, `homing_speed`, `home_position`, `homing_backoff`. Sınırlar ve hızlar
**içe/aşağı yuvarlanır** (dar taraf). pytest: başlık güncel mi, C derleyicisiyle derlenip değerleri okunuyor mu.

### Hub için salt-okunur okuma
`robot_core.hardware.describe_hardware(path=None)` ROS'suz, JSON'a uygun bir sözlük döndürür (eksen tablosu,
döngü kipi, adım limitleri, homing, uyarılar); **hiçbir şey yazmaz, istisna fırlatmaz** (`{'ok': False,
'error': ...}`). Arayüzü Robot_4 yapar.

### Yer tutucu değerler
Bugünkü `hardware.profile.yaml` simüle edilen robot içindir: 200 adım/devir × 16, dişli 1, yön +1, limitler =
eklem limitleri, marj 0, encoder yok, homing konumu 0 rad. **Gerçek bir robot için gerçek sürücü verisi, marj ve
home anahtarı konumları yazılmadan kullanılmamalıdır.** Gerçek donanımda hiçbir şey denenmedi.

## Eksik bilgiyle çalışan profil: kalite etiketi ve olgunluk (J5, `robot_core/maturity.py`)
Robot, gerçek ölçüler olmadan yer tutucu sayılarla tanımlanıp simülasyonda çalışır; sayılar sonradan ölçülüp
doldurulur. Profil **ne kadarının gerçek olduğunu** söyler. Etiketler robot/donanım YAML'ına değil, yanındaki
**`quality.profile.yaml`** dosyasına yazılır (bu yüzden o iki dosya ve **üretilen her şey** bayt bayt aynı kalır).

**Etiket:** `measured` (ölçüldü) · `estimated` (tahmin) · `placeholder` (yer tutucu). Dosyada olmayan alan
`placeholder` sayılır (geriye uyumlu, dar seçim). `null` = hiçbir değer bilinmiyor (`unknown`, yer tutucudan
kötü). `{tag: measured, value: 0.35}` değeri mühürler: profildeki sayı sonradan değişirse etiket `stale` olur ve
`measured` sayılmaz. Bilinmeyen alan yolu ya da yanlış yazılmış etiket hatadır (sessiz yok sayılmaz).

**Olgunluk** (her çağrıda etiketlerden yeniden hesaplanır, bu yüzden `measured → estimated` düşürmek seviyeyi geri düşürür):

| Seviye | Koşul |
|---|---|
| 1 taslak | şema geçerli (profil okundu) ama poz/limit tutarsız ya da `home` pozu yok |
| 2 simülasyona hazır | poz ve limitler tutarlı; donanım bilgisi eksik olabilir |
| 3 donanıma hazır | kritik alanların **hepsi `measured`** ve statik tork denetimi geçti |

Kritik alanlar: `links.<link>.length` ve `.mass` (kütle tork denetiminin girdisi), `joints.<eklem>.limits.position`
ve `.limits.effort` (tork değeri), `axes.<eklem>.motor.type`, `motor.steps_per_rev`, `driver.microstep`,
`gear_ratio`, `limits.position` (motor), `homing.position` (home/limit anahtarı); `hobby_servo` ekseninde adım/dişli/homing
yerine `servo.pulse_range` (K6; donanım profilinde henüz saklanmıyor, o zamana kadar "eksik" görünür).
**Tork denetimi:** her eklem için, o eklemden sonraki tüm kütleler geometrinin izin verdiği en uzak noktada
(kol yatay, tam açık, eksen yönü ne olursa olsun) × g; `effort`'u aşarsa donanıma hazır değildir. Bilerek
fazla tahmin eder, eksik tahmin edemez.

```python
from robot_core.maturity import load_maturity, gate
m = load_maturity(PROFILE_PATH, HARDWARE_PATH, QUALITY_PATH)   # m.name, m.missing_fields, m.gaps
gate('sim', m).open      # simülasyona hazırsa True (eksik donanım bilgisi engellemez)
gate('real', m)          # .open, .reasons ("donanıma hazır değil" + eksik alanlar), .missing
```
**Gerçek kapı:** seviye 3 **ve** yürütmede yol boyunca kutu denetimi (F4b) şart. F4b bitene kadar kapı hiçbir
profille açılmaz (`maturity.F4B_DONE = False`; onu `True` yapmak F4b'yi bitiren PR'ın işidir). Kapıyı Robot API'ye /
`mode: real`'e bağlamak ve Hub sihirbazı bu PR'da **yoktur**.

## Bu aşamada olmayanlar
Takım kütüphanesi (Q2), Hub/Robot API'de takım seçimi ve çalışma zamanı değişimi (Q3), sensörlerin URDF/SDF/Gazebo'ya yazılması (P1), takım kütlesinin tork denetimine girmesi (K7);
Paralel/kapalı çevrim, mimic, prizmatik eklem; IK çözücü seçimi; launch/RViz üretimi; firmware kaynak kodu ve
ROS köprü düğümü (B2/B3), dönüşümün çalışma zamanında köprüye bağlanması; profilin çalışma zamanında okunması (`profile/` CMake ile kurulmuyor, ilk tüketici gelince);
UI'dan düzenleme; kapının API/`mode: real`'e bağlanması ve Hub profil sihirbazı, değişiklik günlüğü (J5 sonraki PR'lar).
