/* pages.js SAYFA YÜKLEME SIRASI (R9): gerçek shell.js + pages.js, sahte tarayıcı (tests/js/page_env.js; betikler EKLENME sırasıyla çalışır).
 * pages_check.js kayıt/menü/yetenek/mount-bir-kez'i sınar; burası "ne ne zaman yüklenir" sorusunu sınar:
 *  1 sayfa dosyaları (/api/pages)   2 yükleme hataları   3 tembel bağımlılık (stil → betikler, sıralı, tekrarsız)
 *  4 bağımlılık hatası (kutuda neden, yeniden deneme yok)   5 3B görünüm yükleyici   6 adres çubuğuyla açılış
 *  7 web/pages/*.js GERÇEK dosyalarının bildirimleri: dosya var mı, mantık betiği paneli önce mi yükleniyor.
 *   node page_load_order_check.js <web dizini> */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { createEnv } = require('./page_env.js');
const WEB = path.resolve(process.argv[2]);
const tick = (ms = 12) => new Promise((r) => setTimeout(r, ms));
const SHOW = console.error;                                                // quiet() sırasında da hata görünsün diye
const quiet = () => { const e = console.error; console.error = () => {}; return () => { console.error = e; }; };

/* Her senaryo: temiz sahte tarayıcı + taze HubPages. pages: {id: tanım}; dosya gövdesi tanımı HubPages.register ile kaydeder. */
function scene(pages, fileDelay = {}) {
  const env = createEnv(WEB); const HubPages = env.core();
  const ev = env.log;                                                    // sayfa olayları da aynı sırada günlüğe yazılır
  const mk = (id, extra = {}) => Object.assign({ id, title: id.toUpperCase(), order: 50,
    mount() { ev.push(`${id}:mount`); }, onState() { ev.push(`${id}:state`); }, onShow() { ev.push(`${id}:show`); }, onHide() { ev.push(`${id}:hide`); } }, extra);
  env.pagesReply = { pages: Object.keys(pages).map((id) => ({ id, script: `/pages/${id}.js` })) };
  for (const [id, extra] of Object.entries(pages)) { env.virtual[`/pages/${id}.js`] = () => HubPages.register(mk(id, extra)); env.delay[`/pages/${id}.js`] = fileDelay[id] || 0; }
  location.hash = '';
  return { env, HubPages, ev };
}
const menu = (env) => env.nav.children.map((c) => c.attrs['data-page']);
const box = (env, id) => env.host.children.find((c) => c.attrs['data-page'] === id);

(async () => {
  const restoreErr = quiet();

  /* ---- 1) sayfa dosyaları: hepsi paralel istenir, ama EKLENME sırasıyla çalışır; menü order→id, ilk görünür sayfa açılır ---- */
  {
    const { env, HubPages, ev } = scene({ zeta: { order: 20 }, alpha: { order: 20 }, mid: { order: 10 }, first: { order: 5 } },
      { zeta: 30, alpha: 0, mid: 15, first: 5 });                                                   // indirme süreleri ters
    await HubPages.build(); await tick(60);
    assert.deepStrictEqual(ev.slice(0, 4), ['script+ /pages/zeta.js', 'script+ /pages/alpha.js', 'script+ /pages/mid.js', 'script+ /pages/first.js']);   // hepsi önce istenir
    assert.deepStrictEqual(ev.filter((e) => e.startsWith('script-')), ['script- /pages/zeta.js', 'script- /pages/alpha.js', 'script- /pages/mid.js', 'script- /pages/first.js']);
    assert.deepStrictEqual([...HubPages._P.defs.keys()], ['zeta', 'alpha', 'mid', 'first']);        // kayıt sırası = liste sırası (async=false)
    assert.deepStrictEqual(menu(env), ['first', 'mid', 'alpha', 'zeta']);                          // menü sırası ise order, eşitse id
    assert.strictEqual(HubPages.current(), 'first'); assert.strictEqual(location.hash, '#/first');
    assert.deepStrictEqual(ev.filter((e) => /:(mount|state|show|hide)$/.test(e)), ['first:mount', 'first:state', 'first:show']);   // yalnız İLK sayfa mount; diğerleri tembel
    assert.strictEqual(document.title, 'FIRST · T');
    assert.ok(env.appended.filter((s) => s.tagName === 'script').every((s) => s.async === false));
  }

  /* ---- 2) yükleme hataları: liste alınamazsa/bir dosya yüklenemezse kalanlar çalışır, neden bildirilir ---- */
  {
    const { env, HubPages } = scene({ ok: {} });
    env.pagesReply = new Error('Hub yok');
    HubPages.register({ id: 'static', title: 'S', mount() {} });                                    // betikten bağımsız kayıtlı sayfa
    await HubPages.build(); await tick();
    assert.deepStrictEqual(env.toasts, [['Sayfa listesi alınamadı: Hub yok', true]]); assert.deepStrictEqual(menu(env), ['static']);
  }
  {
    const { env, HubPages, ev } = scene({ ok: { order: 1 }, broken: { order: 2 }, last: { order: 3 } });
    env.fail.add('/pages/broken.js');
    await HubPages.build(); await tick();
    assert.deepStrictEqual(env.toasts, [['Sayfa broken: yüklenemedi: /pages/broken.js', true]]);   // yalnız o sayfa kaybolur
    assert.deepStrictEqual(menu(env), ['ok', 'last']); assert.ok(ev.includes('ok:mount'));
  }
  {
    const { env, HubPages } = scene({ one: { title: 'Birinci' }, two: {} });
    env.virtual['/pages/two.js'] = () => HubPages.register({ id: 'one', title: 'İkinci', mount() {} });   // aynı kimlik: SON kayıt kazanır
    await HubPages.build(); await tick();
    assert.deepStrictEqual(menu(env), ['one']); assert.strictEqual(HubPages._P.defs.get('one').title, 'İkinci');
  }

  /* ---- 3) tembel bağımlılık: ilk gösterimde stil → betikler SIRAYLA (yavaş olan beklenir), ortak betik bir kez ---- */
  {
    const { env, HubPages, ev } = scene({
      a: { order: 10 },
      b: { order: 20, styles: ['/pages/b.css'], scripts: ['/d1.js', '/d2.js'] },
      c: { order: 30, scripts: ['/d2.js', '/d3.js'] },
      d: { order: 40, scripts: ['/pages/d.js'] },                                                   // kendi sayfa dosyası zaten yüklü
      z: { order: 60, styles: ['/pages/z.css'] } });                                                // stili sayfa HTML'inde zaten bağlı
    env.delay['/d1.js'] = 25; env.delay['/pages/b.css'] = 10;
    const pre = el('link', { rel: 'stylesheet', href: '/pages/z.css' }); document.head.append(pre);   // önceden bağlı stil
    await HubPages.build(); await tick();
    assert.ok(!ev.some((e) => /d1|d2|d3|b\.css/.test(e)));                                          // gösterilmeden hiçbir bağımlılık yüklenmez
    const mark = ev.length;
    await HubPages.show('b');
    assert.deepStrictEqual(ev.slice(mark), ['a:hide', 'style+ /pages/b.css', 'style- /pages/b.css', 'script+ /d1.js', 'script- /d1.js',
      'script+ /d2.js', 'script- /d2.js', 'b:mount', 'b:state', 'b:show']);                         // eski gizlenir → stil → d1 BİTMEDEN d2 başlamaz → mount
    const m2 = ev.length; await HubPages.show('c');
    assert.deepStrictEqual(ev.slice(m2), ['b:hide', 'script+ /d3.js', 'script- /d3.js', 'c:mount', 'c:state', 'c:show']);   // /d2.js yeniden eklenmez
    const m3 = ev.length; await HubPages.show('d');
    assert.deepStrictEqual(ev.slice(m3), ['c:hide', 'd:mount', 'd:state', 'd:show']);               // sayfa dosyası ikinci kez eklenmez
    await HubPages.show('z'); assert.strictEqual(ev.filter((e) => e === 'style+ /pages/z.css').length, 1);   // aynı stil ikinci kez eklenmez
    const m4 = ev.length; await HubPages.show('b');
    assert.deepStrictEqual(ev.slice(m4), ['z:hide', 'b:show']);                                     // ikinci gösterim: yükleme yok, mount yok
    assert.ok(env.appended.filter((s) => s.tagName === 'script').every((s) => s.async === false));
  }

  /* ---- 4) bağımlılık hatası: kutuda neden, mount yok, sonraki betik denenmez, yeniden deneme yok, diğer sayfalar etkilenmez ---- */
  {
    const { env, HubPages, ev } = scene({ a: { order: 1 }, e: { order: 2, scripts: ['/ok.js', '/boom.js', '/after.js'] },
      f: { order: 3, styles: ['/pages/f.css'], scripts: ['/never.js'] }, g: { order: 4 } });
    env.fail.add('/boom.js'); env.fail.add('/pages/f.css');
    await HubPages.build(); await tick();
    await HubPages.show('e');
    assert.match(box(env, 'e').textContent, /Sayfa açılamadı: yüklenemedi: \/boom\.js/);
    assert.ok(!ev.includes('e:mount') && !ev.includes('script+ /after.js'));
    const count = env.appended.length; await HubPages.show('a'); await HubPages.show('e');
    assert.strictEqual(env.appended.length, count);                                                 // başarısız sayfa yeniden denenmez
    assert.match(box(env, 'e').textContent, /Sayfa açılamadı/); assert.ok(!ev.includes('e:show'));
    await HubPages.show('f');
    assert.match(box(env, 'f').textContent, /Sayfa açılamadı: stil yüklenemedi: \/pages\/f\.css/);
    assert.ok(!ev.includes('script+ /never.js'));                                                   // stil düşünce betiğe geçilmez
    await HubPages.show('g'); assert.ok(ev.includes('g:mount') && ev.includes('g:show')); assert.strictEqual(HubPages.current(), 'g');
    HubPages.onState({ k: 1 }); assert.ok(ev.includes('g:state') && !ev.includes('e:state') && !ev.includes('f:state'));   // bozuk sayfalara durum gitmez
  }

  /* ---- 5) paylaşılan 3B görünüm: yüklenemezse yalnız o kutuya neden yazılır, sonra yeniden denenebilir; yüklüyse bağlanır ---- */
  {
    const { env, HubPages } = scene({ a: {} });
    env.virtual['/scene_logic.js'] = () => {};
    await HubPages.build(); await tick();
    const kutu = new env.N('div');
    await HubPages.mountView(kutu);                                                                // /scene3d.js bu ortamda yok → içe aktarma düşer
    assert.ok(env.log.includes('script+ /scene_logic.js'));
    assert.match(kutu.textContent, /Robot 3B/); assert.match(kutu.textContent, /3B görünüm yüklenemedi: .*Diğer işlevler çalışır\./);
    assert.strictEqual(HubPages._P.viewLoading, null);                                              // yeniden denemeye açık
    const seen = [];
    global.Scene3D = { attach: (c) => seen.push(['attach', c]), onState: (s) => seen.push(['state', s]) };
    const kutu2 = new env.N('div'); await HubPages.mountView(kutu2);
    assert.deepStrictEqual(seen, [['attach', kutu2], ['state', S.snap]]);                           // yüklüyse betik/ağ yok, tuval taşınır + durum verilir
    S.snap = null; seen.length = 0; await HubPages.mountView(kutu2); assert.deepStrictEqual(seen.map((x) => x[0]), ['attach']);
    S.snap = { x: 1 };
    global.Scene3D.onState = () => { throw new Error('3B patladı'); };
    assert.doesNotThrow(() => HubPages.onState({ y: 1 }));                                          // 3B hatası sayfaları durdurmaz
    delete global.Scene3D;
  }

  /* ---- 6) adres çubuğuyla açılış: '#/c' doğrudan c'yi açar (yalnız onun bağımlılıkları), bilinmeyen adres ilk sayfaya düşer ---- */
  {
    const { env, HubPages, ev } = scene({ a: { order: 10 }, c: { order: 30, scripts: ['/d9.js'] } });
    location.hash = '#/c';
    await HubPages.build(); await tick();
    assert.strictEqual(HubPages.current(), 'c'); assert.ok(ev.includes('c:mount') && !ev.includes('a:mount'));
    assert.deepStrictEqual(ev.filter((e) => e.includes('d9')), ['script+ /d9.js', 'script- /d9.js']);
  }
  {
    const { env, HubPages, ev } = scene({ a: { order: 10 }, hid: { order: 5, available: () => false } });
    location.hash = '#/yok';
    await HubPages.build(); await tick();
    assert.strictEqual(HubPages.current(), 'a'); assert.strictEqual(location.hash, '#/a');         // gizli/bilinmeyen sayfa seçilmez
    assert.ok(env.nav.children.find((c) => c.attrs['data-page'] === 'hid').hidden);              // available()=false → menüde gizli
  }
  restoreErr();

  /* ---- 7) GERÇEK web/pages/*.js bildirimleri (yeni sayfa eklenince de geçerli kalan sözleşme) ---- */
  {
    const env = createEnv(WEB); const HubPages = env.core();
    const files = fs.readdirSync(path.join(WEB, 'pages')).filter((f) => f.endsWith('.js')).sort();
    assert.ok(files.includes('robot.js') && files.includes('agent.js'));
    for (const f of files) {
      const before = new Set(HubPages._P.defs.keys());
      require(path.join(WEB, 'pages', f));                                                          // gerçek HubPages.register + ShellLogic.checkDef
      const added = [...HubPages._P.defs.keys()].filter((k) => !before.has(k));
      assert.deepStrictEqual(added, [path.basename(f, '.js')], `${f}: dosya adı = sayfa kimliği olmalı`);
      const def = HubPages._P.defs.get(added[0]);
      const scripts = def.scripts || [];
      assert.strictEqual(new Set(scripts).size, scripts.length, `${f}: betik iki kez bildirilmiş`);
      for (const s of scripts) {
        assert.ok(/^\/[A-Za-z0-9_./-]+\.js$/.test(s) && !s.includes('..'), `${f}: geçersiz betik adresi ${s}`);
        assert.ok(fs.existsSync(path.join(WEB, s.slice(1))), `${f}: ${s} dosyası yok`);
      }
      for (const s of scripts) {                                                                    // "X_logic.js" varsa, onu kullanan "X.js"ten ÖNCE yüklenmeli
        const m = /^\/(.+)\.js$/.exec(s);
        if (m && scripts.includes(`/${m[1]}_logic.js`)) assert.ok(scripts.indexOf(`/${m[1]}_logic.js`) < scripts.indexOf(s), `${f}: ${m[1]}_logic.js ${s}'ten önce olmalı`);
      }
      for (const st of def.styles || []) assert.ok(fs.existsSync(path.join(WEB, st.slice(1))), `${f}: ${st} dosyası yok`);   // checkDef yalnız /pages/<id>.css'e izin verir
    }
    assert.deepStrictEqual(HubPages._P.defs.get('agent').scripts, ['/agent_logic.js', '/agent.js']);   // R9: mantık, ajan panelinden önce
    assert.strictEqual(HubPages._P.defs.get('robot').scripts, undefined);                              // Robot sayfası ek betik istemez
  }

  console.log('PAGE_LOAD_ORDER_OK');
})().catch((e) => { SHOW(e); process.exit(1); });
