/* Sayfa testleri için ortak SAHTE TARAYICI (robot_page_check.js, page_load_order_check.js): küçük DOM + <script>/<link> yükleyici.
 * Tarayıcı gibi davranır: async=false betikler EKLENME SIRASIYLA çalışır (indirme süresi ne olursa olsun), yüklenince onload gelir.
 * Betik gövdesi: env.virtual[src] işlevi (sahte dosya) ya da web dizininde gerçek dosya (require edilir). Test dosyası DEĞİLDİR. */
'use strict';
const fs = require('fs');
const path = require('path');

class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.parent = null; this._text = '';
    this.className = ''; this.hidden = false; this.style = { setProperty() {} };
    const self = this;
    this.classList = { add(c) { self.className = [...new Set((self.className + ' ' + c).trim().split(/\s+/))].join(' '); },
      remove(c) { self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(' '); },
      contains(c) { return self.className.split(/\s+/).includes(c); } }; }
  append(...c) { for (const x of c) { const n = typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x; n.parent = this; this.children.push(n); } }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute(k, v) { this.attrs[k] = String(v); } removeAttribute(k) { delete this.attrs[k]; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : (k === 'src' || k === 'href') && this[k] ? this[k] : null; }   // el.src = … da öznitelik sayılır
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
}
const walk = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach((c) => walk(c, f, out)); return out; };

/* env.log: sırayla olaylar ('script+ /x.js' başladı, 'script- /x.js' bitti, 'style+ …', 'style- …'); env.delay[src]: indirme ms;
 * env.fail: yüklenemeyen adresler kümesi; env.virtual: {adres: () => betik gövdesi}. */
function createEnv(web, opts = {}) {
  const env = { N, walk, log: [], delay: {}, fail: new Set(), virtual: {}, appended: [], toasts: [], chain: Promise.resolve(), pagesReply: { pages: [] }, timers: [] };
  const nav = new N('nav'), host = new N('div'); nav.id = 'nav'; host.id = 'page-host';
  const root = new N('body'); root.append(nav, host);
  env.nav = nav; env.host = host; env.byId = (id) => walk(root, (n) => n.id === id)[0] || null;
  const scripts = [];
  global.window = global;
  global.document = { title: '', createElement: (t) => new N(t), getElementById: env.byId, getElementsByTagName: (t) => (t === 'script' ? scripts.filter((s) => s.tagName === 'script') : scripts.filter((s) => s.tagName === 'link')),
    querySelectorAll: () => [], querySelector: () => null, addEventListener() {}, head: { append(s) { scripts.push(s); env.appended.push(s); load(s); } } };
  global.location = { hash: '' };
  global.history = { replaceState(_a, _b, h) { global.location.hash = h; } };
  env.hashListeners = [];
  global.addEventListener = (t, f) => { if (t === 'hashchange') env.hashListeners.push(f); };
  global.$ = env.byId;
  global.el = (tag, props = {}, children = []) => {
    const e = new N(tag);
    for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (k.startsWith('data-')) e.setAttribute(k, v);
      else if (k === 'style') continue; else e[k] = v; }
    for (const c of children) e.append(c);
    return e;
  };
  global.toast = (m, bad = false) => env.toasts.push([m, bad]);
  global.api = async (p) => { if (p === '/api/pages') { if (env.pagesReply instanceof Error) throw env.pagesReply; return env.pagesReply; } throw new Error('beklenmeyen ' + p); };
  global.S = { session: { title: 'T' }, snap: { x: 1 }, ui: null, events: [] };
  global.setInterval = (f, ms) => { env.timers.push({ f, ms }); return env.timers.length; };   // sayfa dosyalarının yükleme anı zamanlayıcıları çalışmaz
  const ms = (src) => env.delay[src] || 0;
  function load(s) {
    const isLink = s.tagName === 'link'; const src = isLink ? s.href : s.src; const tag = isLink ? 'style' : 'script';
    if (!isLink) assert_async(s);
    env.log.push(`${tag}+ ${src}`);
    const before = env.chain;                                  // eklendiği andaki sıra (kendinden önceki betikler)
    const mine = (async () => {
      await new Promise((r) => setTimeout(r, ms(src)));
      if (!isLink) await before;                               // async=false: önceki betikler çalışmadan bu çalışmaz
      if (env.fail.has(src)) { env.log.push(`${tag}! ${src}`); return s.onerror && s.onerror(); }
      if (!isLink) execute(src);
      env.log.push(`${tag}- ${src}`);
      if (s.onload) s.onload();
    })();
    if (!isLink) env.chain = mine.catch(() => {});
  }
  function assert_async(s) { if (s.async !== false) throw new Error('betik async=false olmalı (sıralı çalışma): ' + s.src); }
  function execute(src) {
    if (env.virtual[src]) { env.virtual[src](); return; }
    const file = path.join(web, src.replace(/^\//, ''));
    if (fs.existsSync(file)) { delete require.cache[require.resolve(file)]; require(file); }
  }
  /* Gerçek shell.js + TAZE pages.js (her senaryo temiz HubPages durumu ile başlar). */
  env.core = () => {
    require(path.join(web, 'shell.js'));
    const f = path.join(web, 'pages.js'); delete require.cache[require.resolve(f)]; require(f);
    return global.HubPages;
  };
  return env;
}
module.exports = { createEnv, N, walk };
