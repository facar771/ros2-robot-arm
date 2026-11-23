/* docs/UI_CONTRACT.md'deki SAYFA ŞABLONU gerçekten çalışıyor mu? Şablonu çıkarıp sahte HubPages ile çalıştırır.
 *   node page_template_check.js <şablon.js> */
'use strict';
const assert = require('assert');
const fs = require('fs');
global.window = global;
class N { constructor(t) { this.tagName = t; this.children = []; this._text = ''; }
  append(...c) { this.children.push(...c); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; } }
global.el = (tag, props = {}, children = []) => { const e = new N(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'text') e.textContent = v; else e[k] = v; }
  e.append(...children); return e; };
global.api = async (p) => { assert.strictEqual(p, '/api/hello'); return { ok: true, text: 'merhaba, arka uç: fake' }; };
let def = null;
global.HubPages = { register(d) { def = d; } };
new Function(fs.readFileSync(process.argv[2], 'utf8'))();
(async () => {
  assert.ok(def, 'HubPages.register çağrılmadı');
  assert.ok(/^[a-z][a-z0-9_]{0,31}$/.test(def.id) && def.id === 'hello');
  assert.strictEqual(typeof def.mount, 'function');
  const root = new N('section');
  await def.mount(root);
  assert.match(root.textContent, /merhaba, arka uç: fake/);
  def.onState({});
  console.log('PAGE_TEMPLATE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
