"""AgentService: ``robot_agent``'ı Hub sürecinde sarar (kütüphane olarak; Robot API'ye HTTP).

Her kullanıcı mesajı bağımsız bir *tur*dur (konuşma geçmişi LLM'e taşınmaz: robot durumu her turda
araçlarla okunur, enjekte edilmiş bir metin sonraki turlara geçmez). Güvenlik katmanları, dıştan içe:

1. ACİL DUR Hub'ın kendi yolundan çalışır (``/api/estop``: oturumsuz, ajan kilidi/kuyruğu yok); ajan
   buna dokunamaz, yalnızca dinleyici olarak kendini keser ve bekleyen onayları reddeder.
2. Hareket eden her araç (``robot_agent`` ``motion``/``long``) Hub arayüzünde insan onayı ister;
   süre dolarsa REDDEDİLİR.
3. Tur başına hareket tavanı (``max_motions``) ve günlük LLM istek sınırı (``daily_limit``); ikisi de
   güvenlik araçlarına (acil dur, iptal) UYGULANMAZ.
4. ``robot_agent`` içindeki beyaz liste, şema, güvenli kutu, kuru çalıştırma ve anahtar sızıntı koruması.

Yapı (R10): hata sınıfları ``errors``, ayarlar ``config``, sayaç/korumalı LLM/korumalı port ``guards``
modüllerindedir; ``AgentError``, ``AgentConfig``, ``UsageCounter``, ``ALL_PROVIDERS`` burada da içe
aktarılıdır, yani ``from control_hub.agent.service import ...`` eskisi gibi çalışır.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from control_hub.agent.approvals import ApprovalQueue
from control_hub.agent.config import ALL_PROVIDERS, AgentConfig
from control_hub.agent.errors import AgentError, Cancelled, LimitReached
from control_hub.agent.fakeport import HubFakeRobotPort
from control_hub.agent.guards import GuardedLLM, GuardedPort, UsageCounter
from control_hub.agent.keystore import KeyStore
from control_hub.hub_core import Hub, RobotApiBackend

log = logging.getLogger('control_hub.agent')

MAX_MESSAGE = 2000
TRANSCRIPT_MAX = 200


def _ensure_repo_paths() -> None:
    """Depo çıkışından çalışırken (``python -m control_hub``) ``src/robot_agent`` ve ``src/robot_core``
    yolda değilse ekler; kurulu paket ya da PYTHONPATH varsa dokunmaz (donmuş .exe'de bu klasörler yoktur)."""
    import sys
    from pathlib import Path
    try:
        root = Path(__file__).resolve().parents[4] / 'src'
    except IndexError:
        return
    for name in ('robot_core', 'robot_agent'):
        path = root / name
        if path.is_dir() and str(path) not in sys.path:
            sys.path.append(str(path))


class _Sink:
    """robot_agent'ın olay alıcısı -> Hub olay günlüğü + sohbet dökümü (hepsi anahtar temizlenmiş)."""

    def __init__(self, service: 'AgentService'):
        self._svc = service

    def emit(self, kind: str, payload: Any = None, *_a: Any, **_k: Any) -> None:
        try:
            self._svc._on_agent_event(kind, payload if isinstance(payload, dict) else {})
        except Exception:    # noqa: BLE001 - günlük asla aracı bozmaz
            log.exception('ajan olayı işlenemedi')


def _default_port(hub: Hub):
    backend = hub.backend
    if getattr(backend, 'name', '') == 'fake':
        return HubFakeRobotPort(hub)
    if isinstance(backend, RobotApiBackend):
        if not backend.loopback or backend.https:
            raise AgentError('unavailable', 'robot adresi http://127.0.0.1:<port> değil: ajan yalnızca '
                             'yerel Robot API ile çalışır')
        from robot_agent.client import HttpRobot
        return HttpRobot(f'http://{backend.host}:{backend.port}')
    raise AgentError('unavailable', 'bu robot arka ucu için ajan portu yok')


class AgentService:
    def __init__(self, hub: Hub, config: Optional[AgentConfig] = None, keystore: Optional[KeyStore] = None,
                 *, port_factory: Optional[Callable[[Hub], Any]] = None,
                 llm_factory: Optional[Callable[[AgentConfig, Optional[str]], Any]] = None,
                 counter: Optional[UsageCounter] = None):
        self.hub = hub
        self.config = config or AgentConfig()
        self.keys = keystore or KeyStore()
        self._port_factory = port_factory or _default_port
        self._llm_factory = llm_factory
        self.unavailable: Optional[str] = None
        self._ra: Any = None
        try:
            try:
                import robot_agent.agent  # noqa: F401
            except ImportError:
                _ensure_repo_paths()
            import robot_agent.agent as ra_agent
            import robot_agent.executor as ra_exec
            import robot_agent.llm as ra_llm
            import robot_agent.plugins as ra_plugins
            import robot_agent.redact as ra_redact
            self._ra = {'agent': ra_agent, 'exec': ra_exec, 'llm': ra_llm, 'plugins': ra_plugins,
                        'redact': ra_redact}
            ra_redact.install_log_scrubbing()
        except Exception as exc:    # noqa: BLE001 - robot_core/robot_agent yok: ajan kapalı, Hub çalışır
            self.unavailable = (f'robot_agent kurulu değil ({type(exc).__name__}: {exc}); '
                                'PYTHONPATH=src/robot_agent:src/robot_core')
        self.counter = counter or UsageCounter(self.config.daily_limit)
        self.approvals = ApprovalQueue(self.config.approval_timeout_s, on_event=self._emit_plain)
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor: Any = None
        self._chat: List[Dict[str, Any]] = []
        self._seq = 0
        hub.add_estop_listener(self._on_estop)

    # ---- yardımcılar --------------------------------------------------------------------------
    def _scrub(self, value: Any) -> Any:
        return self._ra['redact'].scrub(value) if self._ra else value

    def _emit_plain(self, kind: str, message: str, level: str = 'info') -> None:
        self.hub.events.emit(kind, self._scrub(message), level=level if level in (
            'info', 'warning', 'error', 'critical') else 'info')

    def _add(self, role: str, text: str, **extra: Any) -> None:
        with self._lock:
            self._seq += 1
            self._chat.append({'id': self._seq, 'ts': time.time(), 'role': role,
                               'text': self._scrub(text), **extra})
            del self._chat[:-TRANSCRIPT_MAX]

    def _on_agent_event(self, kind: str, payload: Dict[str, Any]) -> None:
        payload = self._scrub(payload)
        if kind == 'agent_tool_call':
            ok = bool(payload.get('ok'))
            line = (f"{payload.get('tool')} {payload.get('arguments') or ''} -> "
                    f"{payload.get('status')}: {payload.get('message')}")
            self._add('tool', line, ok=ok, status=payload.get('status'),
                      dry_run=bool(payload.get('dry_run')))
            self.hub.events.emit(kind, line[:300], level='info' if ok else 'warning', call=payload)
        else:
            self.hub.events.emit(kind, str(payload)[:300], call=payload)

    # ---- durum --------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.unavailable is None

    @property
    def active(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def status(self) -> Dict[str, Any]:
        cfg = self.config
        return {'enabled': self.enabled, 'reason': self.unavailable,
                'provider': cfg.provider, 'providers': list(ALL_PROVIDERS), 'model': cfg.model,
                'base_url': cfg.base_url, 'dry_run': cfg.dry_run,
                'keys': self.keys.status(), 'keyring_available': self.keys.persistent_available,
                'limits': {**self.counter.snapshot(), 'per_turn_motions': cfg.max_motions,
                           'approval_timeout_s': cfg.approval_timeout_s, 'max_steps': cfg.max_steps},
                'run': {'active': self.active}, 'approvals': self.approvals.pending(),
                'chat_seq': self._seq}

    def summary(self) -> Dict[str, Any]:
        """Anlık görüntüye girer (WebSocket itmesi): yalnızca sayılar, sır yok."""
        return {'enabled': self.enabled, 'active': self.active,
                'pending_approvals': len(self.approvals.pending()), 'chat_seq': self._seq}

    def chat(self, since: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._chat if m['id'] > since]

    # ---- ayarlar / anahtar (yalnızca yerel yoldan çağrılır: bkz. server.agent_routes) ----------
    def configure(self, **changes: Any) -> AgentConfig:
        if self.active:
            raise AgentError('busy', 'bir tur çalışırken ayar değiştirilemez')
        allowed = set(asdict(self.config))
        unknown = set(changes) - allowed
        if unknown:
            raise AgentError('bad_request', f'bilinmeyen ayar: {sorted(unknown)}')
        new = AgentConfig(**{**asdict(self.config), **changes})
        problems = new.problems()
        if problems:
            raise AgentError('bad_config', '; '.join(problems))
        self.config = new
        self.counter.limit = new.daily_limit
        self.approvals.timeout_s = new.approval_timeout_s
        self._emit_plain('agent_config', f'Ajan ayarı: sağlayıcı={new.provider} model={new.model or "-"} '
                         f'dry_run={new.dry_run} tur_hareket={new.max_motions}')
        return new

    def set_key(self, provider: str, key: str, remember: bool = False) -> Dict[str, Any]:
        try:
            remembered, warning = self.keys.set(provider, key, remember)
        except ValueError as exc:
            raise AgentError('bad_key', str(exc)) from None
        self._emit_plain('agent_key', f'{provider} anahtarı ayarlandı (hatırlanıyor: '
                         f"{'evet' if remembered else 'hayır'})")
        return {'remembered': remembered, 'warning': warning}

    def clear_key(self, provider: str, forget: bool = True) -> None:
        try:
            self.keys.clear(provider, forget)
        except ValueError as exc:
            raise AgentError('bad_key', str(exc)) from None
        self._emit_plain('agent_key', f'{provider} anahtarı silindi')

    # ---- tur ----------------------------------------------------------------------------------
    def _make_llm(self) -> Any:
        cfg, llm_mod = self.config, self._ra['llm']
        if self._llm_factory is not None:
            inner = self._llm_factory(cfg, self.keys.get(cfg.provider))
        elif cfg.provider == 'offline':
            inner = llm_mod.RuleLLM()
        else:
            key = self.keys.get(cfg.provider)
            if not key:
                raise AgentError('no_key', f'{cfg.provider} anahtarı girilmemiş (masaüstü pencereden '
                                 'ya da bu bilgisayarda 127.0.0.1 üzerinden gir)')
            try:
                if cfg.provider == 'anthropic':
                    inner = llm_mod.AnthropicLLM(key, cfg.model or None)
                else:
                    inner = llm_mod.OpenAILLM(key, cfg.model, cfg.base_url)
            except ValueError as exc:
                raise AgentError('bad_config', str(self._scrub(str(exc)))) from None
        counted = None if cfg.provider == 'offline' and self._llm_factory is None else self.counter
        return GuardedLLM(inner, self._cancelled, counted)

    def _confirm(self, request: Any) -> bool:
        if self._cancelled.is_set() or self.hub.estopped:
            return False
        approved = self.approvals.request(request.tool, request.describe(), request.arguments)
        return approved and not self._cancelled.is_set() and not self.hub.estopped

    def send(self, text: str) -> None:
        """Bir tur başlatır (arka plan iş parçacığı). Hatalar ``AgentError``."""
        if not self.enabled:
            raise AgentError('unavailable', self.unavailable or 'ajan kapalı')
        text = str(self._scrub((text or '').strip()))
        if not text or len(text) > MAX_MESSAGE:
            raise AgentError('bad_request', f'mesaj 1-{MAX_MESSAGE} karakter olmalı')
        with self._lock:
            if self.active:
                raise AgentError('busy', 'önceki tur sürüyor')
            try:
                port = GuardedPort(self._port_factory(self.hub), self.hub)
            except AgentError:
                raise
            except Exception as exc:    # noqa: BLE001
                raise AgentError('unavailable', f'robot portu açılamadı: {exc}') from None
            llm = self._make_llm()
            ra = self._ra
            policy = ra['exec'].Policy(max_motion_calls=self.config.max_motions)
            self._executor = ra['exec'].ToolExecutor(
                port, policy, _Sink(self), self._confirm, dry_run=self.config.dry_run,
                plugins=ra['plugins'].PluginBridge(frozenset(), catalog_fn=lambda: []))
            self._cancelled.clear()
            self._add('user', text)
            self._thread = threading.Thread(target=self._run, args=(llm, self._executor, text),
                                            name='agent-turn', daemon=True)
            self._thread.start()
        self._emit_plain('agent_turn', f'Ajan turu başladı ({self.config.provider}'
                         f"{', kuru çalıştırma' if self.config.dry_run else ''})")

    def _run(self, llm: Any, executor: Any, text: str) -> None:
        ra = self._ra
        try:
            run = ra['agent'].AgentRunner(llm, executor, max_steps=self.config.max_steps).run(text)
            if run.text:
                self._add('assistant', run.text, halted=run.halted)
            if run.halted:
                self._add('system', f'Tur durdu: {run.halted}')
        except Cancelled:
            self._add('system', 'Ajan durduruldu.')
        except LimitReached:
            self._add('system', 'Günlük LLM istek sınırı doldu; yarın ya da sınırı yükselterek devam et.')
        except ra['llm'].LLMError as exc:
            self._add('system', f'LLM hatası: {exc}')
        except Exception as exc:    # noqa: BLE001
            log.exception('ajan turu çöktü')
            self._add('system', f'İç hata ({type(exc).__name__}); tur sonlandı.')
        finally:
            self.approvals.deny_all('tur bitti')
            self._emit_plain('agent_turn_end', 'Ajan turu bitti')

    def cancel(self) -> bool:
        """Turu keser: onaylar reddedilir, çalışan robot işi iptal edilir. (ACİL DUR değildir.)"""
        was_active = self.active
        self._cancelled.set()
        self.approvals.deny_all('ajan durduruldu')
        executor = self._executor
        if was_active and executor is not None:
            try:
                executor.execute('cancel_current_job', {})
            except Exception:    # noqa: BLE001
                log.exception('iş iptali başarısız')
        return was_active

    def _on_estop(self) -> None:
        """Hub ACİL DUR dinleyicisi: tur biter, bekleyen onaylar reddedilir. Bloklamaz."""
        self._cancelled.set()
        self.approvals.deny_all('ACİL DUR')

    def close(self) -> None:
        self._cancelled.set()
        self.approvals.close('Hub kapanıyor')
        t = self._thread
        if t is not None:
            t.join(3)
