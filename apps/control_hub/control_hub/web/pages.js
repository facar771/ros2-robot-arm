/* Sayfa kaydı (iskelet): web/pages/<ad>.js dosyaları kendini HubPages.register ile tanıtır; menü ve yönlendirme
 * buradadır. YENİ SAYFA = yeni dosya (ortak dosyaya dokunulmaz); şablon: docs/UI_CONTRACT.md.
 *
 *   HubPages.register({
 *     id: 'jog',                 // dosya adıyla aynı (pages/jog.js) ve adres '#/jog'
 *     title: 'Jog', icon: '🕹', order: 20,
 *     scripts: ['/frames.js'],   // isteğe bağlı: ilk gösterimde sırayla yüklenen ek betikler
 *     available(ctx) {},         // isteğe bağlı: false/Promise<false> ise menüde görünmez (neden: ctx.why)
 *     mount(root, ctx) {},       // ilk gösterimde BİR kez (root: sayfanın kutusu)
 *                                // 3B görünüm gerekiyorsa: HubPages.mountView(kutu) (mount ve onShow'da)
 *     onState(snap) {},          // mount'tan sonra her durum mesajında
 *     onEvents(events) {},       // isteğe bağlı: olay günlüğü değişince (son ~200 olay)
 *     onShow() {}, onHide() {},  // isteğe bağlı
 *   });
 * Bir sayfanın hatası yalnız o sayfayı etkiler (kutuda neden yazılır); ACİL DUR ve diğer sayfalar çalışır. */
'use strict';

window.HubPages = (() => {
  const P = { defs: new Map(), items: new Map(), current: null, started: false, loading: null };

  function register(def) {
    if (!def || !/^[a-z][a-z0-9_]{0,31}$/.test(def.id || '') || typeof def.mount !== 'function') {
      console.error('HubPages.register: geçersiz sayfa', def); return;
    }
    P.defs.set(def.id, def);
  }

  const loadScript = (src) => new Promise((resolve, reject) => {
    for (const s of document.getElementsByTagName('script')) if (s.getAttribute('src') === src) { resolve(); return; }
    const s = document.createElement('script');
    s.src = src; s.async = false;
    s.onload = () => resolve(); s.onerror = () => reject(new Error(`yüklenemedi: ${src}`));
    document.head.append(s);
  });

  async function loadAll() {
    let list = [];
    try { list = (await api('/api/pages')).pages; } catch (e) { toast('Sayfa listesi alınamadı: ' + e.message, true); return; }
    await Promise.all(list.map((p) => loadScript(p.script).catch((e) => toast(`Sayfa ${p.id}: ${e.message}`, true))));
  }

  /* Menüyü kur; her sayfanın available() sonucu beklenir ama bir sayfanın yavaşlığı diğerlerini tutmaz. */
  async function build() {
    if (P.loading) return P.loading;
    P.loading = (async () => {
      await loadAll();
      const defs = ShellLogic.sortPages([...P.defs.values()]);
      const nav = $('nav'), host = $('page-host');
      nav.replaceChildren(); host.replaceChildren(); P.items.clear();
      for (const def of defs) {
        const root = el('section', { class: 'page', id: `page-${def.id}`, hidden: true, 'data-page': def.id });
        const link = el('a', { class: 'nav-item', href: `#/${def.id}`, 'data-page': def.id }, [
          el('span', { class: 'nav-ic', text: def.icon || '▪', 'aria-hidden': 'true' }), el('span', { text: def.title || def.id })]);
        const it = { def, root, link, mounted: false, failed: null, available: true, why: '' };
        P.items.set(def.id, it);
        nav.append(link); host.append(root);
        Promise.resolve().then(() => (def.available ? def.available(it) : true)).then((ok) => {
          it.available = ok !== false; link.hidden = !it.available;
          if (!it.available && P.current === def.id) route();
        }).catch((e) => { it.available = true; it.why = String(e && e.message || e); });
      }
      window.addEventListener('hashchange', route);
      P.started = true; route();
    })();
    return P.loading;
  }

  function firstVisible() {
    for (const it of P.items.values()) if (it.available) return it.def.id;
    return null;
  }

  async function show(id) {
    const it = P.items.get(id); if (!it) return;
    if (P.current && P.current !== id) {
      const old = P.items.get(P.current);
      if (old) { old.root.hidden = true; old.link.classList.remove('active'); old.link.removeAttribute('aria-current');
        if (old.mounted && old.def.onHide) try { old.def.onHide(); } catch (e) { console.error(e); } }
    }
    P.current = id;
    it.root.hidden = false; it.link.classList.add('active'); it.link.setAttribute('aria-current', 'page');
    document.title = `${it.def.title || id} · ${S.session ? S.session.title : 'Robot Kontrol Merkezi'}`;
    if (!it.mounted && !it.failed) {
      try {
        for (const src of it.def.scripts || []) await loadScript(src);
        it.mounted = true;                                      // mount sırasında onState gelebilir
        await it.def.mount(it.root, { snap: S.snap, session: S.session });
        if (S.snap && it.def.onState) it.def.onState(S.snap);
      } catch (e) {
        it.mounted = false; it.failed = String(e && e.message || e);
        it.root.replaceChildren(el('div', { class: 'card' }, [
          el('h2', { text: it.def.title || id }), el('div', { class: 'say bad', role: 'alert', text: `Sayfa açılamadı: ${it.failed}` })]));
        console.error(e);
      }
    }
    if (it.mounted && it.def.onShow && P.current === id) try { it.def.onShow(); } catch (e) { console.error(e); }
    $('nav').classList.remove('open');
  }

  function route() {
    const want = ShellLogic.pageFromHash(location.hash);
    const it = want && P.items.get(want);
    const id = it && it.available ? want : (P.current && P.items.get(P.current).available ? P.current : firstVisible());
    if (id && id !== P.current) { if (location.hash !== `#/${id}`) history.replaceState(null, '', `#/${id}`); show(id); }
    else if (id && P.current === id && it && it.root.hidden) show(id);
  }

  /* Paylaşılan 3B görünüm (tek tuval): ilk çağrıda scene_logic.js + scene3d.js (ES modülü) yüklenir; sonra tuval
   * `container` içine taşınır (aynı anda tek yerde). Başarısız olursa yalnız o kutuya neden yazılır. */
  async function mountView(container) {
    try {
      if (!window.Scene3D) {
        P.viewLoading = P.viewLoading || (async () => { await loadScript('/scene_logic.js'); await import('/scene3d.js'); })();
        await P.viewLoading;
      }
      Scene3D.attach(container);
      if (S.snap) Scene3D.onState(S.snap);
    } catch (e) {
      P.viewLoading = null;
      container.replaceChildren(el('div', { class: 'card' }, [el('h2', { text: 'Robot 3B' }),
        el('div', { class: 'say bad', role: 'alert', text: `3B görünüm yüklenemedi: ${e.message}. Diğer işlevler çalışır.` })]));
    }
  }

  /* app.js her durum mesajında çağırır: yalnız mount edilmiş sayfalara iletilir; sayfa hatası yutulur. */
  function onState(snap) {
    if (window.Scene3D) try { Scene3D.onState(snap); } catch (e) { console.error(e); }
    for (const it of P.items.values()) {
      if (it.mounted && it.def.onState) try { it.def.onState(snap); } catch (e) { console.error(e); }
    }
  }

  /* Olay günlüğü değişince (isteğe bağlı onEvents(events)). */
  function onEvents(events) {
    for (const it of P.items.values()) {
      if (it.mounted && it.def.onEvents) try { it.def.onEvents(events); } catch (e) { console.error(e); }
    }
  }

  return { register, build, onState, onEvents, show, loadScript, mountView, current: () => P.current, _P: P };
})();
