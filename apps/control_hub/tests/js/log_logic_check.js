/* Günlük sayfasının saf mantığı + sayfa kaydı (sahte DOM). node log_logic_check.js <web klasörü> */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

global.window = global;
class N {
  constructor(t) { this.tagName = t; this.children = []; this._text = ''; this.attrs = {}; this.listeners = {}; this.hidden = false; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; this._text = ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  get textContent() { return this._text + this.children.map((c) => (c.textContent === undefined ? String(c) : c.textContent)).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
global.el = (tag, props = {}, children = []) => {
  const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'text') e.textContent = v; else if (k === 'class') e.className = v; else if (k.startsWith('data-')) e.setAttribute(k, v); else e[k] = v; }
  e.append(...children);
  return e;
};
let def = null;
global.HubPages = { register(d) { def = d; } };
const calls = [];
let reply = null;
global.api = async (url) => { calls.push(url); if (reply instanceof Error) throw reply; return reply; };

const find = (n, fn) => (fn(n) ? n : n.children.map((c) => (c instanceof N ? find(c, fn) : null)).find(Boolean) || null);
const byRole = (root, role) => find(root, (n) => n.attrs && n.attrs['data-role'] === role);
const file = path.join(process.argv[2], 'pages', 'log.js');
new Function(fs.readFileSync(file, 'utf8'))();
const LL = window.LogLogic;

(async () => {
  // --- saf mantık ---
  assert.strictEqual(LL.query({ level: 'info', source: '', q: '' }), 'limit=500');
  assert.strictEqual(LL.query({ level: 'error', source: 'robot', q: '  bağlantı ' }), 'level=error&source=robot&q=ba%C4%9Flant%C4%B1&limit=500');
  assert.strictEqual(LL.query({ level: 'nonsense' }), 'limit=500', 'bilinmeyen seviye gönderilmez');
  assert.strictEqual(LL.query({ q: 'a'.repeat(999) }).length, 'q=&limit=500'.length + 200);
  assert.strictEqual(LL.url({ level: 'warning' }, true), '/api/log.csv?level=warning&limit=500');
  assert.strictEqual(LL.url({}), '/api/log?limit=500');
  assert.strictEqual(LL.latestSeq([]), 0);
  assert.strictEqual(LL.latestSeq(null), 0);
  assert.strictEqual(LL.latestSeq([{ seq: 3 }, { seq: 9 }, { seq: 4 }, null]), 9);
  assert.strictEqual(LL.levelOf('critical'), 'critical');
  assert.strictEqual(LL.levelOf('<img onerror=1>'), 'info', 'bilinmeyen seviye sınıfa sızmaz');
  assert.match(LL.summary({ shown: 2, total: 500, capacity: 500 }), /2 \/ 500 olay.*son 500/);
  assert.doesNotMatch(LL.summary({ shown: 2, total: 5, capacity: 500 }), /bellekte/);
  assert.deepStrictEqual(LL.cells({ time: 't', level: 'x', source: 's', message: 'm', detail: null }),
    { time: 't', level: 'info', source: 's', message: 'm', detail: '' });

  // --- sayfa kaydı ---
  assert.ok(def, 'HubPages.register çağrılmadı');
  assert.ok(/^[a-z][a-z0-9_]{0,31}$/.test(def.id) && def.id === 'log');
  assert.deepStrictEqual(def.styles, ['/pages/log.css']);
  assert.strictEqual(typeof def.mount, 'function');
  assert.strictEqual(def.available, undefined, 'her zaman görünür');

  reply = { ok: true, total: 3, shown: 2, capacity: 500, levels: LL.LEVELS, sources: ['robot', 'scenario'],
            events: [{ seq: 3, time: '2026-11-24 10:00:03', level: 'error', source: 'robot', message: 'Bağlantı koptu', detail: 'token=[GİZLİ]' },
                     { seq: 1, time: '2026-11-24 10:00:01', level: 'bogus', source: 'scenario', message: '<b>x</b>', detail: '' }] };
  const root = new N('section');
  await def.mount(root);
  assert.deepStrictEqual(calls, ['/api/log?limit=500']);
  const text = root.textContent;
  assert.match(text, /Bağlantı koptu/);
  assert.match(text, /token=\[GİZLİ\]/);
  assert.match(text, /<b>x<\/b>/, 'veri düz metin olarak yazılır');
  assert.match(text, /2 \/ 3 olay gösteriliyor/);
  assert.match(text, /\[GİZLİ\], \[yol\]/, 'gizleme notu');
  assert.strictEqual(find(root, (n) => (n.className || '').startsWith('lg-empty')).hidden, true, 'satır varken "olay yok" gizli');
  assert.strictEqual(byRole(root, 'csv').href, '/api/log.csv?limit=500');
  const src = byRole(root, 'source');
  assert.deepStrictEqual(src.children.map((o) => o.value), ['', 'robot', 'scenario']);

  // --- ham WS olayları ekrana yazılmaz ---
  const before = root.textContent;
  def.onShow();
  def.onEvents([{ seq: 99, kind: 'login', level: 'info', message: 'pin=123456 SIZINTI', data: { pin: '123456' } }]);
  assert.strictEqual(root.textContent, before, 'onEvents ham metni DOM’a yazmaz');
  assert.ok(!root.textContent.includes('123456'));

  // --- hata gösterimi ---
  reply = Object.assign(new Error('limit tam sayı olmalı'), { status: 400 });
  byRole(root, 'refresh').listeners.click[0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.match(root.textContent, /Günlük okunamadı: limit tam sayı olmalı/);
  assert.strictEqual(find(root, (n) => (n.className || '').startsWith('lg-err')).hidden, false);

  // --- süzgeç değişince sorgu + CSV bağlantısı birlikte güncellenir; boş sonuç mesajı ---
  reply = { ok: true, total: 3, shown: 0, capacity: 500, levels: LL.LEVELS, sources: ['robot', 'scenario'], events: [] };
  const level = byRole(root, 'level');
  level.value = 'critical';
  level.listeners.change[0]();
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(calls[calls.length - 1], '/api/log?level=critical&limit=500');
  assert.strictEqual(byRole(root, 'csv').href, '/api/log.csv?level=critical&limit=500');
  assert.strictEqual(find(root, (n) => (n.className || '').startsWith('lg-empty')).hidden, false);
  assert.strictEqual(find(root, (n) => (n.className || '').startsWith('lg-err')).hidden, true, 'başarılı yanıt hatayı siler');
  console.log('LOG_LOGIC_JS_OK');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
