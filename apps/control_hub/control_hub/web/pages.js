/* Sayfa kaydı (iskelet): web/pages/<ad>.js dosyaları kendini HubPages.register ile tanıtır; menü ve yönlendirme
 * buradadır. YENİ SAYFA = yeni dosya (ortak dosyaya dokunulmaz); şablon: docs/UI_CONTRACT.md.
 *
 *   HubPages.register({
 *     id: 'jog',                 // dosya adıyla aynı (pages/jog.js) ve adres '#/jog'
 *     title: 'Jog', icon: '🕹', order: 20,
 *     scripts: ['/frames.js'],   // isteğe bağlı: ilk gösterimde sırayla yüklenen ek betikler
 *     styles: ['/pages/jog.css'],// isteğe bağlı: yalnız KENDİ dosyası (/pages/<id>.css); style.css'e dokunulmaz
 *     requires: ['sim'],         // isteğe bağlı: snap.caps'te yoksa menüde gizli (ör. SİM'e özgü sayfa)
 *     available(ctx) {},         // isteğe bağlı: false/Promise<false> ise menüde görünmez (neden: ctx.why)
 *                                // (requires ve available birlikte: ikisi de uygunsa görünür)
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
    const bad = ShellLogic.checkDef(def);
    if (bad) { console.error('HubPages.register: geçersiz sayfa', bad, def); return; }
    P.defs.set(def.id, def);
  }

  /* Stil dosyası: <link rel=stylesheet>, aynı kaynak (CSP style-src 'self'). Yüklenemezse sayfa kendi kutusunda hata yazar. */
  const loadStyle = (href) => new Promise((resolve, reject) => {
    for (const l of document.getElementsByTagName('link')) if (l.getAttribute('href') === href) { resolve(); return; }
    const l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = href;
    l.onload = () => resolve(); l.onerror = () => reject(new Error(`stil yüklenemedi: ${href}`));
    document.head.append(l);
  });

  /* Görünürlük = sayfanın available() sonucu VE requires yeteneklerinin hepsi (snap.caps). Durum her
   * değiştiğinde yeniden hesaplanır; böylece SİM sonradan bağlanırsa menü kendiliğinden güncellenir. */
  function recompute(it, snap) {
    it.capsOk = ShellLogic.missingCaps(it.def.requires, snap).length === 0;
    const was = it.available;
    it.available = it.capsOk && it.userOk;
    it.link.hidden = !it.available;
    if (was && !it.available && P.current === it.def.id) route();
    else if (!was && it.available && P.started && !P.current) route();
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
        const it = { def, root, link, mounted: false, failed: null, available: true, why: '', userOk: true, capsOk: true };
        P.items.set(def.id, it);
        nav.append(link); host.append(root);
        recompute(it, S.snap);                                 // requires: yetenek yoksa baştan gizli
        Promise.resolve().then(() => (def.available ? def.available(it) : true)).then((ok) => {
          it.userOk = ok !== false; recompute(it, S.snap);
        }).catch((e) => { it.userOk = true; it.why = String(e && e.message || e); recompute(it, S.snap); });
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
        for (const href of it.def.styles || []) await loadStyle(href);
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
  async function ensureView() {
    if (window.Scene3D) return;
    P.viewLoading = P.viewLoading || (async () => { await loadScript('/scene_logic.js'); await import('/scene3d.js'); })();
    await P.viewLoading;
  }

  /* Sahne API'si (HubScene, UI_CONTRACT §9-C): 3B modül yüklenince döner. Başka sayfanın nesnelerini silmemek
   * için her sayfa kendi nesne kimliklerine `<sayfa>:` önekini koyar. S1-0'da işlevler 'not_implemented' döner. */
  async function scene() { await ensureView(); return window.HubScene; }

  async function mountView(container) {
    try {
      await ensureView();
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
    for (const it of P.items.values()) recompute(it, snap);       // snap.caps değişmiş olabilir
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

  /* Ortak mesaj satırı (R8): sayfa betikleri `HubPages.say(düğüm, metin, bad)` ile yazar; kendi kopyasını TUTMAZ.
   * Düğüm yoksa (sayfa çizilmemiş/kapanmış) sessizce döner. Metin textContent ile yazılır (innerHTML yok); boş/undefined → ''.
   * `bad` yalnız 'bad' sınıfını açar/kapar: düğümün öteki sınıfları (say, jg-msg, jp-msg …) korunur.
   * `bad` METİN gelirse durum sınıfıdır ('bad' | 'warn' | 'ok' | '': Parçalar): üçü birbirini dışlar, '' hepsini kapatır; yine
   * düğümün öteki sınıfları korunur. Seçenek { list: true }: `metin` bir SATIR DİZİSİdir (düğüm <ul>); her satır bir <li>,
   * bad ise class="bad" (Tezgâh iletileri). */
  const STATUS = ['bad', 'warn', 'ok'];
  function say(node, text, bad, opts) {
    if (!node) return;
    if (opts && opts.list) { node.replaceChildren(...(text || []).map((t) => el('li', { class: bad ? 'bad' : '', text: t }))); return; }
    node.textContent = text || '';
    if (typeof bad === 'string') for (const c of STATUS) node.classList.toggle(c, c === bad);
    else node.classList.toggle('bad', !!bad);
  }

  return { register, build, onState, onEvents, show, loadScript, mountView, scene, say, current: () => P.current, _P: P };
})();
