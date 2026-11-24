/* Sayfa kaydı (pages.js): küçük SAHTE DOM üzerinde gerçek pages.js. pytest çağırır (tests/test_ui_skeleton.py).
 *   node pages_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const WEB = path.resolve(process.argv[2]);

class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.className = ''; this.hidden = false;
    this.attrs = {}; this.parent = null; this._text = ''; this.style = { setProperty() {} };
    const self = this; this.classList = { add(c) { self.className = [...new Set((self.className + ' ' + c).trim().split(/\s+/))].join(' '); },
      remove(c) { self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(' '); },
      contains(c) { return self.className.split(/\s+/).includes(c); } }; }
  append(...c) { for (const x of c) { const n = typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x; n.parent = this; this.children.push(n); } }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute(k, v) { this.attrs[k] = v; } removeAttribute(k) { delete this.attrs[k]; } getAttribute(k) { return this.attrs[k] ?? null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
const nav = new N('nav'), host = new N('div'), head = new N('head');
const byId = { nav, 'page-host': host };
global.window = global;
const scripts = [];
global.document = { title: '', createElement: (t) => new N(t), getElementById: (id) => byId[id],
  getElementsByTagName: () => scripts, head: { append(s) { scripts.push(s); (s.tagName === 'link' ? styled : loaded).push(s.tagName === 'link' ? s.href : s.src); setTimeout(() => s.onload && s.onload(), 0); } } };
global.location = { hash: '' };
global.history = { replaceState(_a, _b, h) { location.hash = h; } };
const hashListeners = [];
global.addEventListener = (t, f) => { if (t === 'hashchange') hashListeners.push(f); };
global.removeEventListener = () => {};
const loaded = [], styled = [];
global.$ = (id) => byId[id];
global.el = (tag, props = {}, children = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k.startsWith('data-')) e.setAttribute(k, v); else if (k === 'style') continue; else e[k] = v; }
  for (const c of children) e.append(c);
  return e;
};
global.toast = (m) => { global.lastToast = m; };
let pagesReply = { pages: [] };
global.api = async (p) => { if (p === '/api/pages') return pagesReply; throw new Error('beklenmeyen ' + p); };
global.S = { session: { title: 'T' }, snap: { x: 1 } };

require(path.join(WEB, 'shell.js'));
require(path.join(WEB, 'pages.js'));
const calls = [];
const rec = (n, f) => (...a) => { calls.push(`${n}:${f}`); };

(async () => {
  const err = console.error; let errors = 0; console.error = () => { errors += 1; };
  HubPages.register({ id: 'Bad Id', mount() {} }); HubPages.register({ id: 'nomount' }); HubPages.register(null);
  HubPages.register({ id: 'x1', mount() {}, styles: ['/pages/baska.css'] });                       // başkasının/ortak stili yasak
  HubPages.register({ id: 'x2', mount() {}, styles: ['https://cdn.example/x.css'] });              // dış adres yasak
  HubPages.register({ id: 'x3', mount() {}, requires: ['Büyük'] });                                // geçersiz yetenek adı
  HubPages.register({ id: 'x4', mount() {}, requires: 'sim' });                                    // dizi olmalı
  assert.strictEqual(HubPages._P.defs.size, 0); assert.strictEqual(errors, 7);                     // geçersiz kayıtlar alınmaz

  HubPages.register({ id: 'b', title: 'B', order: 20, scripts: ['/x1.js', '/x2.js'], mount: rec('b', 'mount'), onState: rec('b', 'state'),
    onShow: rec('b', 'show'), onHide: rec('b', 'hide') });
  HubPages.register({ id: 'a', title: 'A', order: 10, mount: rec('a', 'mount'), onState: rec('a', 'state'), onShow: rec('a', 'show'), onHide: rec('a', 'hide') });
  HubPages.register({ id: 'hidden', title: 'H', order: 5, available: () => false, mount: rec('hidden', 'mount') });
  HubPages.register({ id: 'boom', title: 'Boom', order: 30, mount() { throw new Error('patladı'); }, onState: rec('boom', 'state') });
  HubPages.register({ id: 'async_off', title: 'AO', order: 40, available: async () => false, mount: rec('ao', 'mount') });
  HubPages.register({ id: 'simonly', title: 'Sim', order: 50, requires: ['sim'], mount: rec('simonly', 'mount') });
  HubPages.register({ id: 'styled', title: 'St', order: 60, styles: ['/pages/styled.css'], scripts: ['/y.js'], mount: rec('styled', 'mount') });

  await HubPages.build(); await new Promise((r) => setTimeout(r, 5));
  const links = nav.children.map((c) => [c.attrs['data-page'], c.hidden]);
  assert.deepStrictEqual(links.map((l) => l[0]), ['hidden', 'a', 'b', 'boom', 'async_off', 'simonly', 'styled']);           // order sonra id
  assert.deepStrictEqual(links.filter((l) => l[1]).map((l) => l[0]), ['hidden', 'async_off', 'simonly']);          // available() false / yetenek yok -> menüde yok
  await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(HubPages.current(), 'a');                                                         // ilk görünür sayfa
  assert.strictEqual(location.hash, '#/a');
  assert.deepStrictEqual(calls.filter((c) => c.startsWith('a:')), ['a:mount', 'a:state', 'a:show']);   // mount -> durum -> show

  HubPages.onState({ y: 2 });
  assert.ok(calls.includes('a:state') && !calls.includes('b:state'));                                  // mount edilmemiş sayfaya durum gitmez

  location.hash = '#/b'; await HubPages.show('b');
  assert.deepStrictEqual(loaded, ['/x1.js', '/x2.js']);                                                // scripts sırayla, ilk gösterimde
  assert.ok(calls.indexOf('a:hide') < calls.indexOf('b:mount'));                                       // önce eski gizlenir
  assert.ok(nav.children[1].className.indexOf('active') < 0 && nav.children[2].className.includes('active'));
  await HubPages.show('a'); await HubPages.show('b');
  assert.strictEqual(calls.filter((c) => c === 'b:mount').length, 1);                                  // mount BİR kez

  await HubPages.show('boom');                                                                         // sayfa hatası yalnız o sayfayı etkiler
  const boomRoot = host.children.find((c) => c.attrs['data-page'] === 'boom');
  assert.match(boomRoot.textContent, /Sayfa açılamadı: patladı/);
  HubPages.onState({ z: 3 });                                                                          // bozuk sayfaya durum gitmez, kalanlar sürer
  assert.ok(!calls.includes('boom:state')); assert.ok(calls.filter((c) => c === 'b:state').length >= 1);
  await HubPages.show('a');
  assert.ok(!host.children.find((c) => c.attrs['data-page'] === 'a').hidden);
  assert.ok(host.children.find((c) => c.attrs['data-page'] === 'boom').hidden);

  /* adres çubuğu: bilinmeyen/gizli sayfa -> mevcut sayfada kal */
  location.hash = '#/hidden'; hashListeners[0](); await new Promise((r) => setTimeout(r, 5));
  assert.strictEqual(HubPages.current(), 'a');
  location.hash = '#/nope'; hashListeners[0](); await new Promise((r) => setTimeout(r, 5));
  assert.strictEqual(HubPages.current(), 'a');

  /* requires: yetenek snap.caps'e gelince görünür, gidince gizlenir; şu an açıksa başka sayfaya düşer */
  const linkOf = (id) => nav.children.find((c) => c.attrs['data-page'] === id);
  assert.ok(linkOf('simonly').hidden);                                                               // durumda caps yok -> gizli
  HubPages.onState({ caps: ['sim'] }); assert.ok(!linkOf('simonly').hidden);
  location.hash = '#/simonly'; hashListeners[0](); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(HubPages.current(), 'simonly');
  HubPages.onState({ caps: [] }); await new Promise((r) => setTimeout(r, 10));
  assert.ok(linkOf('simonly').hidden); assert.notStrictEqual(HubPages.current(), 'simonly');          // yetenek gitti -> sayfadan çıkar
  HubPages.onState({ caps: 'sim' }); assert.ok(linkOf('simonly').hidden);                            // bozuk caps = yetenek yok

  /* styles: ilk gösterimde, scripts'ten ÖNCE, bir kez yüklenir; mount ondan sonra */
  assert.deepStrictEqual(styled, []);
  await HubPages.show('styled');
  assert.deepStrictEqual(styled, ['/pages/styled.css']); assert.ok(loaded.includes('/y.js'));
  assert.ok(calls.includes('styled:mount'));
  await HubPages.show('a'); await HubPages.show('styled');
  assert.strictEqual(styled.length, 1);

  console.error = err;
  console.log('PAGES_JS_OK');
})().catch((e) => { console.error(e); process.exit(1); });
