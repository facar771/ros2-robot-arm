/* Program sayfasının SAF mantığı (R12): DOM'suz program_logic.js. pytest çağırır (tests/test_program_logic.py).
 *   node program_logic_check.js <web dizini>
 * Sayfanın kendisi (sahte DOM üzerinde): program_page_check.js. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const WEB = path.resolve(process.argv[2]);
const SRC = fs.readFileSync(path.join(WEB, 'program_logic.js'), 'utf8');

/* ---------- A) DOM/ağ/saat OLMADAN yüklenir ve çalışır (boş vm bağlamı) ---------- */
global.window = global;
const L = require(path.join(WEB, 'program_logic.js'));                                // module.exports = window.ProgramLogic (jog_logic.js deseni)
const bare = { window: {} }; vm.createContext(bare); vm.runInContext(SRC, bare);
assert.deepStrictEqual(Object.keys(bare.window.ProgramLogic), Object.keys(L));
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');           // yorumlar çıkarılır, yalnız kod taranır
assert.doesNotMatch(CODE, /\b(document|fetch|setInterval|setTimeout|localStorage|innerHTML|Date)\b|\bel\(|\bapi\(/);   // DOM'suz sözü korunur
const clone = (o) => JSON.parse(JSON.stringify(o));

/* ---------- B) sayı ayrıştırma, adlar, silinmiş waypoint, Çalıştır kapısı, satır renkleri ---------- */
assert.deepStrictEqual(L.nums('1, 2  3', 3, 3), [1, 2, 3]);
for (const bad of ['', '1,2', '1,2,x', 'NaN,1,2', 'Infinity,1,2', '1,2,3,4']) assert.strictEqual(L.nums(bad, 3, 3), null, bad);
assert.strictEqual(L.unique('Nokta 3', ['Nokta 1', 'Nokta 2']), 'Nokta 3');
assert.strictEqual(L.unique('A', []), 'A 1');
assert.strictEqual(L.unique('Nokta', ['Nokta 1', 'Nokta 2']), 'Nokta 3');
assert.strictEqual(L.fmt([1.23456, 2, 0.5], 2), '1.23, 2, 0.5'); assert.strictEqual(L.fmt(null), '');
assert.deepStrictEqual([L.key(3, null), L.key(3, undefined), L.key(3, 0)], ['3', '3', '3.0']);
assert.deepStrictEqual(L.blank(), { version: 1, name: '', waypoints: [], steps: [] });
assert.notStrictEqual(L.blank(), L.blank(), 'her çağrı yeni nesne');
const prog = { version: 1, name: 'x', waypoints: [{ name: 'A', joints_deg: [0] }], steps: [
  { kind: 'move_j', waypoint: 'A' }, { kind: 'loop', count: 2, steps: [{ kind: 'move_j', waypoint: 'SİLİNMİŞ' }] }] };
assert.deepStrictEqual([...L.dangling(prog).keys()], ['1.0']);
assert.strictEqual(L.dangling(prog).get('1.0'), "waypoint yok: 'SİLİNMİŞ'");
const seen = []; L.walk(prog, (s, k) => seen.push(k)); assert.deepStrictEqual(seen, ['0', '1', '1.0'], 'döngü gövdesi de gezilir');
assert.ok(L.empty({ steps: [] }) && L.empty({ steps: [{ kind: 'comment' }, { kind: 'loop', steps: [{ kind: 'comment' }] }] }));
assert.ok(!L.empty({ steps: [{ kind: 'wait', s: 1 }] }) && !L.empty({ steps: [{ kind: 'loop', steps: [{ kind: 'wait' }] }] }));
assert.strictEqual(L.sig({ a: 1 }, 25), '[{"a":1},25]');
assert.strictEqual(L.total([{ kind: 'wait' }, { kind: 'loop', steps: [{}, {}] }]), 4, 'döngü gövdesi sayılır');
const arr = [1, 2, 3];
assert.strictEqual(L.swap(arr, 0, -1), false); assert.strictEqual(L.swap(arr, 2, 1), false); assert.deepStrictEqual(arr, [1, 2, 3], 'sınır dışı: değişmez');
assert.strictEqual(L.swap(arr, 0, 1), true); assert.deepStrictEqual(arr, [2, 1, 3]);
assert.deepStrictEqual(L.path([{ pos_m: [1, 2, 3] }, { joints_deg: [0] }, { pos_m: [4, 5, 6] }]), [{ n: 1, p: [1, 2, 3] }, { n: 3, p: [4, 5, 6] }], 'konumsuz waypoint atlanır, numara yerinde kalır');

const ok = { prog: { steps: [{ kind: 'wait', s: 1 }], waypoints: [] }, check: { sig: 's', res: { ok: true } }, sig: 's', running: false, estop: false, canControl: true };
assert.deepStrictEqual(L.gate(ok), { ok: true, why: '' });
const why = (o) => { const g = L.gate({ ...ok, ...o }); assert.strictEqual(g.ok, false); return g.why; };
assert.match(why({ canControl: false }), /kilitli/);
assert.match(why({ estop: true }), /ACİL DUR/);
assert.match(why({ running: true }), /çalışıyor/);
assert.match(why({ prog: { steps: [], waypoints: [] } }), /boş/);
assert.match(why({ prog }), /Silinmiş waypoint/);
assert.match(why({ check: null }), /Önizle/);
assert.match(why({ sig: 'değişti' }), /Önizle/);                      // düzenleme sonrası önizleme geçersiz
assert.match(why({ check: { sig: 's', res: { ok: false, violations: 3 } } }), /3 ihlal/);
assert.strictEqual(why({ canControl: false, estop: true }), 'Kontroller kilitli', 'kapı sırası: kilit önce');
const info = L.rowInfo({ dangling: new Map(), run: null, check: { res: { rows: [{ line: 0, sub: null, ok: false, violations: [{ message: 'kutu dışı' }] },
  { line: 1, sub: 0, ok: true, violations: [] }] } } }, '0');
assert.deepStrictEqual(info, { cls: 'bad', msgs: ['kutu dışı'] });
assert.strictEqual(L.rowInfo({ dangling: new Map(), run: { running: true, line: 1, sub: 0 }, check: null }, '1.0').cls, 'run');
assert.match(L.rowInfo({ dangling: new Map(), check: null, run: { error: { line: 2, sub: null, message: 'reddedildi' } } }, '2').msgs[0], /reddedildi/);
assert.strictEqual(L.rowInfo({ dangling: new Map(), check: { res: { rows: [{ line: 1, sub: 0, ok: true, violations: [] }] } }, run: null }, '1.0').cls, 'ok');
assert.deepStrictEqual(L.rowInfo({ dangling: new Map([['0', 'waypoint yok: x']]), check: { res: { rows: [{ line: 0, sub: null, ok: true, violations: [] }] } }, run: null }, '0'),
  { cls: 'bad', msgs: ['waypoint yok: x'] }, 'silinmiş waypoint, geçen önizlemeye rağmen kırmızı');
assert.match(L.status({ state: 'failed', error: { line: 2, sub: 1, message: 'x' } }), /satır 3\.2/);
assert.strictEqual(L.status({ state: 'idle' }), ''); assert.strictEqual(L.status(null), '');
assert.strictEqual(L.status({ state: 'running', running: true, say: 'git', done: 1, total: 4, iteration: 2, pause_reason: 'x' }), 'git · 1/4 · tur 2 · x');
const rn = clone(prog); L.rename(rn, 'A', 'B'); assert.strictEqual(rn.steps[0].waypoint, 'B');

/* ---------- C) önizleme metni/sınıfı, düğme kapalılıkları, üst bilgi metinleri ---------- */
const good = { res: { ok: true, rows: [1, 2] } }, red = { res: { ok: false, violations: 2, rows: [] } };
assert.strictEqual(L.prevText(null, ''), 'Önizleme yok / program değişti'); assert.strictEqual(L.prevText(good, 'Denetleniyor…'), 'Denetleniyor…');
assert.strictEqual(L.prevText(good, ''), '✓ Önizleme geçti (2 hareket denetlendi)'); assert.strictEqual(L.prevText(red, ''), '✗ 2 ihlal: kırmızı satırlara bak');
assert.deepStrictEqual([null, good, red].map(L.prevClass), ['pg-prev ', 'pg-prev ok', 'pg-prev bad']);
const gOk = { ok: true, why: '' }, gNo = { ok: false, why: 'Program boş' };
const btns = (o) => L.buttons({ run: null, control: true, gate: gOk, empty: false, ...o });
assert.deepStrictEqual(btns({}), { ro: false, run: false, step: false, prev: false, pause: true, resume: true, stop: true, gate: 'Önizleme geçti: çalıştırılabilir.' });
assert.deepStrictEqual(btns({ gate: gNo, empty: true }), { ro: false, run: true, step: true, prev: true, pause: true, resume: true, stop: true, gate: 'Program boş' });
const running = btns({ run: { running: true, state: 'running' }, gate: gNo });
assert.deepStrictEqual([running.ro, running.run, running.step, running.prev, running.pause, running.resume, running.stop, running.gate], [true, true, true, true, false, true, false, ''], 'çalışırken: yalnız Duraklat/Durdur açık, düzenleme kapalı');
const paused = btns({ run: { running: true, state: 'paused' }, gate: gNo });
assert.deepStrictEqual([paused.step, paused.pause, paused.resume, paused.stop], [false, true, false, false], 'duraklatılmış: Adım/Devam/Durdur açık');
const locked = btns({ control: false, run: { running: true, state: 'running' } });
assert.deepStrictEqual([locked.ro, locked.step, locked.pause, locked.stop], [true, true, true, true], 'kontrol kilitliyse hiçbir işlem açılmaz');
assert.strictEqual(btns({ control: false }).ro, true);
assert.deepStrictEqual([L.dirtyText(true, 'a'), L.dirtyText(false, 'a'), L.dirtyText(false, null)], ['● kaydedilmemiş', 'kayıtlı: a', '']);
assert.deepStrictEqual([L.exportLink('a', false), L.exportLink('a', true), L.exportLink(null, false)],
  [{ hidden: false, href: '/api/program/a/export' }, { hidden: true, href: '/api/program/a/export' }, { hidden: true, href: '' }], 'kaydedilmemiş program indirtilmez');
assert.deepStrictEqual(L.mockBanner({ label: 'SAHTE (mock)', mock: true }), { hidden: false, text: 'SAHTE (mock) hareket servisi: ölçüm değildir, gerçek robot hareket etmez.' });
assert.strictEqual(L.mockBanner({ label: 'SİM', mock: false }).hidden, true); assert.strictEqual(L.mockBanner(null).hidden, true);
assert.deepStrictEqual(L.fromState({ estop: { latched: true }, robot: { mode: 'real' }, me: { can_control: true } }), { estop: true, real: true, control: true });
assert.deepStrictEqual(L.fromState({}), { estop: false, real: false, control: false }, 'eksik durum: kilitli kabul edilir');
assert.strictEqual(L.listLabel({ name: 'P', steps: 3 }), 'P (3 adım)'); assert.strictEqual(L.listLabel({ name: 'P' }), 'P (? adım)');
assert.deepStrictEqual([999999, 1000000, 1000001].map(L.fileTooBig), [false, false, true]);
assert.strictEqual(L.whyOf({ message: 'm', body: { errors: [{ path: 'steps.0', message: 'kötü' }] } }), 'm: steps.0: kötü');
assert.strictEqual(L.whyOf({ message: 'm', body: {} }), 'm'); assert.strictEqual(L.whyOf({ message: 'm' }), 'm');

/* ---------- D) alan doğrulama ---------- */
assert.deepStrictEqual(L.numCheck('5', 1, 1, 0, 10), [5]); assert.strictEqual(L.numCheck('11', 1, 1, 0, 10), null); assert.strictEqual(L.numCheck('-1', 1, 1, 0, 10), null);
assert.deepStrictEqual(L.numCheck('1, 99', 1, 8, 0, 10), [1, 99], 'aralık yalnız İLK değere uygulanır'); assert.deepStrictEqual(L.numCheck('1 2 3', 3, 3), [1, 2, 3]);
assert.strictEqual(L.numCheck('x', 1, 1, 0, 10), null); assert.strictEqual(L.numCheck('NaN', 1, 1), null);
assert.deepStrictEqual([L.vec('  ', 1, 8), L.vec('', 1, 8), L.vec('1,2', 1, 8), L.vec('a', 1, 8)], [[], [], [1, 2], null]);
const names = ['A', 'B'];
assert.strictEqual(L.nameBad('C', names, 0), false); assert.strictEqual(L.nameBad('A', names, 0), false, 'kendi adı serbest');
assert.strictEqual(L.nameBad('B', names, 0), true, 'başkasının adı yasak'); assert.strictEqual(L.nameBad('', names, 0), true);
assert.strictEqual(L.nameBad('-x', names, 0), true); assert.strictEqual(L.nameBad('x'.repeat(40), names, 0), false); assert.strictEqual(L.nameBad('x'.repeat(41), names, 0), true);
assert.strictEqual(L.nameBad('Çıkış 1', names, 0), false, 'Türkçe harf serbest');
assert.deepStrictEqual(L.kinds(false), ['move_j', 'move_l', 'wait', 'tool', 'comment', 'loop']); assert.deepStrictEqual(L.kinds(true), ['move_j', 'move_l', 'wait', 'tool', 'comment']);
assert.strictEqual(L.KIND.loop, 'Döngü');
assert.deepStrictEqual(L.wpOptions(['A'], 'A'), [{ value: '', text: '— seç —' }, { value: 'A', text: 'A' }]);
assert.deepStrictEqual(L.wpOptions(['A'], 'X'), [{ value: '', text: '— seç —' }, { value: 'A', text: 'A' }, { value: 'X', text: 'X (YOK)' }], 'silinmiş waypoint görünür kalır');
assert.strictEqual(L.wpOptions(['A'], '').length, 2);

/* ---------- E) model düzenleme ---------- */
const pose = { joints_deg: [1, 2, 3, 4, 5, 6], pos_m: [0.1, 0.2, 0.3], rpy_deg: [180, 0, 0], open_pct: 70 };
const wp = (o) => ({ waypoints: [{ name: 'W', ...o }] });
assert.deepStrictEqual(L.newStep('wait', L.blank(), null), { kind: 'wait', s: 1 });
assert.deepStrictEqual(L.newStep('comment', L.blank(), null), { kind: 'comment', text: '' });
assert.deepStrictEqual(L.newStep('loop', L.blank(), null), { kind: 'loop', count: 2, steps: [] });
assert.deepStrictEqual(L.newStep('tool', L.blank(), null), { kind: 'tool', open_pct: 50 }); assert.deepStrictEqual(L.newStep('tool', L.blank(), pose), { kind: 'tool', open_pct: 70 });
assert.deepStrictEqual(L.newStep('move_j', L.blank(), null), { kind: 'move_j', joints_deg: [0, 0, 0, 0, 0, 0] }, 'poz yoksa sıfır');
assert.deepStrictEqual(L.newStep('move_j', L.blank(), pose), { kind: 'move_j', joints_deg: [1, 2, 3, 4, 5, 6] });
assert.deepStrictEqual(L.newStep('move_j', wp({ joints_deg: [0] }), pose), { kind: 'move_j', waypoint: 'W' }, 'son waypoint eklem taşıyorsa ona bağlanır');
assert.deepStrictEqual(L.newStep('move_j', wp({ pos_m: [0, 0, 0] }), pose), { kind: 'move_j', joints_deg: [1, 2, 3, 4, 5, 6] }, 'eklemsiz waypoint: değer');
assert.deepStrictEqual(L.newStep('move_l', L.blank(), null), { kind: 'move_l', pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] });
assert.deepStrictEqual(L.newStep('move_l', L.blank(), pose), { kind: 'move_l', pos_m: [0.1, 0.2, 0.3], rpy_deg: [180, 0, 0] });
assert.deepStrictEqual(L.newStep('move_l', wp({ pos_m: [0, 0, 0] }), pose), { kind: 'move_l', waypoint: 'W', offset_m: [0, 0, 0] });
const st = L.newStep('move_j', L.blank(), pose); st.joints_deg[0] = 99; assert.strictEqual(pose.joints_deg[0], 1, 'poz KOPYALANIR');
const z1 = L.newStep('move_j', L.blank(), null); z1.joints_deg[0] = 5; assert.strictEqual(L.newStep('move_j', L.blank(), null).joints_deg[0], 0, 'sıfır dizisi paylaşılmaz');

const mv = (o) => ({ kind: 'move_l', waypoint: 'A', offset_m: [1, 1, 1], orientation: 'free', ...o });
const pg = { waypoints: [{ name: 'A', pos_m: [0.4, 0, 0.2], rpy_deg: [0, 90, 0], joints_deg: [9, 8, 7, 6, 5, 4] }] };
let s1 = mv({}); L.retarget(s1, 'val', pg, pose);
assert.deepStrictEqual(s1, { kind: 'move_l', pos_m: [0.4, 0, 0.2], rpy_deg: [0, 90, 0] }, 'waypoint -> değer: waypoint değerleri alınır, serbest yönelim ve ofset silinir');
s1 = mv({ waypoint: 'YOK' }); L.retarget(s1, 'val', pg, pose);
assert.deepStrictEqual(s1, { kind: 'move_l', pos_m: [0.1, 0.2, 0.3], rpy_deg: [180, 0, 0] }, 'silinmiş waypoint: robotun şu anki pozu');
s1 = mv({ waypoint: 'YOK' }); L.retarget(s1, 'val', pg, null); assert.deepStrictEqual(s1.pos_m, [0, 0, 0]);
s1 = { kind: 'move_j', waypoint: 'A' }; L.retarget(s1, 'val', pg, pose); assert.deepStrictEqual(s1, { kind: 'move_j', joints_deg: [9, 8, 7, 6, 5, 4] });
s1 = { kind: 'move_j', joints_deg: [1, 1, 1, 1, 1, 1] }; L.retarget(s1, 'wp', pg, pose); assert.deepStrictEqual(s1, { kind: 'move_j', waypoint: 'A' }, 'değer -> waypoint: ilk waypoint');
s1 = { kind: 'move_l', pos_m: [1, 1, 1], rpy_deg: [0, 0, 0], orientation: 'free' }; L.retarget(s1, 'wp', pg, pose);
assert.deepStrictEqual(s1, { kind: 'move_l', waypoint: 'A', offset_m: [0, 0, 0] }, 'serbest yönelim bayrağı silinir');
s1 = { kind: 'move_l', pos_m: [1, 1, 1] }; L.retarget(s1, 'wp', { waypoints: [] }, pose); assert.deepStrictEqual(s1, { kind: 'move_l', waypoint: '', offset_m: [0, 0, 0] }, 'waypoint yoksa boş ad');

assert.deepStrictEqual(L.poseWaypoint(pose, ['Nokta 1']), { name: 'Nokta 2', joints_deg: [1, 2, 3, 4, 5, 6], pos_m: [0.1, 0.2, 0.3], rpy_deg: [180, 0, 0] });
assert.deepStrictEqual(L.poseWaypoint({ joints_deg: [1.23456789], pos_m: [0.123456789, 0, 0], rpy_deg: [1.23456789, 0, 0] }, []),
  { name: 'Nokta 1', joints_deg: [1.235], pos_m: [0.12346, 0, 0], rpy_deg: [1.235, 0, 0] }, 'eklem 3, konum 5, yönelim 3 hane');
assert.deepStrictEqual(L.poseWaypoint({ joints_deg: [1] }, []), { name: 'Nokta 1', joints_deg: [1] });
assert.deepStrictEqual(L.poseWaypoint({ pos_m: [1, 2, 3], rpy_deg: [0, 0, 0] }, []), { name: 'Nokta 1', pos_m: [1, 2, 3], rpy_deg: [0, 0, 0] });
assert.strictEqual(L.poseWaypoint({ pos_m: [1, 2, 3] }, []), null, 'yönelimsiz konum okunamadı sayılır'); assert.strictEqual(L.poseWaypoint(null, []), null);

const FREE = { kind: 'move_l', pos_m: [0.1, 0, 0.08], orientation: 'free' }, P0 = () => ({ version: 1, name: '', waypoints: [], steps: [] });
let pr = P0(); let r = L.addSteps(pr, [FREE, { kind: 'loop', steps: [{}, {}] }], '  Daire yay  ', false);
assert.deepStrictEqual(r, { ok: true, total: 4 }); assert.strictEqual(pr.name, 'Daire yay', 'adsız programa ad: kırpılmış'); assert.deepStrictEqual(pr.steps[0], FREE);
assert.notStrictEqual(pr.steps[0], FREE, 'adımlar KOPYALANIR');
L.addSteps(pr, [{ kind: 'wait', s: 1 }], 'Başka', false); assert.strictEqual(pr.name, 'Daire yay', 'dolu ad ezilmez');
pr = P0(); L.addSteps(pr, [FREE], 42, false); assert.strictEqual(pr.name, '', 'dize olmayan ad yok sayılır');
pr = P0(); L.addSteps(pr, [FREE], 'x'.repeat(60), false); assert.strictEqual(pr.name.length, 40, 'ad 40 karakterle sınırlı');
pr = P0(); const snapshot = clone(pr);
assert.deepStrictEqual(L.addSteps(pr, [], 'x', false), { ok: false, message: 'Eklenecek adım yok' }); assert.strictEqual(L.addSteps(pr, 'x', 'x', false).ok, false);
assert.deepStrictEqual(L.addSteps(pr, [FREE], 'x', true), { ok: false, message: 'Program çalışıyor: bitince ekle' });
assert.deepStrictEqual(pr, snapshot, 'reddedilen ekleme modeli DEĞİŞTİRMEZ');
pr.steps = Array.from({ length: 3 }, () => ({ kind: 'wait' }));
r = L.addSteps(pr, Array.from({ length: 498 }, () => ({ kind: 'wait', s: 1 })), '', false);
assert.strictEqual(r.ok, false); assert.strictEqual(r.message, 'En çok 500 adım (şu an 3)'); assert.strictEqual(pr.steps.length, 3);
assert.strictEqual(L.addSteps(pr, Array.from({ length: 497 }, () => ({ kind: 'wait', s: 1 })), '', false).total, 500, 'tam 500 kabul');
pr = P0(); assert.strictEqual(L.addSteps(pr, [{ kind: 'loop', steps: Array(499).fill({}) }], '', false).total, 500, 'döngü gövdesi sınıra sayılır: 1 + 499 = 500 kabul');
pr = P0(); assert.strictEqual(L.addSteps(pr, [{ kind: 'loop', steps: Array(500).fill({}) }], '', false).ok, false, '1 + 500 = 501 reddedilir');

/* ---------- F) hedef öğretme ---------- */
assert.deepStrictEqual(L.roundPos([0.123456, 1, 2, 9]), [0.1235, 1, 2], '3 değer, 4 hane'); assert.deepStrictEqual(L.roundRpy([1.234, 2, 3.999, 7]), [1.23, 2, 4], '3 değer, 2 hane');
assert.deepStrictEqual(L.targetVerdict({ ok: true, violations: [] }), { ok: true, why: 'Ulaşılabilir' });
assert.deepStrictEqual(L.targetVerdict({ ok: false, violations: [{ message: 'kutu dışı' }] }), { ok: false, why: 'kutu dışı' });
assert.deepStrictEqual(L.targetVerdict({ ok: false }), { ok: false, why: 'Reddedildi' });
assert.deepStrictEqual(L.targetVerdict({ failed: true, message: 'ağ yok', ok: true }), { ok: false, why: 'ağ yok' }, 'hata yanıtı asla "ulaşılabilir" olmaz');
assert.deepStrictEqual(L.targetVerdict({ ok: 'true' }), { ok: false, why: 'Reddedildi' }, 'yalnız gerçek true kabul');
assert.deepStrictEqual(L.targetUi({ ok: true, why: 'Ulaşılabilir' }, true), { cls: 'pg-dot ok', why: 'Ulaşılabilir', addDisabled: false });
assert.deepStrictEqual(L.targetUi({ ok: false, why: 'x' }, true), { cls: 'pg-dot bad', why: 'x', addDisabled: true });
assert.deepStrictEqual(L.targetUi({ ok: null, why: 'Denetleniyor…' }, true), { cls: 'pg-dot ', why: 'Denetleniyor…', addDisabled: true });
assert.strictEqual(L.targetUi({ ok: true, why: '' }, false).addDisabled, true, 'kontrol kilitliyse hedef eklenemez');
const tw = L.targetWaypoint({ pos: [1, 2, 3], rpy: [0, 0, 0] }, ['Nokta 1']);
assert.deepStrictEqual(tw, { name: 'Nokta 2', pos_m: [1, 2, 3], rpy_deg: [0, 0, 0] }); assert.deepStrictEqual(Object.keys(tw), ['name', 'pos_m', 'rpy_deg']);

/* ---------- G) 3B sahne nesne tanımları (anahtar sırası dâhil) ---------- */
assert.ok(Object.values(L.SC).every((id) => id.startsWith('program:')), 'kimlikler `program:` önekli');
assert.deepStrictEqual(L.PICK, ['workpiece', 'environment']); assert.deepStrictEqual(L.PICK_ALL, ['workpiece', 'tool', 'environment', 'markers']);
assert.strictEqual(JSON.stringify(L.markerSpec({ n: 2, p: [1, 2, 3] })),
  '{"id":"program:wp2","kind":"marker","layer":"markers","pose":{"pos_m":[1,2,3]},"geometry":{"radius_m":0.012,"number":2},"color":"#4f8cff"}');
assert.strictEqual(JSON.stringify(L.pathSpec([{ n: 1, p: [0, 0, 0] }, { n: 2, p: [1, 1, 1] }])),
  '{"id":"program:path","kind":"polyline","layer":"path","geometry":{"points_m":[[0,0,0],[1,1,1]]},"color":"#e3b341"}');
const tpose = { pos_m: [1, 2, 3], rpy_deg: [0, 0, 0] };
assert.strictEqual(JSON.stringify(L.targetSpec(tpose)), '{"id":"program:target","kind":"frame","layer":"markers","pose":{"pos_m":[1,2,3],"rpy_deg":[0,0,0]},"geometry":{"size_m":0.08},"label":"Hedef"}');
assert.strictEqual(L.targetSpec(tpose).pose, tpose, 'poz nesnesi paylaşılır (sürüklerken aynı referans güncellenir)');
console.log('PROGRAM_LOGIC_OK');
