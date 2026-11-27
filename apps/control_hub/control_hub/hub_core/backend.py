"""Robot arka ucu sözleşmesi. Hub robotu yalnızca bu arayüz üzerinden tanır.

* ``FakeBackend``: ROS'suz, donanımsız simülasyon (geliştirme, testler, telefondan ilk deneme).
* ``RobotApiBackend``: robotun HTTP API v1'i (``/api/jobs``, ``/api/stop``, ``/api/reset``).
* ``SimBackend``: SİM (``--sim``); ``MotionBackend`` + ``MoveBackend`` + ``SimControl`` yüzeyini bilir.

Hareket sözleşmesi (``MotionBackend``/``MoveBackend``/``SimControl``) rotaların ``hub.backend.<ad>(...)``
diye DOĞRUDAN çağırdığı yöntemlerin imzasını SABİTLER (MIMARI.md §7 R6, T6). Rotalar ``getattr`` ile
yoklamaz: bir arka uç bir aileyi bilmiyorsa varsayılanı DEVRALIR (``NoJog``, ``NoMove``, ``NoSim``) ve
varsayılan eski rota yanıtının AYNISINI verir (501 ``jog_unsupported`` / 501 ``move_unsupported`` / 409
``not_sim``). Yöntem hiç yoksa (varsayılan da devralınmamışsa) rota ``AttributeError`` ile 500 olur; imza
kayarsa yine çalışma anında 500. ``tests/test_backend_contract.py`` üç arka ucun imzasını bu Protocol'lerle
karşılaştırır ve rotalarda ``getattr(hub.backend`` kalmadığını denetler.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol, Tuple, runtime_checkable

Reply = Tuple[int, Dict[str, Any]]      # (HTTP durum kodu, JSON gövdesi): rotalar gövdeyi aynen döndürür


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StopReport:
    """ACİL DUR'un sonucu. ``stopped=True`` yalnızca robot kolun durduğunu **doğruladıysa**."""
    stopped: Optional[bool]          # True doğrulandı · False doğrulanamadı/ulaşılamadı
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResetResult:
    ok: bool
    message: str


@runtime_checkable
class RobotBackend(Protocol):
    name: str
    # Çalışan bir hareketi ortasında kesebiliyor mu? False ise ACİL DUR yalnızca sonraki adımı
    # engeller; arayüz bunu kullanıcıya açıkça söyler.
    can_interrupt: bool

    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        """Tek robot komutunu (``commands.parse_robot_command`` beyaz listesi) bitene dek çalıştırır.

        ``cancel`` kurulursa hareketi mümkünse keser. Exception fırlatmaz; hata ``ok=False``."""

    def emergency_stop(self) -> StopReport:
        """Robotu durdur (kilitli kalır: ``reset_emergency_stop`` çağrılana dek). Exception fırlatmaz;
        sonucu ``StopReport`` bildirir (``stopped=False``: durduğu doğrulanamadı)."""

    def reset_emergency_stop(self) -> ResetResult:
        """Robot kilidini açmayı dener; robot reddedebilir (kol hâlâ hareket ediyor olabilir)."""

    def snapshot(self) -> Dict[str, Any]:
        """Canlı durum: ``connected``, ``joints``, ``tool``, ``objects``... (JSON'a uygun)."""

    def close(self) -> None: ...


@runtime_checkable
class MotionBackend(Protocol):
    """Jog / çerçeve / model: ``--fake``, ``--sim`` ve gerçek Robot API bu 6 yöntemi AYNI imzayla bilir.

    Hepsi eşzamanlıdır (rotalar ``asyncio.to_thread`` ile çağırır) ve ``(durum, gövde)`` döner; hatayı
    4xx/5xx ile bildirir (``RobotApiBackend`` ağ hatasında ``RobotApiError`` fırlatır, rota 502'ye çevirir).
    Kullanan rotalar: ``jog_routes`` (jog_command/jog_status/model/frames_*), ``mesh_routes`` (model)."""

    def jog_command(self, body: Any, *, remote: bool = False) -> Reply:
        """Jog komutu (başlat/devam/bırak). ``remote``: istek yerel değil (jog kilidi bunu görür)."""

    def jog_status(self) -> Reply:
        """Jog durumu + TCP."""

    def model(self) -> Reply:
        """Robot modeli: ``{'robot': ad, 'links': [{'name': ...}, ...]}`` (3B sahne ve parça listesi için)."""

    def frames_get(self) -> Reply:
        """Çerçeve ve küre listesi."""

    def frames_put(self, body: Any) -> Reply:
        """Çerçeve listesini değiştir."""

    def frames_reach(self, body: Any) -> Reply:
        """Küre (merkez, yarıçap) için erişim aralığı; robot oynamaz."""


@runtime_checkable
class MoveBackend(MotionBackend, Protocol):
    """``/api/move*`` ailesi: YALNIZ SİM bilir (``--fake`` ve gerçek Robot API ``NoMove`` varsayılanını
    devralır; ``supports_move`` ikisini ayırır).

    Ya TÜM 6 yöntem gerçektir ya hiçbiri: ``supports_move`` yarım kümeyi (eksik yöntem) "desteklenmiyor"
    sayar."""

    def move_submit(self, body: Any) -> Reply:
        """Hareket işi başlat (202 + ``job_id``)."""

    def move_check(self, body: Any) -> Reply:
        """Kuru çalıştırma: aynı doğrulama, robot oynamaz."""

    def move_get(self, job_id: str) -> Reply:
        """İş durumu."""

    def move_stop(self) -> Reply:
        """İşi kontrollü durdur."""

    def move_pause(self) -> Reply:
        """İşi duraklat."""

    def move_resume(self) -> Reply:
        """Duraklatılmış işe devam et."""


@runtime_checkable
class SimControl(Protocol):
    """``/api/sim/*`` uçları: YALNIZ SİM bilir. Diğer arka uçlar ``NoSim`` varsayılanını devralır (409)."""

    def sim_reset(self) -> Reply:
        """Robotu home'a döndür (simülasyon sıfırlama)."""

    def set_speed(self, pct: Any) -> Reply:
        """Genel hız yüzdesi (``robot.speed_pct``)."""


# --- Varsayılanlar: "bu aileyi bilmiyorum" cevabı, eski rota cevabıyla BAYT BAYT aynı ---------------------
# Gövde ``server.errors.error_body`` ile aynı anahtar sırasını taşır (hub_core ``server``'ı içe aktarmaz).

def _refusal(status: int, code: str, message: str) -> Reply:
    return status, {'ok': False, 'error': code, 'message': message}


class NoJog:
    """Jog / çerçeve / model yüzeyini bilmeyen arka uç bunu devralır (501 ``jog_unsupported``).
    Üç mevcut arka uç jog'u bilir; bu yalnız ileride ekleneceklerin (ör. ESP32) yolunu açık tutar."""

    def jog_command(self, body: Any, *, remote: bool = False) -> Reply:
        return _refusal(501, 'jog_unsupported', 'Bu robot arka ucu jog/çerçeve ucunu desteklemiyor')

    def jog_status(self) -> Reply:
        return self.jog_command(None)

    def model(self) -> Reply:
        return self.jog_command(None)

    def frames_get(self) -> Reply:
        return self.jog_command(None)

    def frames_put(self, body: Any) -> Reply:
        return self.jog_command(None)

    def frames_reach(self, body: Any) -> Reply:
        return self.jog_command(None)


class NoMove:
    """``/api/move*`` ailesini bilmeyen arka uç (``--fake``, gerçek Robot API) bunu devralır: 501
    ``move_unsupported``. ``supports_move`` bu sınıfı tanır; ``clock``/``time_scale`` SİM'in bildirdiği
    saat ve hız ölçeğinin "yok" karşılığıdır (``None`` = gerçek zaman, 1.0 = ölçek yok)."""

    clock: Optional[Callable[[], float]] = None
    time_scale: float = 1.0

    def move_submit(self, body: Any) -> Reply:
        return _refusal(501, 'move_unsupported', 'Bu robot arka ucu hareket uçlarını desteklemiyor')

    def move_check(self, body: Any) -> Reply:
        return self.move_submit(body)

    def move_get(self, job_id: str) -> Reply:
        return self.move_submit(job_id)

    def move_stop(self) -> Reply:
        return self.move_submit(None)

    def move_pause(self) -> Reply:
        return self.move_submit(None)

    def move_resume(self) -> Reply:
        return self.move_submit(None)


class NoSim:
    """``/api/sim/*`` uçlarını bilmeyen arka uç bunu devralır: 409 ``not_sim``."""

    def sim_reset(self) -> Reply:
        return _refusal(409, 'not_sim', 'Bu uç yalnız SİM modunda çalışır')

    def set_speed(self, pct: Any) -> Reply:
        return self.sim_reset()


def supports_move(backend: Any) -> bool:
    """Arka uç ``/api/move*`` ailesini GERÇEKTEN biliyor mu? Sözleşmenin 12 yöntemi var VE ``NoMove``
    varsayılanı değil. Yarım yöntem kümesi (ör. yalnız ``move_submit``) "bilmiyor" sayılır."""
    return isinstance(backend, MoveBackend) and not isinstance(backend, NoMove)
