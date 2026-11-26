/* Robot sayfası (pages/robot.js): GERÇEK shell.js + pages.js + robot.js, sahte tarayıcı üstünde (tests/js/page_env.js).
 * Sayfa, /api/pages listesinden betik olarak yüklenir (üretimdeki yol): kayıt, menü, ilk açılış, panel düzeni, durum/robot/olay
 * çizimi, iptal düğmesi ve 3B görünümün bağlanması. pytest çağırır (tests/test_page_load_order.py).
 *   node robot_page_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const { createEnv } = require('./page_env.js');
const WEB = path.resolve(process.argv[2]);
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

const robot = (o = {}) => Object.assign({ name: 'sim', mode: 'sim', connected: true, state: 'idle', moving: false, tool: [0.1, 0.2, 0.3],
  joints: [{ name: 'J1', deg: 12.34 }, { name: 'J2', deg: null }], safety: { zone: { box: { x: [0, 1], y: [-1, 1], z: [0.1, 0.5] } } } }, o);
const snap = (o = {}) => Object.assign({ caps: [], me: { can_control: true }, estop: { latched: false },
  runner: { running: false, last: null }, robot: robot() }, o);

(async () => {
  const env = createEnv(WEB);
  global.fmt = (v) => (v == null ? '—' : v.toFixed(3));                       // app.js'in gerçek biçimi
  global.hhmmss = (ts) => `t${ts}`;                                          // saat dilimine bağlı olmayan, ayırt edilebilir sahte
  const HubPages = env.core();
  const views = []; HubPages.mountView = (c) => views.push(c);               // 3B yükleme (three.js) bu testin konusu değil
  const apiCalls = []; let apiFail = false;
  const baseApi = global.api;
  global.api = async (p, m) => { if (p === '/api/pages') return baseApi(p); apiCalls.push([p, m]); if (apiFail) throw new Error('sunucu yok'); return {}; };
  const q = env.byId;
  const kv = () => { const c = q('rb-kv').children; const out = []; for (let i = 0; i < c.length; i += 2) out.push([c[i].textContent, c[i + 1].textContent]); return out; };

  /* ---- üretim yolu: /api/pages → betik yüklenir → kayıt → menü → ilk görünür sayfa olarak mount ---- */
  S.ui = null; S.snap = snap();
  S.events = Array.from({ length: 45 }, (_, i) => ({ ts: i + 1, level: i % 2 ? 'warn' : 'info', message: `m${i + 1}` }));
  env.pagesReply = { pages: [{ id: 'robot', script: '/pages/robot.js' }] };
  await HubPages.build(); await tick();
  assert.deepStrictEqual(env.log, ['script+ /pages/robot.js', 'script- /pages/robot.js']);                 // yalnız sayfa dosyası; ek betik/stil YOK
  const def = HubPages._P.defs.get('robot');
  assert.deepStrictEqual([def.id, def.title, def.icon, def.order], ['robot', 'Robot', '🤖', 10]);
  assert.ok(!def.scripts && !def.styles && !def.requires && !def.available);                                // ek yükleme/koşul istemez: her zaman görünür
  assert.strictEqual(env.nav.children.length, 1); assert.strictEqual(env.nav.children[0].attrs['data-page'], 'robot');
  assert.strictEqual(HubPages.current(), 'robot'); assert.strictEqual(location.hash, '#/robot');
  assert.deepStrictEqual(env.toasts, []);
  assert.ok(!env.host.children[0].hidden && !env.host.children[0].textContent.includes('açılamadı'));

  /* ---- mount: varsayılan paneller sırayla; 3B görünüm kutusu mount'ta ve her onShow'da bağlanır ---- */
  const grid = env.host.children[0].children[0];
  assert.strictEqual(grid.className, 'page-grid two');
  assert.strictEqual(grid.children[0].id, 'robot-view');
  assert.deepStrictEqual(grid.children[1].children.map((c) => c.id), ['p-status', 'p-robot', 'p-log']);
  assert.deepStrictEqual(views, [q('robot-view'), q('robot-view')]);                                        // mount + ilk onShow
  def.onShow(); assert.strictEqual(views.length, 3); assert.strictEqual(views[2], q('robot-view'));

  /* ---- olay günlüğü: son 40, en yeni üstte; onEvents verilen listeyi çizer (S.events'i değil) ---- */
  let lis = q('lg').children;
  assert.strictEqual(lis.length, 40); assert.strictEqual(lis[0].textContent, 't45m45'); assert.strictEqual(lis[39].textContent, 't6m6');
  assert.strictEqual(lis[0].attrs['data-level'], 'info'); assert.strictEqual(lis[1].attrs['data-level'], 'warn');
  HubPages.onEvents([{ ts: 99, level: 'error', message: 'yeni' }]);
  lis = q('lg').children; assert.strictEqual(lis.length, 1); assert.strictEqual(lis[0].textContent, 't99yeni'); assert.strictEqual(lis[0].attrs['data-level'], 'error');
  HubPages.onEvents([]); assert.strictEqual(q('lg').children.length, 0);

  /* ---- durum kartı ---- */
  const big = () => [q('st-big').textContent, q('st-big').className];
  HubPages.onState(snap());
  assert.deepStrictEqual(big(), ['Hazır', 'status-big ok']); assert.strictEqual(q('st-say').textContent, ''); assert.strictEqual(q('st-last').textContent, '');
  assert.strictEqual(q('btn-cancel').hidden, true); assert.strictEqual(q('btn-cancel').disabled, true);          // iş yokken iptal gizli
  HubPages.onState(snap({ runner: { running: false, last: { label: 'Git', message: 'tamam' } } }));
  assert.strictEqual(q('st-last').textContent, 'Son: Git: tamam');
  const running = { running: true, label: 'Kare', step: 2, steps_total: 5, say: 'Hareket ediyor', last: { label: 'Git', message: 'tamam' } };
  HubPages.onState(snap({ runner: running }));
  assert.deepStrictEqual(big(), ['Kare (adım 2/5)', 'status-big busy']); assert.strictEqual(q('st-say').textContent, 'Hareket ediyor');
  assert.strictEqual(q('st-last').textContent, ''); assert.strictEqual(q('btn-cancel').hidden, false); assert.strictEqual(q('btn-cancel').disabled, false);
  HubPages.onState(snap({ runner: running, me: { can_control: false } })); assert.strictEqual(q('btn-cancel').disabled, true);   // yetkisiz: görünür ama kapalı
  HubPages.onState(snap({ runner: running, estop: { latched: true } })); assert.deepStrictEqual(big(), ['ACİL DUR', 'status-big bad']);   // ACİL DUR her şeyin önünde

  /* ---- iptal düğmesi: POST /api/scenarios/cancel; hata bildirim olur ---- */
  q('btn-cancel').listeners.click[0](); await tick(1);
  assert.deepStrictEqual(apiCalls, [['/api/scenarios/cancel', 'POST']]); assert.deepStrictEqual(env.toasts, []);
  apiFail = true; q('btn-cancel').listeners.click[0](); await tick(1);
  assert.deepStrictEqual(env.toasts, [['sunucu yok', true]]); apiFail = false;

  /* ---- robot kartı ---- */
  HubPages.onState(snap());
  assert.deepStrictEqual(kv(), [['Arka uç', 'sim'], ['Bağlantı', 'bağlı'], ['Robot durumu', 'hazır'], ['Hareket', 'duruyor'],
    ['Uç konum (m)', '0.100  0.200  0.300'], ['Güvenli kutu', 'x 0…1, y -1…1, z 0.1…0.5']]);
  const js = q('rb-joints').children; assert.deepStrictEqual(js.map((j) => [j.children[0].textContent, j.children[1].textContent]), [['12.3°', 'J1'], ['—', 'J2']]);
  HubPages.onState(snap({ robot: robot({ name: 'FakeBackend', mode: 'fake', connected: false, state: 'estopped', moving: null, tool: null, joints: undefined,
    safety: null, bridge: undefined, current_job: { id: 'j1', command: 'move' }, last_error: { message: 'limit' } }),
  bridge: { serial_connected: true, client_connected: false, listen: '127.0.0.1:9000' } }));
  assert.deepStrictEqual(kv(), [['Arka uç', 'FakeBackend (fake)'], ['Bağlantı', 'yok'], ['Robot durumu', 'ACİL DUR'], ['Hareket', 'bilinmiyor'], ['Uç konum (m)', '—'],
    ['ESP32 köprüsü', 'seri bağlı, istemci yok (127.0.0.1:9000)'], ['Çalışan iş', 'j1: move'], ['Son hata', 'limit']]);
  assert.strictEqual(q('rb-joints').children.length, 0);
  HubPages.onState(snap({ robot: robot({ state: 'garip', moving: true }) }));
  assert.deepStrictEqual([kv()[2], kv()[3]], [['Robot durumu', '—'], ['Hareket', 'hareket ediyor']]);
  HubPages.onState(snap({ robot: null })); assert.strictEqual(kv()[0][0], 'Arka uç');                          // robot yokken kart olduğu gibi kalır, hata yok

  /* ---- bozuk durum yalnız o sayfanın çizimini bozar: Hub çökmez (console.error ile bildirilir) ---- */
  const err = console.error; let errors = 0; console.error = () => { errors += 1; };
  assert.doesNotThrow(() => HubPages.onState({ caps: [], estop: {} })); console.error = err;
  assert.strictEqual(errors, 1);

  /* ---- panel düzeni (S.ui.layout.panels): verilen sırayla, bilinmeyen atlanır; eksik panelde çizim sessizce geçer ---- */
  const remount = (panels) => { S.ui = panels ? { layout: { panels } } : { layout: {} }; const r = new env.N('section'); env.host.replaceChildren(r); def.mount(r); return r; };
  let r = remount(['log', 'bogus', 'status']);
  assert.deepStrictEqual(r.children[0].children[1].children.map((c) => c.id), ['p-log', 'p-status']);
  r = remount(['log']); assert.deepStrictEqual(r.children[0].children[1].children.map((c) => c.id), ['p-log']);
  assert.doesNotThrow(() => def.onState(snap())); assert.doesNotThrow(() => def.onEvents([{ ts: 1, level: 'info', message: 'x' }]));
  assert.strictEqual(q('lg').children.length, 1);
  r = remount(null); assert.deepStrictEqual(r.children[0].children[1].children.map((c) => c.id), ['p-status', 'p-robot', 'p-log']);   // düzen yok → varsayılan
  assert.strictEqual(q('btn-cancel').listeners.click.length, 1);

  console.log('ROBOT_PAGE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
