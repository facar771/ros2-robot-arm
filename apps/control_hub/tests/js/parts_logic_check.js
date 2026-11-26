/* Parçalar sayfasının SAF mantığı (R12): DOM'suz parts_logic.js. pytest çağırır (tests/test_parts_logic.py).
 *   node parts_logic_check.js <web dizini>
 * Sayfanın kendisi (sahte DOM üzerinde): parts_page_check.js. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const WEB = path.resolve(process.argv[2]);
const SRC = fs.readFileSync(path.join(WEB, 'parts_logic.js'), 'utf8');

/* ---------- A) DOM/ağ/saat OLMADAN yüklenir (boş vm bağlamı); yalnız SceneLogic'e tembel bağlıdır ---------- */
global.window = global;
require(path.join(WEB, 'scene_logic.js'));
const L = require(path.join(WEB, 'parts_logic.js'));                                  // module.exports = window.PartsLogic (program_logic.js deseni)
const bare = { window: {} }; vm.createContext(bare); vm.runInContext(SRC, bare);
assert.deepStrictEqual(Object.keys(bare.window.PartsLogic), Object.keys(L));
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');           // yorumlar çıkarılır, yalnız kod taranır
assert.doesNotMatch(CODE, /\b(document|fetch|setInterval|setTimeout|localStorage|innerHTML|Date)\b|\bel\(|\bapi\(/);   // DOM'suz sözü korunur
assert.doesNotMatch(CODE, /\b(checkMove|executeMove|\/api\/move)\b/, 'hareket komutu yolu bu dosyada YOK (approach.js)');
const SL = global.SceneLogic, clone = (o) => JSON.parse(JSON.stringify(o));

/* ---------- B) sabitler: varsayılanlar, seçim katmanları, seçenekler ---------- */
assert.strictEqual(L.DEFAULT_D_MM, 50); assert.strictEqual(L.DEFAULT_SPEED_PCT, 10); assert.strictEqual(L.DELETE_ARM_MS, 4000);
assert.deepStrictEqual(L.PICK_LAYERS, ['workpiece', 'environment'], 'takım robota bağlı: hedef olamaz');
assert.deepStrictEqual(L.DEFAULT_PICK, ['workpiece', 'tool', 'environment', 'markers']);
assert.deepStrictEqual(L.POSE_KEYS.map((k) => k[0]), ['x', 'y', 'z', 'r', 'p', 'w']);
assert.deepStrictEqual(L.KIND_OPTS.map((k) => k[0]), ['workpiece', 'tool', 'environment']);
assert.deepStrictEqual(L.UNIT_OPTS.map((k) => k[0]), ['mm', 'in', 'm']);
assert.deepStrictEqual(L.MODE_TEXT.map((k) => k[0]), ['free', 'fixed']); assert.deepStrictEqual(Object.keys(L.ORIENT_TEXT), ['normal', 'down', 'keep']);
assert.deepStrictEqual(L.CAMERA_OPTS.map((k) => k[0]), ['iso', 'top', 'front', 'side']); assert.deepStrictEqual(L.GIZMO_OPTS.map((k) => k[0]), ['translate', 'rotate', 'off']);
assert.strictEqual(L.FILE_ACCEPT, '.stl,.obj,.step,.stp,.iges,.igs');
assert.deepStrictEqual(L.PLAN_IDS, ['parts:ap-frame', 'parts:ap-line', 'parts:ap-ghost', 'parts:ap-tgt'], 'plan düşünce nokta kalır');
assert.ok(Object.values(L.AP).every((id) => id.startsWith('parts:ap-')));
assert.match(L.ASSUMPTION, /VARSAYILIR; bu bir ölçüm değildir/);

/* ---------- C) robot durumu: yalnız OKUR; bildirilen TCP ---------- */
assert.ok(L.estopped({ estop: { latched: true } }) && !L.estopped({ estop: { latched: false } }) && !L.estopped(null) && !L.estopped({}));
assert.ok(L.robotStale({ robot: { stale: true } }) && !L.robotStale({ robot: {} }) && !L.robotStale({}) && !L.robotStale(null));
const snap = { robot: { tcp: { pos_m: [0.3, 0, 0.2], rpy_deg: [180, 0, 0] } } };
const tcp = L.reportedTcp(snap); assert.deepStrictEqual(tcp, { pos_m: [0.3, 0, 0.2], rpy_deg: [180, 0, 0] });
tcp.pos_m[0] = 9; assert.strictEqual(snap.robot.tcp.pos_m[0], 0.3, 'kopya döner: robot verisi değişmez');
assert.deepStrictEqual(L.reportedTcp({ robot: { tcp: { position: [1, 2, 3], wpr_deg: [4, 5, 6] } } }), { pos_m: [1, 2, 3], rpy_deg: [4, 5, 6] }, 'eski adlar');
for (const bad of [null, {}, { robot: {} }, { robot: { tcp: { pos_m: [1, 2], rpy_deg: [0, 0, 0] } } }, { robot: { tcp: { pos_m: [1, 2, NaN], rpy_deg: [0, 0, 0] } } },
  { robot: { tcp: { pos_m: [1, 2, 3], rpy_deg: ['0', 0, 0] } } }, { robot: { tcp: { pos_m: [1, 2, 3] } } }]) assert.strictEqual(L.reportedTcp(bad), null, JSON.stringify(bad));
assert.strictEqual(L.tcpBlock(snap), ''); assert.strictEqual(L.tcpBlock({ robot: { ...snap.robot, stale: true } }), 'Robot verisi bayat: TCP konumu alınamadı');
assert.strictEqual(L.tcpBlock({ robot: {} }), 'Robotun TCP konumu bilinmiyor');
assert.strictEqual(L.tcpBlock({ robot: { stale: true } }), 'Robot verisi bayat: TCP konumu alınamadı', 'bayat + bilinmiyor: bayat öncelikli');

/* ---------- D) base64 yardımcısı: parça parça, büyük dosyada yığın taşmaz ---------- */
const big = new Uint8Array(300000).map((_, i) => (i * 7 + 3) % 256);
assert.strictEqual(Buffer.from(L.binaryString(big.buffer), 'latin1').toString('base64'), Buffer.from(big).toString('base64'));
assert.strictEqual(L.binaryString(new Uint8Array([0, 1, 250, 255]).buffer), '\u0000\u0001úÿ'); assert.strictEqual(L.binaryString(new ArrayBuffer(0)), '');

/* ---------- E) sahne eşlemesi ve liste ---------- */
const ID1 = 'a'.repeat(32), ID2 = 'b'.repeat(32), ID3 = 'c'.repeat(32);
assert.strictEqual(L.sceneId({ id: ID1 }), `parts:${ID1}`);
assert.deepStrictEqual(L.staleIds([`parts:${ID1}`, `parts:${ID2}`, 'parts:ap-point', 'program:wp1', 'parts:xyz', `parts:${ID3}`], [{ id: ID1 }, { id: ID3 }]), [`parts:${ID2}`],
  'yalnız listede olmayan PARÇA nesneleri; ap-* ve başkasının nesnelerine dokunulmaz');
assert.deepStrictEqual(L.staleIds([], [{ id: ID1 }]), []);
assert.ok(L.needsRebuild({ layer: 'workpiece', attach: undefined }, { layer: 'tool', attach: 'flange' }) && L.needsRebuild({ layer: 'tool', attach: 'flange' }, { layer: 'tool' }));
assert.ok(!L.needsRebuild({ layer: 'tool', attach: 'flange' }, { layer: 'tool', attach: 'flange' }) && !L.needsRebuild(null, { layer: 'tool' }), 'sahnede yoksa yeniden kurma değil, ilk kurulum');
assert.strictEqual(L.rowSub({ kind: 'workpiece', size_m: [0.06, 0.06, 0.02], triangles: 12 }), 'İş parçası · 60 × 60 × 20 mm · 12 üçgen');
assert.strictEqual(L.rowSub({ kind: 'tool', size_m: [0.03, 0.02, 0.015], triangles: 342 }), 'Takım · 30 × 20 × 15 mm · 342 üçgen');
assert.strictEqual(L.countText(1, { max_parts: 20 }), '1 / 20 parça'); assert.strictEqual(L.countText(0, null), '0 / ? parça'); assert.strictEqual(L.countText(3, {}), '3 / ? parça');
assert.deepStrictEqual(L.listState({}), { parts: [], limits: null, step: null, problems: '' });
assert.deepStrictEqual(L.listState({ parts: [1], limits: { max_parts: 5 }, step: { available: true }, problems: ['a', 'b'] }), { parts: [1], limits: { max_parts: 5 }, step: { available: true }, problems: 'a · b' });
assert.deepStrictEqual(L.saveSay(), ['Kaydedildi', 'ok']); assert.deepStrictEqual(L.saveSay('Not'), ['Not', 'warn']);

/* ---------- F) yükleme: STEP/IGES (S2b) kararları, sınırlar, gövde ---------- */
const OFF = { available: false, message: 'Sunucu: dönüştürücü yok' }, ON = { available: true, notice: 'Bildirim X', timeout_s: 20 };
const stl = { name: 'kare.stl', size: 10 }, step = { name: 'kasa.STEP', size: 10 };
assert.deepStrictEqual(L.fileChoice(null, OFF), { goDisabled: false, unitDisabled: false, say: ['', ''] }, 'dosya yok');
assert.deepStrictEqual(L.fileChoice(stl, ON), { goDisabled: false, unitDisabled: false, say: ['', ''] }, 'STL: birim seçilebilir, ileti yok');
assert.deepStrictEqual(L.fileChoice(step, OFF), { goDisabled: true, unitDisabled: false, say: ['Sunucu: dönüştürücü yok', 'warn'] }, 'dönüştürücü yok: yükle kapalı');
assert.deepStrictEqual(L.fileChoice(step, null).say, ['STEP için dönüştürücü kurulu değil, STL’e çevirip yükle.', 'warn'], 'sunucu bildirmezse yedek metin');
assert.deepStrictEqual(L.fileChoice(step, ON), { goDisabled: false, unitDisabled: true,
  say: ['STEP/IGES: birim dosyadan okunur, parça mm olarak kaydedilir. Dönüşüm en çok 20 sn sürer.', ''] }, 'dönüştürücü var: birim kilitli (mm)');
for (const n of ['a.stp', 'a.IGES', 'a.igs', 'a.Step']) assert.ok(L.fileChoice({ name: n }, ON).unitDisabled, n);
for (const n of ['a.stl', 'a.obj', 'stepfile', 'a.step.stl', '']) assert.ok(!L.fileChoice({ name: n }, ON).unitDisabled, n);
const onNote = L.uploadNote(ON), offNote = L.uploadNote(OFF);
assert.deepStrictEqual(onNote, { title: 'Parça ekle (STL / OBJ / STEP / IGES)', note: 'En çok 50 MB, 500 000 üçgen. STEP/IGES alt süreçte dönüştürülür (en çok 20 sn).',
  licHidden: false, lic: 'Bildirim X. Lisans ve kaynak: docs/THIRD_PARTY.md' });
assert.deepStrictEqual(offNote, { title: 'Parça ekle (STL / OBJ)', note: 'En çok 50 MB, 500 000 üçgen. STEP/IGES için dönüştürücü kurulu değil: CAD’den STL’e çevirip yükle.',
  licHidden: true, lic: '' }, 'dönüştürücü yokken lisans bildirimi görünmez (kullanılmıyor)');
assert.strictEqual(L.uploadNote({ available: true }).lic, 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna). Lisans ve kaynak: docs/THIRD_PARTY.md', 'sunucu bildirmezse yedek LGPL metni');
assert.deepStrictEqual(L.uploadPlan(null), { error: 'Önce bir dosya seç' }); assert.deepStrictEqual(L.uploadPlan(undefined), { error: 'Önce bir dosya seç' });
assert.deepStrictEqual(L.uploadPlan({ name: 'dev.stl', size: 50 * 1048576 + 1 }), { error: 'Dosya 50 MB sınırını aşıyor' });
assert.deepStrictEqual(L.uploadPlan({ name: 'tam.stl', size: 50 * 1048576 }), { busy: 'Yükleniyor…' }, 'tam sınır kabul');
assert.deepStrictEqual(L.uploadPlan(step), { busy: 'Dönüştürülüyor (en çok 20 sn)…' });
assert.deepStrictEqual(Object.keys(L.uploadBody(stl, 'QQ', 'tool', 'in', '')), ['filename', 'data_b64', 'kind', 'unit'], 'ad boşsa gövdede yok');
assert.deepStrictEqual(L.uploadBody(stl, 'QQ', 'tool', 'in', '  Plaka  '), { filename: 'kare.stl', data_b64: 'QQ', kind: 'tool', unit: 'in', name: 'Plaka' });
assert.deepStrictEqual(Object.keys(L.uploadBody(stl, 'QQ', 'tool', 'in', 'x')), ['filename', 'data_b64', 'kind', 'unit', 'name'], 'anahtar sırası korunur');
assert.deepStrictEqual(L.uploadDone({}), ['Yüklendi', 'ok']); assert.deepStrictEqual(L.uploadDone({ warnings: [] }), ['Yüklendi', 'ok']);
assert.deepStrictEqual(L.uploadDone({ warnings: ['a', 'b'] }), ['a · b', 'warn']);

/* ---------- G) seçili parça: görünüm, alanlardan kayıt yaması ---------- */
const wp = { kind: 'workpiece', size_m: [0.04, 0.04, 0.04], triangles: 12, pose: { pos_m: [1, 2, 3], rpy_deg: [0, 0, 0] }, tool_offset: null };
const tl = { ...wp, kind: 'tool', tool_offset: { pos_m: [0, 0, 0.05], rpy_deg: [0, 0, 0] } };
assert.deepStrictEqual(L.selectedView(wp), { title: 'Yerleşim (dünya çerçevesi)', toolNoteHidden: true, tcpHidden: false, size: 'Boyut (ölçekli): 40 × 40 × 40 mm · 12 üçgen', pose: wp.pose });
assert.deepStrictEqual(L.selectedView(tl), { title: 'Takım ofseti (flanşa göre)', toolNoteHidden: false, tcpHidden: true, size: 'Boyut (ölçekli): 40 × 40 × 40 mm · 12 üçgen', pose: tl.tool_offset });
assert.strictEqual(L.poseKey(wp), 'pose'); assert.strictEqual(L.poseKey(tl), 'tool_offset'); assert.strictEqual(L.poseKey({ kind: 'environment' }), 'pose');
assert.strictEqual(L.gimbalText([0, 0, 0]), ''); assert.match(L.gimbalText([0, 90, 0]), /^Pitch ±90° yakınında: roll ve yaw birleşir/);
const F = { x: '12,5', y: '0', z: '30', r: '0', p: '45', w: '0' };
assert.deepStrictEqual(clone(L.readPatch(wp, F, '2', 'in')), { ok: true, rpy: [0, 45, 0], patch: { pose: { pos_m: [0.0125, 0, 0.03], rpy_deg: [0, 45, 0] }, scale: 2, unit: 'in' } });
assert.deepStrictEqual(Object.keys(L.readPatch(wp, F, '2', 'in').patch), ['pose', 'scale', 'unit'], 'anahtar sırası korunur');
assert.deepStrictEqual(Object.keys(L.readPatch(tl, F, '1', 'mm').patch), ['tool_offset', 'scale', 'unit'], 'takımda ofset yazılır');
const bad1 = L.readPatch(wp, { ...F, y: 'abc' }, '1', 'mm'); assert.ok(!bad1.ok); assert.match(bad1.message, /sayı olmalı/); assert.ok(!('patch' in bad1));
const bad2 = L.readPatch(wp, F, '5000', 'mm'); assert.ok(!bad2.ok); assert.match(bad2.message, /Ölçek/);
const bad3 = L.readPatch(wp, { ...F, y: 'abc' }, '5000', 'mm'); assert.strictEqual(bad3.message, bad1.message, 'ikisi de bozuksa konum iletisi öncelikli');

/* ---------- H) dokun-git: kapalılık nedenleri ve düğme durumu (yalnız gösterim; asıl denetim sunucuda) ---------- */
const GOOD = { estop: { latched: false }, me: { can_control: true }, robot: { stale: false } };
assert.strictEqual(L.blockReason(GOOD, true, true), '');
assert.strictEqual(L.blockReason({ ...GOOD, estop: { latched: true } }, true, true), 'ACİL DUR aktif: hareket yok');
assert.strictEqual(L.blockReason({ ...GOOD, me: { can_control: false } }, true, true), 'Bu oturum salt-okunur: hareket yok');
assert.strictEqual(L.blockReason({ ...GOOD, robot: { stale: true } }, true, true), 'Robot verisi bayat: hareket yok');
assert.strictEqual(L.blockReason(GOOD, false, true), '3B görünüm yok: dokunarak hedef seçilemez');
assert.strictEqual(L.blockReason(GOOD, true, false), 'Önce 3B’de parçanın yüzeyine dokun');
assert.strictEqual(L.blockReason(null, true, true), 'Bu oturum salt-okunur: hareket yok', 'durum yoksa salt-okunur sayılır (kapalı)');
assert.strictEqual(L.blockReason({ ...GOOD, estop: { latched: true }, me: { can_control: false } }, false, false), 'ACİL DUR aktif: hareket yok', 'öncelik: ACİL DUR en başta');
const ST = (o = {}) => ({ hasScene: true, hasPick: true, busy: false, hasPlan: false, ...o });
assert.deepStrictEqual(L.approachButtons(GOOD, ST()), { nearOff: false, touchOff: false, okHidden: true, okOff: false, why: '', whyCls: '' });
assert.deepStrictEqual(L.approachButtons(GOOD, ST({ hasPlan: true })), { nearOff: false, touchOff: false, okHidden: false, okOff: false, why: '', whyCls: '' });
assert.deepStrictEqual(L.approachButtons(GOOD, ST({ busy: true, hasPlan: true })), { nearOff: true, touchOff: true, okHidden: false, okOff: true, why: '', whyCls: '' }, 'meşgulken hiçbir düğme çalışmaz');
assert.deepStrictEqual(L.approachButtons(GOOD, ST({ hasPick: false })), { nearOff: true, touchOff: true, okHidden: true, okOff: false, why: 'Önce 3B’de parçanın yüzeyine dokun', whyCls: '' });
const ES = { ...GOOD, estop: { latched: true } };
assert.deepStrictEqual(L.approachButtons(ES, ST({ hasPlan: true })), { nearOff: true, touchOff: true, okHidden: false, okOff: true, why: '', whyCls: 'bad' }, 'ACİL DUR: onay kapalı (plan görünür kalsa bile komut gitmez)');
assert.strictEqual(L.approachButtons(ES, ST()).why, 'ACİL DUR aktif: hareket yok'); assert.strictEqual(L.approachButtons(ES, ST()).whyCls, 'bad');
assert.ok(L.approachButtons({ ...GOOD, me: { can_control: false } }, ST({ hasPlan: true })).okOff, 'salt-okunur: onay kapalı');
assert.ok(L.approachButtons({ ...GOOD, robot: { stale: true } }, ST({ hasPlan: true })).okOff, 'bayat veri: onay kapalı');

/* ---------- I) dokunulan nokta ve hedef için sahne nesneleri ---------- */
assert.ok(L.validPick({ point_m: [1, 2, 3], normal: [0, 0, 1] }));
for (const bad of [null, undefined, {}, { point_m: [1, 2, 3] }, { normal: [0, 0, 1] }, { point_m: [1, 2, NaN], normal: [0, 0, 1] }, { point_m: [1, 2, 3], normal: [0, 1] }]) assert.ok(!L.validPick(bad), JSON.stringify(bad));
assert.strictEqual(L.pickedPartId({ objectId: `parts:${ID1}` }), ID1);
for (const o of ['parts:ap-point', 'program:wp1', 'parts:' + ID1 + 'x', '', undefined]) assert.strictEqual(L.pickedPartId({ objectId: o }), null, String(o));
const pick = { point_m: [0.3, 0.0, 0.1], normal: [0, 0, 1] };
assert.deepStrictEqual(clone(L.pointSpec(pick)), { id: 'parts:ap-point', kind: 'marker', layer: 'markers', geometry: { radius_m: 0.006 }, pose: { pos_m: [0.3, 0, 0.1], rpy_deg: [0, 0, 0] }, color: 0xf0883e, opacity: 1 });
assert.strictEqual(L.pointInfo(pick), 'Nokta (mm): 300 · 0 · 100  ·  yüzey normali: 0 0 1');
assert.strictEqual(L.pointInfo({ point_m: [0.01234, -0.5, 0.002], normal: [0.123, 0.0, 0.992] }), 'Nokta (mm): 12.3 · -500 · 2  ·  yüzey normali: 0.12 0 0.99');
const parts = [{ kind: 'workpiece', visible: true }, { kind: 'tool', visible: false, id: 'gizli' }, { kind: 'tool', visible: true, id: 'görünür' }];
assert.strictEqual(L.visibleTool(parts).id, 'görünür'); assert.strictEqual(L.visibleTool(parts.slice(0, 2)), undefined);
const OFFSET = { pos_m: [0, 0, 0.1], rpy_deg: [0, 0, 0] };
const TOOL = { kind: 'tool', visible: true, url: '/api/parts/x/mesh', unit_m: 0.001, scale: 2, tool_offset: { pos_m: [0, 0, 0.05], rpy_deg: [0, 0, 0] } };
const fixedT = { pos_m: [0.3, 0, 0.15], rpy_deg: [180, 0, 0], orientation: 'fixed' }, freeT = { pos_m: [0.3, 0, 0.15], orientation: 'free' };
const shape = (ops) => ops.map(([op, x]) => `${op}:${typeof x === 'string' ? x : x.id}`);
let asked = 0; const offOf = () => { asked += 1; return OFFSET; };
assert.deepStrictEqual(shape(L.targetOps(pick, freeT, TOOL, offOf)), ['put:parts:ap-line', 'drop:parts:ap-frame', 'drop:parts:ap-ghost', 'put:parts:ap-tgt'], 'serbest: yalnız hedef noktası');
assert.strictEqual(asked, 0, 'serbest: sahneye TCP ofseti sorulmaz');
assert.deepStrictEqual(shape(L.targetOps(pick, fixedT, TOOL, offOf)), ['put:parts:ap-line', 'drop:parts:ap-tgt', 'put:parts:ap-frame', 'put:parts:ap-ghost'], 'sabit: çerçeve + gölge takım');
assert.strictEqual(asked, 1);
assert.deepStrictEqual(shape(L.targetOps(pick, fixedT, undefined, offOf)), ['put:parts:ap-line', 'drop:parts:ap-tgt', 'put:parts:ap-frame', 'drop:parts:ap-ghost'], 'takım yok: gölge yok');
assert.deepStrictEqual(shape(L.targetOps(pick, fixedT, TOOL, () => null)), ['put:parts:ap-line', 'drop:parts:ap-tgt', 'put:parts:ap-frame', 'drop:parts:ap-ghost'], 'TCP ofseti yok: gölge yok');
assert.strictEqual(asked, 1, 'takım yokken ofset sorulmaz');
for (const ops of [L.targetOps(pick, freeT, TOOL, offOf), L.targetOps(pick, fixedT, TOOL, offOf)]) {
  for (const [op, x] of ops) if (op === 'put') assert.ok(SL.validateSpec(x), `sahne tanımı geçerli olmalı: ${x.id}`);
}
const line = L.targetOps(pick, freeT, TOOL, offOf)[0][1];
assert.deepStrictEqual(clone(line), { id: 'parts:ap-line', kind: 'polyline', layer: 'path', geometry: { points_m: [[0.3, 0, 0.1], [0.3, 0, 0.15]] }, color: 0xf0883e });
const ghost = L.targetOps(pick, fixedT, TOOL, offOf)[3][1];
assert.strictEqual(ghost.geometry.url, TOOL.url); assert.strictEqual(ghost.geometry.scale, 0.002, 'birim × ölçek'); assert.strictEqual(ghost.opacity, 0.4); assert.strictEqual(ghost.color, 0xd29922);
assert.deepStrictEqual(clone(ghost.pose), clone(SL.ghostToolPose(fixedT, OFFSET, TOOL.tool_offset)));
assert.strictEqual(L.targetOps(pick, fixedT, TOOL, offOf)[2][1].pose, fixedT, 'çerçeve hedefin kendisinde durur');

/* ---------- J) yönelim kipi ve iletiler ---------- */
assert.deepStrictEqual(L.modeView(false, 'real'), { orientHidden: false, text: 'Sabit: alet yönelimi seçtiğin gibi tutulur; bilek tekilliği yanındaki hedefler reddedilebilir (o zaman Serbest dene).', cls: '' });
assert.strictEqual(L.modeView(true, 'sim').cls, ''); assert.ok(L.modeView(true, 'sim').orientHidden); assert.match(L.modeView(true, 'sim').text, /yönelimi robot seçer/);
for (const k of ['real', 'unknown', null]) { const v = L.modeView(true, k); assert.strictEqual(v.cls, 'warn'); assert.match(v.text, /GERÇEK ya da bilinmeyen robotta/); }
assert.strictEqual(L.modeSync('unknown', 'sim', 'free'), null, 'bilinmeyen mod: seçime dokunulmaz');
assert.strictEqual(L.modeSync('sim', 'sim', 'fixed'), null, 'aynı modda durum yayını elle seçimi ezmez');
assert.deepStrictEqual(L.modeSync('sim', null, 'fixed'), { modeKey: 'sim', want: 'free', set: true });
assert.deepStrictEqual(L.modeSync('real', 'sim', 'free'), { modeKey: 'real', want: 'fixed', set: true });
assert.deepStrictEqual(L.modeSync('bench', 'sim', 'fixed'), { modeKey: 'bench', want: 'fixed', set: false }, 'zaten istenen kip: plan düşmez');
assert.strictEqual(L.gimbalTarget({ gimbal: true }), 'Hedef yönelimi pitch ±90° yakınında (kilitlenme): roll/yaw birleşir.'); assert.strictEqual(L.gimbalTarget({}), '');
assert.deepStrictEqual(L.checkSay({ ok: true }, fixedT), ['Kontrol geçti. Onaylarsan robot hareket eder.', 'ok']);
assert.deepStrictEqual(L.checkSay({ ok: true }, freeT), ['Kontrol geçti (yönelimi robot seçer). Onaylarsan robot hareket eder.', 'ok']);
assert.deepStrictEqual(L.checkSay({ ok: false, message: 'Hedef güvenli kutunun dışında' }, fixedT), ['Hedef güvenli kutunun dışında — hareket GÖNDERİLMEDİ', 'bad']);
assert.deepStrictEqual(L.moveSay({ ok: true, job_id: 'j7' }), ['Hareket komutu gönderildi (iş j7).', 'ok']); assert.deepStrictEqual(L.moveSay({ ok: true }), ['Hareket komutu gönderildi.', 'ok']);
assert.deepStrictEqual(L.moveSay({ ok: false, message: 'ACİL DUR aktif: önce kilidi aç' }), ['ACİL DUR aktif: önce kilidi aç', 'bad']);
assert.deepStrictEqual(Object.keys(L.TXT), ['noPoint', 'delIdle', 'delArmed', 'checking', 'tcpPlaced', 'modeChanged', 'settingChanged', 'staleNotSent', 'toolNote', 'howTo', 'stepOn', 'stepOff']);
assert.strictEqual(L.TXT.delArmed, 'Emin misin? Sil'); assert.strictEqual(L.TXT.staleNotSent, 'Robot verisi bayat: hareket GÖNDERİLMEDİ, yeniden kontrol et');
console.log('PARTS_LOGIC_OK');
