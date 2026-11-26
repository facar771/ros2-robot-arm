/* P4 pot mantığı (pot_logic.js) + Tezgâh pot paneli (bench.js PotPanel), SAHTE veri ve sahte DOM ile (jsdom yok).
 * Hub'da pot uç noktası henüz yok: panel sahte gövdeyle denenir. pytest çağırır (tests/test_bench_pot.py).
 *   node pot_panel_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);

class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.className = ''; this._text = ''; this.style = { setProperty() {} }; }
  append(...c) { for (const x of c) this.children.push(typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x); }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener() {}
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
const body = new N('body'), panels = Object.assign(new N('div'), { id: 'panels' });
body.append(panels);
const walk = (n, id) => { if (n.id === id) return n; for (const c of n.children) { const r = walk(c, id); if (r) return r; } return null; };
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach((c) => all(c, f, out)); return out; };
global.window = global;
global.document = { hidden: false, createElement: (t) => new N(t), getElementById: (id) => walk(body, id), addEventListener() {} };
global.el = (tag, props = {}, children = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (!k.startsWith('data-')) e[k] = v; }
  for (const c of children) e.append(c);
  return e;
};
let calls = [], reply = null;
global.api = async (p) => { calls.push(p); return reply(p); };
global.S = { snap: { bench: { enabled: true } } };
const timers = [];
global.setInterval = (f, ms) => { timers.push({ f, ms }); return timers.length; };
global.clearInterval = (id) => { timers[id - 1] = null; };

require(path.join(WEB, 'pages.js'));                     // gerçek HubPages.say (R8)
require(path.join(WEB, 'pot_logic.js'));
require(path.join(WEB, 'bench.js'));
const Z = PotLogic;

/* --- saf mantık: SINIR değerleri --- */
assert.strictEqual(Z.INVALID_LOW, 8); assert.strictEqual(Z.INVALID_HIGH, 1015);
for (const [raw, zone] of [[0, 'low'], [8, 'low'], [9, 'ok'], [512, 'ok'], [1014, 'ok'], [1015, 'high'], [1023, 'high'],
  [1024, 'bad'], [-1, 'bad'], [1.5, 'bad'], ['512', 'bad'], [null, 'bad'], [NaN, 'bad']]) assert.strictEqual(Z.zone(raw), zone, `zone(${raw})`);
assert.strictEqual(Z.freshness(0), 'fresh'); assert.strictEqual(Z.freshness(500), 'fresh');
assert.strictEqual(Z.freshness(501), 'stale'); assert.strictEqual(Z.freshness(-1), 'unknown');
assert.strictEqual(Z.freshness(undefined), 'unknown'); assert.strictEqual(Z.freshness(NaN), 'unknown');
const cal = { raw_min: 100, raw_max: 900, angle_at_min_deg: -90, angle_at_max_deg: 90 };
assert.ok(Math.abs(Z.angle(500, cal)) < 1e-9);
assert.ok(Math.abs(Z.angle(100, cal) + 90) < 1e-9);
assert.ok(Math.abs(Z.angle(1000, cal) - 112.5) < 1e-9);               // uç dışı kırpılmaz
assert.strictEqual(Z.angle(500, { ...cal, raw_max: 100 }), null);       // raw_min >= raw_max
assert.strictEqual(Z.angle(500, { raw_min: 100 }), null);
assert.strictEqual(Z.angle(500, null), null);
assert.strictEqual(Z.angle(null, cal), null);
let n = Z.normalize({ ok: true, hz: 15, channels: [{ ch: 2, raw: 5, age_ms: 30 }, { ch: 1, raw: 512, age_ms: 40 },
  { ch: 0, raw: 1 }, { ch: 3, raw: 1015, age_ms: 600 }, null, { ch: 4, raw: 4000 }] });
assert.deepStrictEqual(n.rows.map((r) => r.ch), [1, 2, 3, 4]);          // ch 0 ve null atıldı, sıralı
assert.deepStrictEqual(n.rows.map((r) => r.zone), ['ok', 'low', 'high', 'bad']);
assert.strictEqual(n.rows[3].raw, null); assert.strictEqual(n.hz, 15);
assert.strictEqual(Z.status(n.rows[0]).level, 'ok');
assert.strictEqual(Z.status(n.rows[1]).level, 'bad'); assert.ok(Z.status(n.rows[1]).text.includes('GEÇERSİZ'));
assert.strictEqual(Z.status(Z.normalize({ channels: [{ ch: 1, raw: 512, age_ms: 900 }] }).rows[0]).level, 'warn');
for (const bad of [null, undefined, 5, 'x', {}, { channels: 3 }]) assert.deepStrictEqual(Z.normalize(bad).rows, []);
assert.strictEqual(Z.normalize({}).ok, false);

/* --- Tezgâh pot paneli: sahte veriyle --- */
(async () => {
  reply = async (p) => { if (p === '/api/params') return { draft: { edits: { 'pot.joint_1.channel': 1, 'pot.joint_1.raw_min': 100,
    'pot.joint_1.raw_max': 900, 'pot.joint_1.angle_at_min_deg': -90, 'pot.joint_1.angle_at_max_deg': 90, 'pot.joint_2.raw_min': 5 } } };
    throw Object.assign(new Error('Not Found'), { status: 404 }); };
  PotPanel.mount(panels);
  await new Promise((r) => setImmediate(r));
  assert.ok(walk(body, 'p-pot'), 'pot paneli çizilmeli');
  assert.deepStrictEqual(Object.keys(PotPanel._state.cal), ['1']);       // yalnız KANALI olan kalibrasyon
  assert.strictEqual(PotPanel._state.cal[1].name, 'joint_1');
  PotPanel.mount(panels);                                                 // ikinci mount: çift panel YOK
  assert.strictEqual(all(body, (x) => x.id === 'p-pot').length, 1);

  PotPanel.feed({ ok: true, hz: 15, channels: [{ ch: 1, raw: 500, valid: true, age_ms: 40 }, { ch: 2, raw: 4, valid: false, age_ms: 40 },
    { ch: 3, raw: 1020, valid: false, age_ms: 40 }, { ch: 4, raw: 600, valid: true, age_ms: 700 }] });
  const text = walk(body, 'pt-tbl').textContent;
  assert.ok(text.includes('K1 · joint_1') && text.includes('500') && text.includes('0.0°'), 'ham + açı');
  assert.ok(text.includes('40 ms'), 'tazelik ms');
  assert.ok(text.includes('GEÇERSİZ'), 'geçersiz bölge uyarısı');
  assert.ok(text.includes('bayat (700 ms)'), 'bayat örnek');
  assert.ok(walk(body, 'pt-state').textContent.includes('GEÇERSİZ ham değer var'));
  assert.ok(walk(body, 'pt-state').textContent.includes('15 Hz'));
  const bad = all(body, (x) => /pt-bad/.test(x.className));
  assert.strictEqual(bad.length, 2, 'iki geçersiz satır kırmızı');

  PotPanel.feed({ nonsense: true });                                      // bozuk gövde: çökme yok, "veri yok"
  assert.ok(walk(body, 'pt-state').textContent.includes('pot verisi yok'));
  PotPanel.feed({ ok: true, channels: [] });
  assert.ok(walk(body, 'pt-state').textContent.includes('Kanal yok'));

  /* uç nokta yok (404): panel nedeni yazar ve sorguyu keser */
  calls = [];
  PotPanel.start();
  await new Promise((r) => setImmediate(r));
  assert.ok(calls.includes('/api/pot'));
  assert.ok(walk(body, 'pt-state').textContent.includes('pot uç noktası yok'));
  assert.strictEqual(PotPanel._state.timer, null, '404 sonrası sorgu durur');
  calls = [];
  PotPanel.start(); await new Promise((r) => setImmediate(r));
  assert.ok(!calls.includes('/api/pot'), '404 sonrası tekrar sorulmaz');

  /* Hub snapshot'ında pot varsa uç noktaya hiç gidilmez */
  PotPanel._state.missing = false; calls = [];
  S.snap.pot = { ok: true, hz: 14, channels: [{ ch: 1, raw: 300, age_ms: 20 }] };
  PotPanel.start(); await new Promise((r) => setImmediate(r));
  assert.ok(!calls.includes('/api/pot'));
  assert.ok(walk(body, 'pt-tbl').textContent.includes('300'));
  PotPanel.stop();
  assert.strictEqual(PotPanel._state.timer, null);
  console.log('POT_PANEL_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
