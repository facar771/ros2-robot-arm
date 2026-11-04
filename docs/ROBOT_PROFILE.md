# Robot profili

Robotun tüm fiziksel tanımı **tek bir YAML**'dadır:
`src/robot_description/profile/six_axis_robot.yaml` (şema v2, yorumlu). Planlayıcının okuduğu dosyalar
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
| `motors` (**ayrı**) | hiçbir şey: URDF/MoveIt'e girmez; yalnız doğrulanır (donanım köprüsü sonra) |

**Elle kalanlar:** xacro sarmalayıcı (`six_axis_robot.urdf.xacro`: `world`, `masa`, Gazebo eklentisi),
`kinematics.yaml`, `pilz_cartesian_limits.yaml`, launch ve RViz dosyaları, hardware.profile ve firmware.

## Şema özeti
- **`links.<ad>.length`** (m): hem geometriyi (silindir boyu / kutu z, `length/2`'de ortalı) hem sonraki
  eklemin ofsetini sürer. Bir sayıyı değiştir, link, çarpışma şekli ve sonrası birlikte kayar; atalet,
  `inertia` yazılmadıysa kütle + şekilden türetilir (bugünkü URDF'in kullandığı katı-cisim formülü).
  Taban gibi ortalı geometri için `shape.length` / 3'lü `size` / `origin` açıkça yazılır.
- **`joints.<ad>`**: `type: revolute`, `parent`/`child`, birim `axis`, `limits.position` (fiziksel limit),
  isteğe bağlı `velocity`/`acceleration`/`effort`, isteğe bağlı `offset: [dx, dy, dz]`. Eklem
  `(0, 0, üst linkin length'i) + offset` konumundadır.
- **`moveit`**: grup, adlandırılmış pozlar, başlangıç pozu, hız/ivme ölçeği, `collision.allow_extra`.
- **`safety`**: `box` (omuz merkezli erişim küresinin içinde olmalı), `max_velocity_scale`.
- **`motors`** (eklem başına, ayrı bölüm): `type: step`, `steps_per_rev`, `microstep`, `direction` (±1),
  `zero_offset`, `gear_ratio`, isteğe bağlı `encoder: none|incremental|absolute` (varsayılan `none`),
  `limits` (motorun ulaşabildiği konum/hız, **eklem birimlerinde**), isteğe bağlı `margin`.
  `motor_pozisyonu = direction · gear_ratio · (eklem − zero_offset)` (rad, motor mili);
  mikroadım = `motor_rad / 2π · steps_per_rev · microstep`.

## Doğrulayıcının denetledikleri (`robot_core/profile.py`; hata mesajı YAML'daki yeri söyler)
- **Motor ⊇ eklem:** eklem konum limiti, motor limiti − marj içinde olmalı; eklem hızı, motor hızı − marj
  altında olmalı. **Yön** yalnız tam sayı +1/−1; `gear_ratio > 0`; mikroadım 1–256; `type` yalnız `step`.
- **Birim sağduyusu:** konum ±4π rad üstü (derece yazılmış?), uzunluk > 5 m (mm yazılmış?) ya da < 1 mm,
  hız > 50 rad/s, kütle ≤ 0 → hata. Sonlu olmayan sayı → hata.
- **Kapsam:** yalnız seri zincir + döner eklem; prizmatik, mimic ve dallanma net bir mesajla reddedilir.
- Zincir sırası, birim eksen, poz/başlangıç değerleri limit içinde, `allow_extra` gerekçeli ve komşu olmayan
  çift, güvenli kutu zemin üstünde ve erişim içinde.

## "Daha dar" kuralları (varsayılan her zaman güvenli yön)
Güvenli varsayılanı olmayan değerler **zorunludur** (konum limiti, eksen, uzunluk, kütle, motor limitleri).
Yazılmazsa: hız/ivme 0.5, efor 10, `max_velocity_scale` 0.5, motor marjı 0.05 rad ve 0.05 rad/s;
`safety.box` yoksa omuz merkezli, yarıçapı erişimin yarısı olan kürenin içindeki kutu (z ≥ 0.02,
x ≥ 0.05). Çarpışma: yalnız parent–child çiftleri kapalı; başkası ancak gerekçeyle `allow_extra`'da.

## Kanıt: üretilen = el yazması robot
`tests/golden/today_robot.json`, el yazması dosyalardan (üretici gelmeden önce) alınmış anlık görüntüdür;
`test_generated_robot_equals_the_original_hand_written_robot` üretilen dosyalardan aynısını çıkarıp
karşılaştırır: eklemler/limitler/ofsetler/eksenler, link geometrisi + atalet, **11 konfigürasyonda ileri
kinematik** (URDF'ten bağımsız hesaplanır), **çarpışma matrisi**, pozlar, güvenli kutu, hız/ivme,
başlangıç pozu, kontrolcü eklem listeleri. Bu golden dosyayı yalnızca robotu bilerek değiştirirken güncelle.

## Bu aşamada olmayanlar
Paralel/kapalı çevrim, mimic, prizmatik eklem; IK çözücü seçimi; launch/RViz üretimi; `hardware.profile` ve
firmware; profilin çalışma zamanında okunması (`profile/` CMake ile kurulmuyor, ilk tüketici gelince);
UI'dan düzenleme.
