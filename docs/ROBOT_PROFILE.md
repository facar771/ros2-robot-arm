# Robot profili — tasarım notu (TASLAK: onay bekliyor, üretici kod yok)

**Amaç.** Robotun tüm fiziksel tanımı tek bir YAML'da olsun
(`src/robot_description/profile/six_axis_robot.yaml`); URDF/xacro, `joint_limits.yaml`, SRDF çarpışma
çiftleri ve güvenli kutu bundan **türetilsin**. Bugünkü robot profilin ilk örneğidir: üretilen
dosyalar bugünküyle aynı davranır, `selftest*` modları değişmeden geçer. UI'dan eksen yönü/limit/link
uzunluğu düzenleme ve ESP32 köprüsü sonraki adımlardır; bu not yalnızca şemayı ve kuralları sabitler.

## Bölümler (şemanın tamamı YAML'ın içinde, yorumlu)
| Bölüm | İçerik | Kim okuyacak |
|---|---|---|
| `links` | şekil (cylinder/box), geometri merkezi, kütle, atalet, renk | URDF |
| `joints` | tip, parent/child, `origin` (= linkler arası uzunluk/ofset), eksen vektörü, `limits`: konum [alt, üst], hız, ivme, efor | URDF, joint_limits.yaml, ros2_control |
| `moveit` | planlama grubu, adlandırılmış pozlar, başlangıç pozu, varsayılan hız/ivme ölçeği, `collision.allow_extra` | SRDF, joint_limits.yaml |
| `safety` | güvenli kutu, `max_velocity_scale` | `safe_zone.yaml` (`robot_core.safezone`) |
| `motors` (**ayrı**) | eksen başına `direction` (±1), `zero_offset`, `gear_ratio` | URDF/MoveIt'e **girmez**; yalnız donanım köprüsü (sonra) |

Motor formülü: `motor_pozisyonu = direction · gear_ratio · (eklem_pozisyonu − zero_offset)` (rad, motor
mili). Simüle robotun motoru yok: her eksen (1, 0, 1) birim eşleme.

## Üretici ne üretecek (onaydan sonra, 2. PR)
`robot.urdf`, `six_axis_robot.ros2_control.xacro`, `joint_limits.yaml`, `six_axis_robot.srdf` (grup,
pozlar, sanal eklem, çarpışma çiftleri), `safe_zone.yaml`, `initial_positions.yaml`. **Elle kalır:**
`six_axis_robot.urdf.xacro` (sarmalayıcı: `world`, `masa`), `ros2_controllers.yaml`,
`moveit_controllers.yaml` (mevcut test eklem listelerini korur), `kinematics.yaml`,
`pilz_cartesian_limits.yaml` (planlayıcı ayarı, robot tanımı değil). Üretilen dosyalar repoda
**commit'li** ve başlıklarında "ÜRETİLDİ" yazar; pytest "üretilen == commit'li" ve (geçiş için)
"üretilen == bugünkü dosya" kontrolünü yapar. ROS gerekmez, CI `core` işinde koşar, Docker'da üretim
adımı yoktur.

## "Daha dar" kuralları (türetmede varsayılan her zaman güvenli yönde)
- **Çarpışma:** yalnız parent–child çiftleri kapatılır (bugün de böyle); başka çift ancak
  `allow_extra`'da gerekçesiyle kapanır. Varsayılan: her çift kontrol edilir.
- **Eksik değer:** hız 0.5 rad/s, ivme 0.5 rad/s², `max_velocity_scale` 0.5. Bugünkü profil hepsini
  açıkça yazar (1.0, 1.0, 0.5), bu yüzden davranış değişmez.
- **`safety.box` yoksa:** omuz merkezli, yarıçapı 0.5·R (R = omuzdan uca düz kol uzunluğu) olan kürenin
  içine sığan eksen-hizalı kutu, x ≥ 0.05, z ≥ 0.02. Varsa ve bir köşesi R'den uzaksa üretici **hata** verir.
- **Çelişki:** iki kaynak çelişirse (ileride motor hız sınırı < eklem sınırı) etkin değer ikisinin
  küçüğüdür ve üretici uyarı basar.

## Doğrulama planı
Bu PR: `test_robot_profile_today.py`, profilin URDF/SRDF/joint_limits/safe_zone/initial_positions/
ros2_control/xacro ile **değer değer** (tam float eşitliği) aynı olduğunu kanıtlar. 2. PR: üretilen
dosyalar bugünküyle aynı; mevcut model-bütünlük, kendine çarpışma ve erişim testleri değişmeden
geçer; Docker `selftest*` modları bir kez koşulur.

## Onayını istediğim kararlar (önerim ilk seçenek)
1. **Yer/okuyucu:** profil `robot_description/profile/`, ROS'suz okuyucu+üretici `robot_core/profile*.py`
   (yeni bağımlılık yok, PyYAML zaten var).
2. **Üretilen dosyalar commit'li** + "güncel mi" testi (alternatif: derleme sırasında üretim; ROS'a
   bağlar, test edilmesi zor).
3. **Adlandırılmış pozlar** SRDF'ten profile taşınır, SRDF tamamen üretilir (yarım üretim kırılgan).
4. **Link uzunluğu:** v1'de açık geometri (şu hâli). UI için tek sayı (`length`, geometriyi ve sonraki
   eklem ofsetini birlikte sürer) sonra eklenebilir; ister misin şimdi şemaya girsin?
5. **Motor formülü ve birimi** yukarıdaki gibi mi (rad, motor mili tarafı)?

## Bu PR'da / bu PR'da değil
**Var:** bu not, bugünkü robotun profili, eşitlik testi. **Yok:** profil okuyucu, üretici, CMake kurulumu
(`profile/` henüz kurulmuyor, çalışma zamanında kimse okumuyor), UI, ESP32 köprüsü. Hiçbir çalışan dosya
değişmedi, bu yüzden selftest davranışı da değişmez.
