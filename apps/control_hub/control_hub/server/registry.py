"""Sayfa/rota kaydı: yeni bir sayfa yalnızca YENİ DOSYA ekleyerek gelir (ortak dosyaya dokunulmaz).

* ``server/<ad>_routes.py``  -> ``install(app, ctx)`` otomatik yüklenir (``ctx``: ``RouteContext``).
* ``web/pages/<ad>.js``      -> ``GET /api/pages`` listeler; ön uç hepsini yükler (``HubPages.register``).

Keşif: geliştirmede ``pkgutil`` ile (dosya sistemi). PyInstaller ``.exe`` içinde ``pkgutil`` PYZ'yi
LİSTELEYEMEZ
(denendi: boş döner), bu yüzden ``hub.spec`` derleme anında ``server/*_routes.py`` dosyalarını tarayıp
``routes.manifest`` üretir ve gömer; ``.exe`` onu okur (yine otomatik: elle liste yok). Modüllerin kendisi
``collect_submodules('control_hub')`` ile gömülür. Keşif/içe aktarma hatası ``--check-config``'te yakalanır.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List

log = logging.getLogger('control_hub.server')
ROUTES_SUFFIX = '_routes'
PAGE_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}\.js$')


@dataclass(frozen=True)
class RouteContext:
    """Rota modüllerine verilen ortak araçlar (yeni sayfa bunlardan başkasına ihtiyaç duymamalı)."""
    hub: Any
    need_view: Callable[[Any], None]        # oturum yoksa 401 (salt-okunur uç noktalar)
    need_control: Callable[[Any], None]     # kilitliyse 403, oturum yoksa 401 (komut uç noktaları)
    token_of: Callable[[Any], Any]
    api_error: Callable[..., Exception]     # api_error(status, code, message) -> raise edilebilir hata


MANIFEST = 'routes.manifest'


def _frozen_manifest_names() -> List[str]:
    """``.exe``: hub.spec'in derleme anında ürettiği ``routes.manifest`` (satır başına bir modül adı)."""
    base = Path(getattr(sys, '_MEIPASS', '')) / 'control_hub' / 'server' / MANIFEST
    try:
        lines = base.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    return sorted(n.strip() for n in lines if n.strip().endswith(ROUTES_SUFFIX))


def route_module_names() -> List[str]:
    """``control_hub.server`` altındaki ``*_routes`` modülleri (sıralı, ``_routes`` soneki dahil)."""
    if getattr(sys, 'frozen', False):
        return _frozen_manifest_names()
    from control_hub import server
    return sorted(m.name for m in pkgutil.iter_modules(server.__path__)
                  if m.name.endswith(ROUTES_SUFFIX) and not m.name.startswith('_'))


def install_routes(app, ctx: RouteContext) -> List[str]:
    """Her ``*_routes`` modülünün ``install(app, ctx)``'ini çağırır; kurulan modül adlarını döndürür.

    ``install`` yoksa modül sessizce atlanmaz: bu bir programlama hatasıdır, açılışta patlar (yarım arayüz
    yerine açık hata).
    """
    done: List[str] = []
    for name in route_module_names():
        mod = importlib.import_module(f'control_hub.server.{name}')
        fn = getattr(mod, 'install', None)
        if not callable(fn):
            raise RuntimeError(f'control_hub/server/{name}.py: install(app, ctx) işlevi yok '
                               '(docs/UI_CONTRACT.md "Yeni sayfa ekleme")')
        fn(app, ctx)
        done.append(name)
    return done


def page_files(web_dir: Path) -> List[str]:
    """``web/pages/*.js`` dosya adları (geçerli ad deseni; sıralı)."""
    d = Path(web_dir) / 'pages'
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and PAGE_RE.match(p.name))


CORE_ROUTES = ('agent_routes', 'bench_routes', 'jog_routes', 'mesh_routes')
CORE_PAGES = ('agent.js', 'bench.js', 'jog.js', 'robot.js')
CORE_ASSETS = ('index.html', 'app.js', 'pages.js', 'scene3d.js', 'scene_hub.js', 'scene_logic.js',
               'approach.js', 'vendor/three/three.module.js', 'vendor/three/three.core.js')


def selfcheck(web_dir: Path) -> List[str]:
    """``--check-config`` için: iskelet dosyaları ve otomatik keşif çalışıyor mu? Sorun listesi (boş = tamam).

    ``.exe``'de keşif sessizce boş dönerse (``pkgutil`` ya da ``datas`` eksikse) arayüz menüsüz açılırdı;
    bu denetim onu derleme sonrası adımında (``--check-config``) yakalar.
    """
    problems: List[str] = []
    routes, pages = route_module_names(), page_files(web_dir)
    for n in CORE_ROUTES:
        if n not in routes:
            problems.append(f'rota modülü keşfedilmedi: {n} (bulunanlar: {routes})')
    for n in CORE_PAGES:
        if n not in pages:
            problems.append(f'sayfa dosyası bulunamadı: pages/{n} (bulunanlar: {pages})')
    for n in CORE_ASSETS:
        if not (Path(web_dir) / n).is_file():
            problems.append(f'web dosyası eksik: {n}')
    for n in routes:                              # gömülü mü ve içe aktarılabiliyor mu (eksik hiddenimport)?
        try:
            mod = importlib.import_module(f'control_hub.server.{n}')
            if not callable(getattr(mod, 'install', None)):
                problems.append(f'{n}: install(app, ctx) yok')
        except Exception as exc:                  # noqa: BLE001 - her türlü içe aktarma hatası raporlanır
            problems.append(f'{n} içe aktarılamadı: {exc!r}')
    return problems
