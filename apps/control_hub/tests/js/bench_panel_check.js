/* KM3 Tezgâh paneli: gerçek bench.js'i küçük SAHTE DOM üzerinde çalıştırır (jsdom yok). pytest çağırır
 * (tests/test_bench_hub.py); node yoksa o test atlanır.  node bench_panel_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);

class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.className = ''; this.value = '';
    this.disabled = false; this.checked = false; this._text = ''; this.style = { setProperty() {} }; }
  append(...c) { for (const x of c) this.children.push(typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x); }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute() {}
  async click() { if (this.disabled) return; for (const f of this.listeners.click || []) await f({ target: this }); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
const body = new N('body'), panels = Object.assign(new N('div'), { id: 'panels' });
body.append(panels);
const walk = (n, id) => { if (n.id === id) return n; for (const c of n.children) { const r = walk(c, id); if (r) return r; } return null; };
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach((c) => all(c, f, out)); return out; };
global.window = global;
global.document = { hidden: false, createElement: (t) => new N(t), getElementById: (id) => walk(body, id), addEventListener() {} };
global.setInterval = () => 0;
global.el = (tag, props = {}, children = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (!k.startsWith('data-')) e[k] = v; }
  for (const c of children) e.append(c);
  return e;
};
global.toast = () => {};
let calls = [], reply = null;
global.api = async (p, m = 'GET', b) => { calls.push({ p, m, b }); return reply(p, m, b); };
global.S = { snap: { bench: { enabled: true, open: false } } };

require(path.join(WEB, 'bench.js'));
const L = BenchLogic;
const ax = (o = {}) => ({ name: 'j1', idx: 0, pos: 10, moving: false, homed: true, enabled: true, lo: -256, hi: 256, ...o });
const st = (o = {}) => ({ ok: true, enabled: true, open: true, bench_mode: true, link_ok: true, state: 'ENABLED', reason: 'NONE',
  flags: ['BENCH_MODE'], axes: [ax()], messages: [], serial: 'COM3', bridge_active: false, ...o });

/* saf mantık */
assert.strictEqual(L.deltaFor('50', ax()), '40');
assert.strictEqual(L.deltaFor('-30', ax()), '-40');
assert.strictEqual(L.deltaFor('10', ax()), null);          // zaten orada
assert.strictEqual(L.deltaFor('257', ax()), null);         // firmware sınırı dışında: gönderilmez
assert.strictEqual(L.deltaFor('5deg', ax()), null);
assert.strictEqual(L.deltaFor('', ax()), null);
assert.strictEqual(L.stepAmount(-1, 5), '-5');
assert.ok(!L.canZero(false, st()));                        // onay kutusu yok: sıfırlama yok
assert.ok(L.canZero(true, st()));
assert.ok(!L.canZero(true, st({ bench_mode: false })));    // BENCH_MODE görülmedi
assert.ok(!L.canMove(ax(), st({ state: 'HOMED' })));
assert.ok(!L.canMove(ax({ enabled: false }), st()));
assert.ok(!L.canMove(ax({ moving: true }), st()));
assert.ok(L.canMove(ax(), st()));

(async () => {
  reply = async (p) => (p === '/api/bench' ? st({ open: false, state: null, axes: [] }) : st());
  BenchPanel.mount();
  await new Promise((r) => setImmediate(r));
  const card = walk(body, 'p-bench');
  assert.ok(card, 'panel çizilmeli');
  assert.ok(card.textContent.includes('TEZGÂH — gerçek robot DEĞİL'), 'sarı bant');
  assert.ok(walk(body, 'bn-estop').textContent.includes('ACİL DUR'));
  assert.strictEqual(walk(body, 'bn-open').disabled, false);
  assert.strictEqual(walk(body, 'bn-enable').disabled, true);

  /* açık + sıfırsız: sıfırla düğmesi onay kutusu işaretlenmeden pasif */
  reply = async (p) => st({ state: 'UNHOMED', axes: [ax({ homed: false, enabled: false })] });
  BenchPanel._state.st = st({ state: 'UNHOMED', axes: [ax({ homed: false, enabled: false })] });
  BenchPanel.render();
  const zero = all(walk(body, 'bn-axes'), (n) => n.tagName === 'button' && n.textContent === 'Sıfırla')[0];
  assert.ok(zero && zero.disabled === true, 'onaysız sıfırla pasif');
  walk(body, 'bn-confirm').checked = true;
  BenchPanel.render();
  const zero2 = all(walk(body, 'bn-axes'), (n) => n.tagName === 'button' && n.textContent === 'Sıfırla')[0];
  assert.strictEqual(zero2.disabled, false);
  calls = [];
  await zero2.click();
  assert.strictEqual(calls[0].p, '/api/bench/command');
  assert.deepStrictEqual(calls[0].b, { cmd: 'zero', axis: 'j1', confirm: true });

  /* köprü açıkken mesaj */
  BenchPanel._state.st = st({ open: false, bridge_active: true, axes: [] });
  BenchPanel.render();
  assert.ok(walk(body, 'bn-state').textContent.includes('ESP32 köprüsü açık'));

  /* ACİL DUR oturumsuz uca gider */
  calls = [];
  reply = async () => st();
  await walk(body, 'bn-estop').click();
  assert.strictEqual(calls[0].p, '/api/bench/estop');
  console.log('BENCH_PANEL_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
