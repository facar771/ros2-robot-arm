/* R8: ortak mesaj satırı HubPages.say(düğüm, metin, bad[, {list}]) — GERÇEK pages.js, küçük SAHTE DOM (jsdom yok).
 *   node hubpages_say_check.js <web dizini>
 * Sınananlar: metin textContent ile yazılır (HTML etiketi düz metin kalır, innerHTML kullanılmaz), boş/undefined/null → '',
 * bad yalnız 'bad' sınıfını açar/kapar ve öteki sınıfları KORUR, düğüm yoksa sessiz dönüş, list kipi (<ul><li>),
 * ve eski 6 kopyanın (frames/sphere/jog/pages-jog/algo/bench) ortak sözleşmesi: ardışık çağrılarda durum birebir. */
'use strict';
const assert = require('assert');
const path = require('path');

const WEB = path.resolve(process.argv[2]);

class N {
  constructor(tag, cls = '') {
    this.tagName = tag; this.children = []; this._text = ''; this._cls = new Set(cls.split(/\s+/).filter(Boolean));
    this.classList = { toggle: (c, on) => { if (on === undefined ? !this._cls.has(c) : on) this._cls.add(c); else this._cls.delete(c); return this._cls.has(c); },
      contains: (c) => this._cls.has(c) };
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(_v) { throw new Error('innerHTML kullanılamaz (K4)'); }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this._text = ''; this.children = []; this.append(...c); }
}
global.window = global;
global.el = (tag, props = {}, kids = []) => {
  const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else e[k] = v; }
  e.append(...kids); return e;
};
require(path.join(WEB, 'pages.js'));
const say = HubPages.say;

/* 1) API yüzeyi ve DOM'suz yükleme */
assert.strictEqual(typeof say, 'function', 'HubPages.say dışa açık olmalı');
assert.strictEqual(say.length, 4, 'say(düğüm, metin, bad, seçenek)');

/* 2) metin: textContent, boş/undefined/null → '' , HTML düz metin */
const n = new N('div', 'say jg-msg');
say(n, 'merhaba'); assert.strictEqual(n.textContent, 'merhaba');
for (const empty of ['', undefined, null]) { say(n, 'dolu'); say(n, empty); assert.strictEqual(n.textContent, '', `boş: ${String(empty)}`); }
say(n, '<b>x</b><img src=x onerror=alert(1)>'); assert.strictEqual(n.textContent, '<b>x</b><img src=x onerror=alert(1)>');
assert.strictEqual(n.children.length, 0, 'metin düğüm üretmez');

/* 3) bad: yalnız 'bad' açılıp kapanır, öteki sınıflar kalır; bad verilmezse/yalan ise KAPALI */
say(n, 'hata', true); assert.strictEqual(n.className, 'say jg-msg bad');
say(n, 'tamam'); assert.strictEqual(n.className, 'say jg-msg', 'bad verilmeyince kapanır');
say(n, 'hata', 1); assert.ok(n.classList.contains('bad'), 'doğru-sayılan bad açar');
for (const f of [false, 0, '', null, undefined]) { say(n, 'x', true); say(n, 'x', f); assert.ok(!n.classList.contains('bad'), `yalan-sayılan bad: ${String(f)}`); }
say(n, 'hata', true); say(n, 'hata', true); assert.strictEqual(n.className, 'say jg-msg bad', 'tekrar bad çift sınıf üretmez');

/* 4) düğüm yoksa sessiz dönüş (sayfa çizilmemiş/kapanmış) */
for (const none of [null, undefined]) { assert.doesNotThrow(() => say(none, 'x', true)); assert.doesNotThrow(() => say(none, ['x'], true, { list: true })); }

/* 5) list kipi: <ul><li>; bad → her li class="bad"; boş/undefined → hiç li; eski satırlar silinir */
const ul = new N('ul', 'log');
say(ul, ['bir', 'iki'], false, { list: true });
assert.deepStrictEqual(ul.children.map((c) => [c.tagName, c.className, c.textContent]), [['li', '', 'bir'], ['li', '', 'iki']]);
say(ul, ['kötü'], true, { list: true });
assert.deepStrictEqual(ul.children.map((c) => [c.tagName, c.className, c.textContent]), [['li', 'bad', 'kötü']]);
for (const none of [[], undefined, null]) { say(ul, ['x'], false, { list: true }); say(ul, none, false, { list: true }); assert.strictEqual(ul.children.length, 0); }
assert.strictEqual(ul.className, 'log', 'list kipi düğümün kendi sınıfına dokunmaz');
say(ul, ['<i>x</i>'], false, { list: true }); assert.strictEqual(ul.children[0].textContent, '<i>x</i>');
say(n, 'metin', false, {}); assert.strictEqual(n.textContent, 'metin', 'seçenek yoksa/boşsa metin kipi');

/* 6) sözleşme: rastgele ardışık çağrılar, metin kipi — durum yalnız SON çağrıya bağlı (kalıntı yok) */
let seed = 20261126; const rnd = (k) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % k; };
const TEXTS = ['', undefined, null, 'a', 'Geçersiz değer — x', '<u>y</u>'], FLAGS = [undefined, false, true, 0, 1, null];
const m = new N('div', 'say pg-x');
for (let i = 0; i < 2000; i++) {
  const t = TEXTS[rnd(TEXTS.length)], f = FLAGS[rnd(FLAGS.length)];
  say(m, t, f);
  assert.strictEqual(m.textContent, t || ''); assert.strictEqual(m.classList.contains('bad'), !!f);
  assert.ok(m.classList.contains('say') && m.classList.contains('pg-x'), 'öteki sınıflar korunur');
}

console.log('HUBPAGES_SAY_OK');
