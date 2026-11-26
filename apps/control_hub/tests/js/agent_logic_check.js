/* Ajan paneli (R9): (A) DOM'suz agent_logic.js saf mantığı, (B) agent.js sahte DOM + sahte fetch üzerinde (ekran/istek/bildirim
 * aynı kalır), (C) pages/agent.js kaydı. pytest çağırır (tests/test_agent_logic.py).
 *   node agent_logic_check.js <web dizini> */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const WEB = path.resolve(process.argv[2]);
const SRC = fs.readFileSync(path.join(WEB, 'agent_logic.js'), 'utf8');

/* ---------- A) saf mantık: DOM/ağ/saat OLMADAN çalışır (boş bir vm bağlamında yüklenir) ---------- */
global.window = global;
const L = require(path.join(WEB, 'agent_logic.js'));                                  // module.exports = window.AgentLogic (jog_logic.js deseni)
const bare = { window: {} }; vm.createContext(bare); vm.runInContext(SRC, bare);     // document/fetch/saat YOK: yine de yüklenir ve çalışır
assert.deepStrictEqual(Object.keys(bare.window.AgentLogic), Object.keys(L));
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');           // yorumlar çıkarılır, yalnız kod taranır
assert.doesNotMatch(CODE, /\b(document|fetch|setInterval|setTimeout|localStorage|innerHTML)\b|\bel\(/);   // DOM'suz sözü korunur

const ST = { enabled: true, provider: 'openai', model: 'gpt-x', base_url: 'https://a/v1', dry_run: true, can_configure: true,
  limits: { daily_limit: 100, per_turn_motions: 3, used_today: 7 }, run: { active: false }, approvals: [],
  keys: { openai: { set: true, remembered: false }, anthropic: { set: true, remembered: true }, x: { set: false } } };
const clone = (o) => JSON.parse(JSON.stringify(o));

assert.strictEqual(L.PATH.status, '/api/agent/status'); assert.strictEqual(L.PATH.keyClear, '/api/agent/key/clear');
assert.strictEqual(L.COUNTDOWN_MS, 1000);
assert.deepStrictEqual(L.LIMITS, { text: 2000, model: 100, daily: [1, 100000], motions: [0, 20] });
assert.deepStrictEqual([{ enabled: true }, { enabled: false }, {}, null, undefined].map(L.isEnabled), [true, false, false, false, false]);

assert.deepStrictEqual(L.requestOptions('GET', undefined, 'c'), { method: 'GET', credentials: 'same-origin', headers: {} });
let o = L.requestOptions('POST', { a: 1 }, 'tok');
assert.deepStrictEqual(o.headers, { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' });
assert.strictEqual(o.body, '{"a":1}'); assert.strictEqual(o.credentials, 'same-origin');
assert.strictEqual(L.requestOptions('POST', undefined, 't').body, '{}');                                  // gövdesiz yazma = {}
assert.ok(!('body' in L.requestOptions('GET', { a: 1 }, 't')));                                           // GET gövde taşımaz

assert.strictEqual(bare.window.AgentLogic.metaText(ST), L.metaText(ST));
assert.strictEqual(L.keyText(ST), 'openai: ayarlı (yalnızca bellekte) · anthropic: ayarlı (hatırlanıyor) · x: yok');
assert.strictEqual(L.metaText(ST), 'KURU ÇALIŞTIRMA · openai / gpt-x · bugün 7/100 istek · tur başına en çok 3 hareket — ' + L.keyText(ST));
const live = Object.assign(clone(ST), { dry_run: false, model: '' });
assert.ok(L.metaText(live).startsWith('openai · bugün 7/100 istek'));                                     // model yoksa " / " yok, uyarı yok

assert.deepStrictEqual(['user', 'assistant', 'tool', 'system', 'garip'].map(L.roleLabel), ['Sen', 'Ajan', 'Araç', 'Sistem', 'garip']);
assert.strictEqual(L.chatClass({ role: 'tool', ok: false }), 'ag-m tool bad');
assert.strictEqual(L.chatClass({ role: 'tool', ok: true }), 'ag-m tool');
assert.strictEqual(L.chatClass({ role: 'tool' }), 'ag-m tool');                                           // ok yok = hata değil
assert.strictEqual(L.chatClass({ role: 'assistant', ok: false }), 'ag-m assistant');                      // yalnız araç kırmızı olur

assert.strictEqual(L.chatPath(false, 6), '/api/agent/chat?since=6'); assert.strictEqual(L.chatPath(true, 6), '/api/agent/chat?since=0');
const prev = [{ id: 1 }, { id: 2 }]; const inc = [{ id: 5 }, { id: 3 }];
let mg = L.mergeChat(prev, inc, false, 2);
assert.deepStrictEqual(mg, { msgs: [{ id: 1 }, { id: 2 }, { id: 5 }, { id: 3 }], seq: 5 }); assert.strictEqual(prev.length, 2);   // girdi değişmez
mg = L.mergeChat(prev, [{ id: 1 }], true, 9);                                                              // zorla: liste sıfırlanır, imleç GERİ GİTMEZ
assert.deepStrictEqual(mg, { msgs: [{ id: 1 }], seq: 9 });
assert.deepStrictEqual(L.mergeChat(prev, [], false, 2), { msgs: prev, seq: 2 }); assert.notStrictEqual(L.mergeChat(prev, [], false, 2).msgs, prev);

const sig = L.formSignature(ST);
assert.strictEqual(sig, JSON.stringify([true, 'openai', 'gpt-x', 'https://a/v1', true, 100, 3]));
const withUsed = clone(ST); withUsed.limits.used_today = 8;
assert.strictEqual(L.formSignature(withUsed), sig);                                                        // kullanım sayacı formu yeniden kurmaz
assert.notStrictEqual(L.formSignature(Object.assign(clone(ST), { model: 'b' })), sig);
for (const [force, same, typed, want] of [[true, true, false, true], [false, true, false, false], [false, false, false, true],
  [false, false, true, false], [true, false, true, false]]) {
  assert.strictEqual(L.shouldRebuildForm({ force, signature: same ? 's' : 't', previous: 's', keyTyped: typed }), want, [force, same, typed].join());
}

assert.deepStrictEqual([{ agent: {} }, {}, null].map(L.hasAgent), [true, false, false]);
assert.strictEqual(L.stateSignature({ agent: { chat_seq: 7, active: true, pending_approvals: 1 }, estop: { latched: false } }), '[7,true,1,false]');
assert.deepStrictEqual([[{ run: { active: false } }, true], [{ run: { active: true } }, true], [{ run: { active: false } }, false],
  [{}, true]].map(([st, c]) => L.sendDisabled(st, c)), [false, true, true, true]);
assert.deepStrictEqual([{ run: { active: true } }, { run: { active: false } }, {}].map(L.stopDisabled), [false, true, true]);   // run yoksa da atmaz

assert.strictEqual(L.topApprovalId({ approvals: [] }), null); assert.strictEqual(L.topApprovalId({ approvals: [{ id: 'a' }, { id: 'b' }] }), 'a');
assert.deepStrictEqual([['x', 'a'], ['a', 'a'], [null, 'a'], ['a', null], [undefined, null]].map(([p, t]) => L.isNewApproval(p, t)),
  [true, false, true, false, false]);
assert.strictEqual(L.approvalPath('ap/1 x'), '/api/agent/approvals/ap%2F1%20x');                           // kimlik yol parçası olarak kaçışlanır
assert.deepStrictEqual(L.decisionBody(false), { approve: false });
assert.deepStrictEqual([29.2, 0.1, 5].map(L.leftLabel), ['30 sn', '1 sn', '5 sn']);
assert.deepStrictEqual(['3', '0.5', '0', '29.2'].map(L.nextLeft), [2, 0, 0, 28.2]);                        // asla negatif olmaz

assert.strictEqual(L.messageText('  merhaba \n'), 'merhaba'); assert.strictEqual(L.messageText('   '), '');
assert.strictEqual(JSON.stringify(L.messageBody('x')), '{"text":"x"}');
assert.strictEqual(JSON.stringify(L.configBody({ provider: 'p', model: 'm', baseUrl: 'u', dailyLimit: '250', maxMotions: '5abc', dryRun: false })),
  '{"provider":"p","model":"m","base_url":"u","daily_limit":250,"max_motions":5,"dry_run":false}');       // anahtar SIRASI sabit
assert.strictEqual(JSON.stringify(L.configBody({ provider: 'p', model: 'm', baseUrl: 'u', dailyLimit: '', maxMotions: '08', dryRun: true })),
  '{"provider":"p","model":"m","base_url":"u","daily_limit":null,"max_motions":8,"dry_run":true}');       // boş → NaN → null (sunucu reddeder)
assert.strictEqual(JSON.stringify(L.keyBody('p', 'sk', true)), '{"provider":"p","key":"sk","remember":true}');
assert.strictEqual(JSON.stringify(L.keyClearBody('p')), '{"provider":"p","forget":true}');
assert.deepStrictEqual(L.keyNotice({ ok: true }), { text: 'Anahtar ayarlandı', bad: false });
assert.deepStrictEqual(L.keyNotice({ warning: 'bellekte' }), { text: 'bellekte', bad: true });

/* ---------- B) AgentPanel: sahte DOM + sahte fetch (gerçek agent_logic.js + agent.js) ---------- */
class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attrs = {}; this._text = ''; this.scrolled = 0; }
  append(...c) { for (const x of c) this.children.push(typeof x === 'string' ? Object.assign(new N('#text'), { _text: x }) : x); }
  replaceChildren(...c) { this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  get dataset() { const a = this.attrs; return new Proxy({}, { get: (_t, k) => a['data-' + k], set: (_t, k, v) => { a['data-' + k] = String(v); return true; } }); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  scrollIntoView() { this.scrolled += 1; }
}
const walk = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach((c) => walk(c, f, out)); return out; };
const body = new N('body'); const panels = new N('div'); panels.id = 'panels'; body.append(panels);
const byId = (id) => walk(body, (n) => n.id === id)[0] || null;
const byText = (txt) => walk(body, (n) => n.tagName === 'button' && n.textContent === txt)[0];
global.document = { createElement: (t) => new N(t), getElementById: byId,
  querySelectorAll: (sel) => walk(body, (n) => String(n.className || '').split(/\s+/).includes(sel.slice(1))) };
global.el = (tag, props = {}, children = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (k.startsWith('data-')) e.setAttribute(k, v); else e[k] = v; }
  for (const c of children) e.append(c);
  return e;
};
global.ApiError = class ApiError extends Error { constructor(s, b) { super((b && b.message) || `HTTP ${s}`); this.status = s; } };
let toasts = [], calls = [], timers = [];
global.toast = (m, bad = false) => toasts.push([m, bad]);
global.setInterval = (f, ms) => { timers.push({ f, ms }); return timers.length; };
global.S = { snap: { me: { can_control: true } } };
const world = { st: null, msgs: [], fail: {}, keyReply: { ok: true }, down: false };
global.fetch = async (p, opt) => {
  calls.push({ p, method: opt.method, headers: opt.headers, body: opt.body, cred: opt.credentials });
  if (world.down) throw new Error('ağ yok');
  const key = `${opt.method} ${p.split('?')[0]}`; const bad = world.fail[key];
  const res = bad || (key === 'GET /api/agent/status' ? { status: 200, body: clone(world.st) }
    : key === 'GET /api/agent/chat' ? { status: 200, body: { messages: world.msgs.filter((m) => m.id > Number(p.split('since=')[1])) } }
      : key === 'POST /api/agent/key' ? { status: 200, body: world.keyReply } : { status: 200, body: { ok: true } });
  return { ok: res.status < 400, status: res.status, json: async () => { if (res.noBody) throw new Error('gövde yok'); return res.body; } };
};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
const fresh = (over = {}) => Object.assign({ csrf: 'tok1', chat_seq: 6, keys: { openai: { set: true, remembered: false } }, providers: ['openai', 'anthropic'] },
  clone(ST), over);
const agentState = (seq, active = 0, pend = 0, latched = false) => ({ agent: { chat_seq: seq, active: !!active, pending_approvals: pend }, estop: { latched } });
const MSGS = [{ id: 1, role: 'user', text: 'Merhaba' }, { id: 2, role: 'assistant', text: 'Selam' }, { id: 3, role: 'tool', text: 'hata', ok: false },
  { id: 4, role: 'tool', text: 'tamam', ok: true }, { id: 5, role: 'system', text: 'not' }, { id: 6, role: 'weird', text: '?' }];
const posts = () => calls.filter((c) => c.method === 'POST');

(async () => {
  require(path.join(WEB, 'agent.js'));                                                  // agent_logic.js üstte yüklendi (sayfa kaydı da bu sırayı ister)
  assert.strictEqual(window.AgentLogic, L);

  /* ajan kapalı: panel çizilmez, geri sayım kurulmaz */
  world.st = { enabled: false, csrf: 'x' };
  AgentPanel.mount(null); await settle();
  assert.strictEqual(byId('p-agent'), null); assert.strictEqual(timers.length, 0); assert.strictEqual(calls.length, 1);

  /* açık: kurulum + ilk yükleme (durum, sonra sohbet since=0); ikinci mount yok sayılır */
  world.st = fresh(); world.msgs = clone(MSGS); calls = [];
  AgentPanel.mount(null); await settle();
  assert.deepStrictEqual(calls.map((c) => c.p), ['/api/agent/status', '/api/agent/status', '/api/agent/chat?since=0']);
  assert.ok(calls.every((c) => c.method === 'GET' && c.cred === 'same-origin' && Object.keys(c.headers).length === 0));
  assert.deepStrictEqual(timers.map((t) => t.ms), [1000]);
  assert.strictEqual(byId('ag-meta').textContent, L.metaText(world.st));
  const lis = walk(byId('ag-chat'), (n) => n.tagName === 'li');
  assert.deepStrictEqual(lis.map((n) => n.className), ['ag-m user', 'ag-m assistant', 'ag-m tool bad', 'ag-m tool', 'ag-m system', 'ag-m weird']);
  assert.deepStrictEqual(lis.map((n) => n.children[0].textContent), ['Sen', 'Ajan', 'Araç', 'Araç', 'Sistem', 'weird']);
  assert.strictEqual(byId('ag-model').value, 'gpt-x'); assert.strictEqual(byId('ag-lim').max, 100000); assert.strictEqual(byId('ag-mot').max, 20);
  assert.strictEqual(byId('ag-text').maxLength, 2000);
  assert.strictEqual(byId('ag-keystate').textContent, L.keyText(world.st));
  assert.strictEqual(byId('ag-go').disabled, false); assert.strictEqual(byId('ag-stop').disabled, true);
  calls = []; AgentPanel.mount(null); await settle(); assert.strictEqual(calls.length, 0);

  /* durum mesajı: ajanı ilgilendirmiyorsa / imza aynıysa istek yok; imza değişince yalnız yeni mesajlar (since=6) */
  AgentPanel.onState({}); AgentPanel.onState({ agent: null }); await settle(); assert.strictEqual(calls.length, 0);
  AgentPanel.onState(agentState(6)); await settle();
  calls = []; AgentPanel.onState(agentState(6)); await settle(); assert.strictEqual(calls.length, 0);
  world.msgs.push({ id: 7, role: 'assistant', text: 'Eksen 1 +10°' });
  world.st = fresh({ chat_seq: 7, run: { active: true }, approvals: [{ id: 'ap/1', description: 'Eksen 1 +10°', expires_in_s: 29.2 }] });
  AgentPanel.onState(agentState(7, 1, 1)); await settle();
  assert.deepStrictEqual(calls.map((c) => c.p), ['/api/agent/status', '/api/agent/chat?since=6']);
  assert.strictEqual(walk(byId('ag-chat'), (n) => n.tagName === 'li').length, 7);                       // eskiler korunur + 1 yeni
  assert.strictEqual(byId('ag-go').disabled, true); assert.strictEqual(byId('ag-stop').disabled, false);   // çalışırken gönder kapalı, durdur açık

  /* onay: yeni onayda panel öne kayar + bildirim BİR kez; geri sayım 1 sn'de bir; aynı onay yeniden kaydırmaz */
  assert.strictEqual(byId('p-agent').scrolled, 1); assert.deepStrictEqual(toasts, [['Ajan hareket için onay bekliyor', false]]);
  const left = walk(byId('ag-appr'), (n) => n.className === 'ag-left')[0];
  assert.strictEqual(left.textContent, '30 sn'); timers[0].f(); assert.strictEqual(left.textContent, '29 sn'); timers[0].f(); assert.strictEqual(left.textContent, '28 sn');
  AgentPanel.onState(agentState(7, 1, 1, true)); await settle();
  assert.strictEqual(byId('p-agent').scrolled, 1); assert.strictEqual(toasts.length, 1);
  calls = []; byText('Onayla').listeners.click[0](); await settle();
  assert.deepStrictEqual([posts()[0].p, posts()[0].body, posts()[0].headers], ['/api/agent/approvals/ap%2F1', '{"approve":true}',
    { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok1' }]);
  assert.deepStrictEqual(calls.slice(1).map((c) => c.p), ['/api/agent/status', '/api/agent/chat?since=7']);   // karardan sonra yenilenir
  world.fail['POST /api/agent/approvals/ap%2F1'] = { status: 409, body: { message: 'Onay süresi dolmuş' } }; calls = [];
  byText('Reddet').listeners.click[0](); await settle();
  assert.strictEqual(posts()[0].body, '{"approve":false}'); assert.deepStrictEqual(toasts.pop(), ['Onay süresi dolmuş', true]);
  assert.ok(calls.some((c) => c.p === '/api/agent/status'));                                          // hata olsa da yenilenir
  delete world.fail['POST /api/agent/approvals/ap%2F1'];

  /* gönder: kırpılır, başarıda kutu boşalır; boşsa istek yok; hatada metin KALIR + kırmızı bildirim */
  world.st = fresh({ chat_seq: 7 }); const inp = byId('ag-text'); const form = byId('ag-send'); let pd = 0; const ev = { preventDefault() { pd += 1; } };
  inp.value = '  merhaba  '; calls = []; form.listeners.submit[0](ev); await settle();
  assert.strictEqual(posts()[0].p, '/api/agent/message'); assert.strictEqual(posts()[0].body, '{"text":"merhaba"}'); assert.strictEqual(inp.value, '');
  inp.value = '   '; calls = []; form.listeners.submit[0](ev); await settle(); assert.strictEqual(calls.length, 0); assert.strictEqual(pd, 2);   // form hep engellenir
  inp.value = 'tekrar'; world.fail['POST /api/agent/message'] = { status: 429, body: { message: 'Günlük sınır doldu' } };
  form.listeners.submit[0](ev); await settle(); assert.strictEqual(inp.value, 'tekrar'); assert.deepStrictEqual(toasts.pop(), ['Günlük sınır doldu', true]);
  delete world.fail['POST /api/agent/message'];

  /* durdur: gövde {}; gövdesiz hata yanıtı "HTTP n" bildirimi verir */
  calls = []; byText('Ajanı durdur').listeners.click[0](); await settle();
  assert.deepStrictEqual([posts()[0].p, posts()[0].body], ['/api/agent/cancel', '{}']);
  world.fail['POST /api/agent/cancel'] = { status: 500, noBody: true }; byText('Ajanı durdur').listeners.click[0](); await settle();
  assert.deepStrictEqual(toasts.pop(), ['HTTP 500', true]); delete world.fail['POST /api/agent/cancel'];

  /* ayarlar: gövde anahtar sırası sabit, sayılar tamsayı; ardından zorla yenileme (since=0 ve form yeniden) */
  byId('ag-prov').value = 'anthropic'; byId('ag-model').value = 'claude-y'; byId('ag-url').value = 'https://x.example';
  byId('ag-lim').value = '250'; byId('ag-mot').value = '5abc'; byId('ag-dry').checked = false;
  world.st = fresh({ provider: 'anthropic', model: 'claude-y', dry_run: false, limits: { daily_limit: 250, per_turn_motions: 5, used_today: 8 } });
  calls = []; byText('Ayarları kaydet').listeners.click[0](); await settle();
  assert.strictEqual(posts()[0].body, '{"provider":"anthropic","model":"claude-y","base_url":"https://x.example","daily_limit":250,"max_motions":5,"dry_run":false}');
  assert.deepStrictEqual(toasts.pop(), ['Ajan ayarları kaydedildi', false]);
  assert.ok(calls.some((c) => c.p === '/api/agent/chat?since=0'));
  assert.strictEqual(byId('ag-model').value, 'claude-y'); assert.ok(byId('ag-meta').textContent.startsWith('anthropic / claude-y · bugün 8/250 istek'));

  /* anahtar: kutu istekten ÖNCE boşalır; uyarı kırmızı; boşsa istek yok; silme gövdesi; yazarken form yeniden kurulmaz */
  byId('ag-key').value = 'sk-secret'; byId('ag-rem').checked = true; world.keyReply = { ok: true, warning: 'Anahtar yalnız bellekte tutulacak' };
  calls = []; byText('Anahtarı ayarla').listeners.click[0]();
  assert.strictEqual(byId('ag-key').value, ''); await settle();
  assert.strictEqual(posts()[0].body, '{"provider":"anthropic","key":"sk-secret","remember":true}');
  assert.deepStrictEqual(toasts.pop(), ['Anahtar yalnız bellekte tutulacak', true]);
  byId('ag-key').value = 'sk-2'; world.keyReply = { ok: true }; byText('Anahtarı ayarla').listeners.click[0](); await settle();
  assert.deepStrictEqual(toasts.pop(), ['Anahtar ayarlandı', false]);
  byId('ag-key').value = ''; calls = []; byText('Anahtarı ayarla').listeners.click[0](); await settle(); assert.strictEqual(calls.length, 0);
  calls = []; byText('Anahtarı sil').listeners.click[0](); await settle();
  assert.deepStrictEqual([posts()[0].p, posts()[0].body], ['/api/agent/key/clear', '{"provider":"anthropic","forget":true}']);
  const modelBox = byId('ag-model'); byId('ag-key').value = 'yarim'; world.st = fresh({ model: 'degisti', chat_seq: 8 });
  AgentPanel.onState(agentState(8)); await settle();
  assert.strictEqual(byId('ag-model'), modelBox); assert.strictEqual(byId('ag-key').value, 'yarim');       // form olduğu gibi
  byId('ag-key').value = '';

  /* yetki / oturum: kontrol yetkisi yok ya da snap yok → gönder kapalı; ağ koparsa refresh sessiz kalır */
  S.snap = { me: { can_control: false } }; world.st = fresh({ chat_seq: 9 }); AgentPanel.onState(agentState(9)); await settle();
  assert.strictEqual(byId('ag-go').disabled, true);
  S.snap = null; world.st = fresh({ chat_seq: 10 }); AgentPanel.onState(agentState(10)); await settle(); assert.strictEqual(byId('ag-go').disabled, true);
  S.snap = { me: { can_control: true } }; world.st = fresh({ chat_seq: 11 }); AgentPanel.onState(agentState(11)); await settle(); assert.strictEqual(byId('ag-go').disabled, false);
  toasts = []; world.down = true; AgentPanel.onState(agentState(12)); await settle(); assert.deepStrictEqual(toasts, []); world.down = false;

  /* telefondan (yalnız okuma): anahtar/ayar formu YOK, açıklama var */
  panels.replaceChildren(); delete global.AgentPanel; delete require.cache[require.resolve(path.join(WEB, 'agent.js'))];
  require(path.join(WEB, 'agent.js')); world.st = fresh({ can_configure: false }); AgentPanel.mount(null); await settle();
  assert.strictEqual(byId('ag-key'), null); assert.strictEqual(byId('ag-setkey'), null);
  assert.match(byId('ag-form').textContent, /telefondan girilemez/);

  /* ---------- C) pages/agent.js: kayıt, betik sırası, available() ---------- */
  let def = null; global.HubPages = { register: (d) => { def = d; } };
  require(path.join(WEB, 'pages', 'agent.js'));
  assert.deepStrictEqual([def.id, def.title, def.order], ['agent', 'Ajan', 30]);
  assert.deepStrictEqual(def.scripts, ['/agent_logic.js', '/agent.js']);                                  // mantık, panelden ÖNCE
  const seen = []; const ask = (res) => { global.fetch = async (p, o) => { seen.push([p, o]); if (res instanceof Error) throw res; return res; }; return def.available(); };
  assert.strictEqual(await ask({ ok: true, json: async () => ({ enabled: true }) }), true);
  assert.deepStrictEqual(seen[0], ['/api/agent/status', { credentials: 'same-origin' }]);
  assert.strictEqual(await ask({ ok: true, json: async () => ({ enabled: false }) }), false);
  assert.strictEqual(await ask({ ok: true, json: async () => ({ enabled: 'evet' }) }), false);              // yalnız tam true
  assert.strictEqual(await ask({ ok: false, json: async () => ({ enabled: true }) }), false);
  assert.strictEqual(await ask(new Error('ağ yok')), false);
  const sent = []; global.AgentPanel = { mount: (r) => sent.push(['mount', r]), onState: (s) => sent.push(['state', s]) };
  def.mount('KOK'); def.onState({ a: 1 }); assert.deepStrictEqual(sent, [['mount', 'KOK'], ['state', { a: 1 }]]);

  console.log('AGENT_LOGIC_OK');
})().catch((e) => { console.error(e); process.exit(1); });
