# Arayüzler

## ROS topic'leri

### `/perception/objects` — `visualization_msgs/MarkerArray` (robot_perception)
Kameradaki renkli nesneler, `world` çerçevesinde. Her mesaj **o an görünen tüm nesnelerin** tam
listesidir (mesajda olmayan nesne yok demektir). Marker başına:

| Alan | Anlam |
|---|---|
| `ns` | renk etiketi: `red`, `green`, `blue` |
| `id` | aynı renkteki blob sırası (0 = görüntüde en büyük) — kalıcı kimlik DEĞİL |
| `pose.position` | nesne merkezi tahmini (m, world) |
| `scale` | sınırlayıcı kutu (x, y, z; m) |
| `lifetime` | 1 s (RViz'de kaybolan nesne silinir) |

Varsayımlar: nesneler zemine oturuyor, dışbükey ve yaklaşık simetrik; kamera üstten bakıyor
(merkez = görünen üst yüzün ortası, merkez yüksekliği = nesne yüksekliğinin yarısı). Yuvarlak
nesnelerin arka yüzü görünmediği için boyut ~1 cm eksik çıkabilir.

Başlatma: `ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true`
(ya da çalışan simülasyona `ros2 launch robot_perception perception.launch.py`).
Doğrulama: `ros2 run robot_perception perception_check`.
