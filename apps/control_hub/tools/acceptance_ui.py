"""Kabul adımlarının ortak arayüz yardımcıları (sayfa aç, 3B hazır, gerçek piksel tıklama).
Saf değil: bir Playwright sayfası ister (saf mantık acceptance_core.py'dedir)."""
from __future__ import annotations

import time

from tools.acceptance_core import Ctx, Fail, project

NAV_MS = 40000                                           # sayfa + 3B sahne (yazılım GL) hazır olana dek

_CAMERA_JS = """() => {
    const V = Scene3D._V; V.camera.updateMatrixWorld(true);
    const r = V.canvas.getBoundingClientRect();
    return { view: [...V.camera.matrixWorldInverse.elements], proj: [...V.camera.projectionMatrix.elements],
             rect: { l: r.left, t: r.top, w: r.width, h: r.height } };
}"""
_SET_RANGE_JS = "(n, v) => { n.value = String(v); n.dispatchEvent(new Event('input', { bubbles: true })); }"


def open_page(c: Ctx, route: str, ready: str) -> None:
    c.page.goto(f'{c.hub.base}/#/{route}')
    c.page.wait_for_selector(ready, timeout=NAV_MS)


def wait_scene(c: Ctx, prefix: str = '') -> None:
    """3B sahne gerçeklendi (ve `prefix` önekli nesne çizildi). WebGL yoksa zaman aşımı → adım KALDI."""
    c.page.wait_for_function('() => window.HubScene && HubScene.implemented !== false', timeout=NAV_MS)
    if prefix:
        js = f"() => HubScene.list().some((i) => i.startsWith('{prefix}'))"
        c.page.wait_for_function(js, timeout=NAV_MS)


def wait_for(c: Ctx, js: str, what: str, timeout: int = 10000) -> None:
    """`js` doğru olana dek bekler; olmazsa NE beklendiğini söyleyen Fail (ham zaman aşımı belirsizdir)."""
    try:
        c.page.wait_for_function(js, timeout=timeout)
    except Exception as exc:                             # noqa: BLE001 — Playwright TimeoutError
        if 'Timeout' not in str(exc):
            raise
        raise Fail(f'{what} ({timeout / 1000:g} sn içinde)') from None


def enabled(c: Ctx, sel: str, timeout: int = 20000) -> None:
    """Öğe görünür VE etkin olana dek bekler (Playwright seçicisi de olur: `button:text-is("Çalıştır")`)."""
    loc = c.page.locator(sel).first
    loc.wait_for(state='visible', timeout=timeout)
    end = time.monotonic() + timeout / 1000
    while not loc.is_enabled():
        c.expect(time.monotonic() < end, f'{sel} {timeout / 1000:.0f} sn içinde etkinleşmedi')
        c.page.wait_for_timeout(100)


def set_range(c: Ctx, sel: str, value: float) -> None:
    """Kaydırıcıyı kullanıcı gibi oynatır (değer + `input` olayı)."""
    c.page.eval_on_selector(sel, _SET_RANGE_JS, value)


def set_field(c: Ctx, sel: str, value: str) -> None:
    """Metin alanı: yaz + `change` + odaktan çık (gerçek kullanıcı gibi; fill tek başına `change` üretmez)."""
    loc = c.page.locator(sel).first
    loc.fill(value)
    loc.dispatch_event('change')
    loc.blur()


def fill_until(c: Ctx, sel: str, value: str, predicate_js: str, what: str, tries: int = 3,
               wait_ms: int = 4000) -> None:
    """Alanı doldurur, `predicate_js` doğru olana dek bekler; olmazsa yeniden yazar.
    Gerekçe: Parametreler sayfası ilk açılışta (mount + onShow) formu İKİ kez kurabilir; arada yazılan değer
    kaybolur (gözlem). İnsan yeniden yazar, betik de öyle. Hiç olmazsa `what` ile Fail verir."""
    for k in range(tries):
        c.page.fill(sel, value)
        try:
            c.page.wait_for_function(predicate_js, timeout=wait_ms)
            return
        except Exception:                                # noqa: BLE001 — zaman aşımı: form yeniden kurulmuş olabilir
            if k == tries - 1:
                raise Fail(f'{what} ({tries} denemede, her biri {wait_ms / 1000:g} sn)') from None
            c.notes.append('form yeniden kuruldu: değer yeniden yazıldı')


def _camera(c: Ctx) -> dict:
    return c.page.evaluate(_CAMERA_JS)


def click_world(c: Ctx, point_m) -> None:
    """3B tuvalde dünya noktasının GERÇEK piksel konumuna fare ile tıklar (kamera matrislerinden).
    Telefon düzeninde tuval sayfa kaydırılınca görüş dışı kalır: önce ortalanır; kamera durulana dek beklenir;
    piksel tuvalde değilse (yapışkan çubuk gibi bir öğe örtüyorsa) adım KALDI olur, yanlış yere basmaz."""
    c.page.evaluate('() => Scene3D._V.canvas.scrollIntoView({ block: "center", inline: "nearest" })')
    cam = _camera(c)
    for _ in range(15):                                  # odak/kamera animasyonu sürerken matris değişir
        time.sleep(0.2)
        nxt = _camera(c)
        done = nxt['view'] == cam['view'] and nxt['rect'] == cam['rect']
        cam = nxt
        if done:
            break
    px, py, nz = project(point_m, cam['view'], cam['proj'], cam['rect'])
    c.expect(-1 <= nz <= 1, f'hedef nokta kamera görüşü dışında (ndc_z={nz:.2f})')
    top = c.page.evaluate('([x, y]) => { const n = document.elementFromPoint(x, y); return n && n.tagName; }',
                          [px, py])
    c.expect(top == 'CANVAS', f'({px:.0f}, {py:.0f}) pikselinde 3B tuval yok (üstte: {top}): görünüm engelli')
    c.page.mouse.click(px, py)
