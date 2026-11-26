/* Sayfa: Parçalar (S2). İş parçası / takım / ortam içe aktarma (STL, OBJ), yerleştirme (sayı alanları + 3B gizmo +
 * "TCP'ye yerleştir"), takım TCP ofseti kaydı ve DOKUN-GİT / YAKLAŞ.
 * Sahne: HubScene (UI_CONTRACT §9-C); nesne kimlikleri `parts:` önekli (parça = `parts:<uuid>`, yardımcılar `parts:ap-…`).
 * GÜVENLİK: hareket yalnız "önce kontrol, sonra onay" akışıyla gider (approach.js: checkMove/executeMove): kontrolden
 * geçmeyen hedefe, bayat plana, bayat robot verisinde ya da ACİL DUR'da komut GİTMEZ. ACİL DUR yalnız üst çubuktadır.
 * Yönelim kipi: SİM'de Serbest (rpy_deg gitmez), gerçek/tezgâhta Sabit; kip her çağrıda `orientation` olarak gider.
 * Bu sayfa hareketi TUTMAZ (tek seferlik komut); yine de sayfadan çıkınca plan düşer. Parça konumu ÖLÇÜM değildir
 * (gerçek parçayla uyuştuğu VARSAYILIR). Veri yalnız textContent ile yazılır.
 * R12: kararlar ve metinler (düğme durumu, uyarılar, sahne nesneleri, sunucu gövdeleri) DOM'suz parts_logic.js'tedir
 * (window.PartsLogic, `scripts` ile yüklenir; node testi: tests/js/parts_logic_check.js). Burası yalnız DOM kurar ve ağa konuşur. */
'use strict';

(() => {
  const SL = () => window.SceneLogic, AM = () => window.ApproachMove;
  let L = null;                                                            // window.PartsLogic: mount'ta alınır (scripts mount'tan ÖNCE yüklenir)

  const P = { root: null, view: null, scene: null, parts: [], limits: null, step: null, selId: null, pick: null, target: null,
    plan: null, speed: null, busy: false, unsub: [], ui: {}, delTimer: null, gizmo: null, fresh: false,
    saving: Promise.resolve(), editSeq: 0, modeKey: null };                                // kayıt zinciri + kullanıcı yazım sayacı

  /* ---------- küçük yardımcılar ---------- */
  const say = (n, text, cls = '') => { n.textContent = text; n.className = 'say ' + cls; };
  const btn = (text, act, fn, cls = 'small') => { const b = el('button', { class: cls, type: 'button', text, 'data-act': act }); b.addEventListener('click', fn); return b; };
  const input = (name, extra = {}) => el('input', { type: 'text', inputMode: 'decimal', autocomplete: 'off', 'data-f': name, ...extra });
  const field = (label, ctl) => el('label', { class: 'pf' }, [el('span', { text: label }), ctl]);
  const select = (name, opts) => { const s = el('select', { 'data-f': name }); for (const [v, t] of opts) s.append(el('option', { value: v, text: t })); return s; };
  const sel = () => P.parts.find((p) => p.id === P.selId) || null;
  const estopped = () => L.estopped(S.snap), robotStale = () => L.robotStale(S.snap), reportedTcp = () => L.reportedTcp(S.snap);

  /* ---------- sahne köprüsü (3B yoksa sayfa yine liste yöneticisi olarak çalışır) ---------- */
  const has = (id) => !!P.scene && P.scene.list().includes(id);
  const put = (spec) => { if (!P.scene) return false; if (has(spec.id)) P.scene.removeObject(spec.id); return P.scene.addObject(spec).ok; };
  const drop = (id) => { if (has(id)) P.scene.removeObject(id); };
  function syncScene() {
    const H = P.scene; if (!H) return;
    for (const id of L.staleIds(H.list(), P.parts)) H.removeObject(id);
    for (const p of P.parts) {
      const id = L.sceneId(p), spec = SL().partSpec(p);
      if (L.needsRebuild(H.info(id), spec)) H.removeObject(id);
      if (!has(id)) {
        const r = H.addObject(spec);
        if (!r.ok) { say(P.ui.listSay, `${p.name}: ${r.message}`, 'bad'); continue; }
        H.ready(id).then((m) => { if (!m.ok) say(P.ui.listSay, `${p.name}: ${m.message}`, 'bad'); });      // mesh indirilemedi/bozuk
      } else { H.setPose(id, spec.pose); H.updateObject(id, { scale: spec.geometry.scale }); }
      H.setVisible(id, p.visible);
    }
  }

  /* ---------- liste ---------- */
  function renderList() {
    const U = P.ui, rows = P.parts.map((p) => {
      const main = el('button', { class: 'part-main', type: 'button', 'data-act': 'select' }, [
        el('b', { text: p.name }), el('span', { class: 'muted', text: L.rowSub(p) })]);
      main.addEventListener('click', () => choose(p.id, true));
      const row = el('li', { class: 'part-row' + (p.id === P.selId ? ' sel' : ''), 'data-id': p.id },
        [main, btn(p.visible ? 'Gizle' : 'Göster', 'vis', () => save(p.id, { visible: !p.visible }))]);
      if (p.size_warning) row.append(el('div', { class: 'say warn', text: p.size_warning }));
      return row;
    });
    U.list.replaceChildren(...rows);
    U.empty.hidden = rows.length > 0;
    U.count.textContent = L.countText(P.parts.length, P.limits);
  }
  async function reload() {
    try {
      const r = L.listState(await api('/api/parts'));
      P.parts = r.parts; P.limits = r.limits; P.step = r.step; renderUploadNote();
      say(P.ui.listSay, r.problems, 'warn');
    } catch (e) { say(P.ui.listSay, e.message, 'bad'); return; }
    if (P.selId && !sel()) P.selId = null;
    syncScene(); renderList(); renderSelected();
  }
  /* Kısmi güncelleme. İki yarış önlenir (alan alan yazarken yavaş ağda gerçek hata olmuştu: y=-30 yazılıp 0 kaydedildi):
   *  1) kayıtlar SIRAYLA gider (FIFO) — yer değiştiren yanıt eski değeri yeni değerin üstüne yazmasın;
   *  2) yanıt beklenirken kullanıcı bir alana yazdıysa (editSeq değişti) alanlar yeniden DOLDURULMAZ — bayat sunucu
   *     değeri, yazılmakta olan girdiyi ezmesin ve sonraki `change` onu geri kaydetmesin. */
  function save(id, patch, note) {
    const seq = P.editSeq;                                                 // yama ŞİMDİ okunan alanlardan üretildi; sıradaki kayıt sonra başlasa da
    const run = () => doSave(id, patch, note, seq);                        // bu andan sonra yazılanlar yamada YOK -> yanıt onları ezmemeli
    P.saving = P.saving.then(run, run);
    return P.saving;
  }
  async function doSave(id, patch, note, seq) {
    try {
      const r = await api(`/api/parts/${id}`, 'PUT', patch);
      P.parts = P.parts.map((p) => (p.id === id ? r.part : p));
      say(P.ui.selSay, ...L.saveSay(note));
    } catch (e) { say(P.ui.selSay, e.message, 'bad'); return; }
    syncScene(); renderList(); renderSelected(P.editSeq !== seq);
  }

  /* ---------- yükleme ---------- */
  function onFileChosen() {
    const U = P.ui.up, c = L.fileChoice(U.file.files && U.file.files[0], P.step);
    U.go.disabled = c.goDisabled;                                          // dönüştürücü yoksa STEP/IGES yüklenmez
    U.unit.disabled = c.unitDisabled; if (c.unitDisabled) U.unit.value = 'mm';   // STEP/IGES: birim DOSYADAN okunur, parça mm olur
    say(U.say, ...c.say);
  }
  function renderUploadNote() {                                            // başlık, sınır notu ve LGPL bildirimi (dönüştürücü varsa)
    const U = P.ui.up, n = L.uploadNote(P.step);
    U.title.textContent = n.title; U.note.textContent = n.note; U.lic.hidden = n.licHidden; U.lic.textContent = n.lic;
  }
  async function upload() {
    const U = P.ui.up, f = U.file.files && U.file.files[0], plan = L.uploadPlan(f);
    if (plan.error) { say(U.say, plan.error, 'bad'); return; }
    U.go.disabled = true; say(U.say, plan.busy, '');
    try {
      const b64 = btoa(L.binaryString(await f.arrayBuffer()));
      const r = await api('/api/parts', 'POST', L.uploadBody(f, b64, U.kind.value, U.unit.value, U.name.value));
      say(U.say, ...L.uploadDone(r));
      U.file.value = ''; U.name.value = ''; U.unit.disabled = false;
      await reload(); choose(r.part.id, true);
    } catch (e) { say(U.say, e.message, 'bad'); }
    U.go.disabled = false;
  }

  /* ---------- seçili parça: yerleştirme ---------- */
  function fillFields(pose) { const v = SL().poseFields(pose); for (const [k] of L.POSE_KEYS) P.ui.f[k].value = v[k]; warnGimbal(pose.rpy_deg); }
  function warnGimbal(rpy) { say(P.ui.gimbal, L.gimbalText(rpy), 'warn'); }
  function renderSelected(keepInputs) {                                   // keepInputs: kullanıcı yazıyor, girdilere dokunma
    const p = sel(), Q = P.ui.s;
    Q.card.hidden = !p; if (!p) return;
    const v = L.selectedView(p);
    if (!keepInputs) { Q.name.value = p.name; Q.kind.value = p.kind; Q.unit.value = p.unit; Q.scale.value = String(p.scale); }
    Q.title.textContent = v.title; Q.toolNote.hidden = v.toolNoteHidden; Q.tcp.hidden = v.tcpHidden; Q.size.textContent = v.size;
    if (!keepInputs) fillFields(v.pose);
    for (const [m, b] of Q.gz) b.classList.toggle('on', P.gizmo === m);
  }
  function choose(id, focus) {
    if (P.selId !== id && P.gizmo) setGizmo('off');
    P.selId = id; renderList(); renderSelected();
    if (P.scene && has(`parts:${id}`)) { P.scene.select(`parts:${id}`); if (focus) P.scene.focus(`parts:${id}`); }
  }
  function readSelected() {
    const p = sel(), f = {}; if (!p) return null;
    for (const [k] of L.POSE_KEYS) f[k] = P.ui.f[k].value;
    const r = L.readPatch(p, f, P.ui.s.scale.value, P.ui.s.unit.value);
    if (!r.ok) { say(P.ui.selSay, r.message, 'bad'); return null; }
    warnGimbal(r.rpy);
    return r.patch;
  }
  function applyFields(note) {
    const p = sel(); if (!p) return;
    if (P.ui.s.kind.value !== p.kind) { save(p.id, { kind: P.ui.s.kind.value }); return; }   // tür değişince sunucu varsayılan ofseti kurar
    const patch = readSelected(); if (patch) save(p.id, patch, note);
  }
  function placeAtTcp() {
    const why = L.tcpBlock(S.snap);
    if (why) { say(P.ui.selSay, why, 'bad'); return; }
    fillFields(reportedTcp());
    applyFields(L.TXT.tcpPlaced);
  }
  async function setGizmo(mode) {
    const H = P.scene, p = sel();
    if (!H || !p) return;
    if (mode === 'off') { H.disableGizmo(); P.gizmo = null; }
    else {
      const r = H.enableGizmo(L.sceneId(p), { mode });
      if (!r.ok) { say(P.ui.selSay, r.message, 'bad'); return; }
      P.gizmo = mode;
    }
    for (const [m, b] of P.ui.s.gz) b.classList.toggle('on', P.gizmo === m);
  }
  function onGizmo(ev) {                                                    // sürüklerken alanlar canlı; bırakınca (phase 'end') TEK kayıt
    const p = sel(); if (!p || ev.id !== L.sceneId(p)) return;
    fillFields(ev.pose);
    if (ev.phase === 'end') save(p.id, { [L.poseKey(p)]: ev.pose });
  }
  function removePart() {
    const p = sel(), b = P.ui.s.del; if (!p) return;
    if (!P.delTimer) {                                                     // iki adım: yanlışlıkla silinmesin (confirm() penceresi yok)
      b.textContent = L.TXT.delArmed; P.delTimer = setTimeout(() => { b.textContent = L.TXT.delIdle; P.delTimer = null; }, L.DELETE_ARM_MS); return;
    }
    clearTimeout(P.delTimer); P.delTimer = null; b.textContent = L.TXT.delIdle;
    api(`/api/parts/${p.id}`, 'DELETE').then(() => { if (P.gizmo) setGizmo('off'); P.selId = null; return reload(); })
      .catch((e) => say(P.ui.selSay, e.message, 'bad'));
  }

  /* ---------- dokun-git / yaklaş ---------- */
  const blockReason = () => L.blockReason(S.snap, !!P.scene, !!P.pick);
  function setApButtons() {
    const A = P.ui.ap, b = L.approachButtons(S.snap, { hasScene: !!P.scene, hasPick: !!P.pick, busy: P.busy, hasPlan: !!P.plan });
    A.near.disabled = b.nearOff; A.go0.disabled = b.touchOff; A.ok.hidden = b.okHidden; A.ok.disabled = b.okOff;
    say(A.why, b.why, b.whyCls);
  }
  function drawPoint() { put(L.pointSpec(P.pick)); P.ui.ap.info.textContent = L.pointInfo(P.pick); }
  function drawTarget(t) {
    const offOf = () => P.scene && P.scene.tcpOffset();
    for (const [op, x] of L.targetOps(P.pick, t, L.visibleTool(P.parts), offOf)) { if (op === 'put') put(x); else drop(x); }
  }
  function tint(c) { for (const id of [L.AP.line, L.AP.ghost, L.AP.tgt]) if (has(id)) P.scene.updateObject(id, { color: c }); }
  function clearPlan(msg) {
    P.plan = null; P.target = null; for (const id of L.PLAN_IDS) drop(id);
    P.ui.ap.warn.textContent = ''; say(P.ui.ap.say, msg || '', ''); setApButtons();
  }
  function onPick(res) {                                                    // §9-C: yalnız isabette çağrılır (boşluğa dokunuş gelmez)
    if (!L.validPick(res)) return;
    P.pick = res; clearPlan(''); drawPoint();
    const id = L.pickedPartId(res); if (id) choose(id, false);
    setApButtons();
  }
  function showMoveMode() {
    const A = P.ui.ap, v = L.modeView(A.mode.value === 'free', P.modeKey);
    A.orientField.hidden = v.orientHidden; say(A.modeHint, v.text, v.cls);
  }
  function syncMoveMode() {                                                 // kural ve gerekçe: parts_logic.js modeSync
    const A = P.ui.ap, m = L.modeSync(window.ShellLogic ? window.ShellLogic.modeInfo(S.snap).key : 'unknown', P.modeKey, A.mode.value);
    if (m) {
      P.modeKey = m.modeKey;
      if (m.set) { A.mode.value = m.want; if (P.plan || P.target) clearPlan(L.TXT.modeChanged); }
    }
    showMoveMode();
  }
  async function approach(zero) {
    const A = P.ui.ap;
    if (P.busy || blockReason()) return;
    const cfg = SL().readApproach(A.d.value, A.speed.value, zero);
    if (!cfg.ok) { say(A.say, cfg.message, 'bad'); return; }
    let t;
    try {
      const cur = reportedTcp();
      t = SL().approachTarget(P.pick.point_m, P.pick.normal, cfg.dMm, { orientation: A.orient.value, currentRpy: cur && cur.rpy_deg, moveOrientation: A.mode.value });
    } catch (e) { say(A.say, e.message, 'bad'); return; }
    P.target = t; P.speed = cfg.speedPct; P.plan = null; drawTarget(t);
    say(A.warn, L.gimbalTarget(t), 'warn');
    P.busy = true; say(A.say, L.TXT.checking, ''); setApButtons();
    const res = await AM().checkMove(api, t, cfg.speedPct);
    P.busy = false;
    if (P.target !== t) { setApButtons(); return; }                        // bu arada başka hedef seçildi
    P.plan = res.ok ? res.plan : null; tint(res.ok ? L.TINT.ok : L.TINT.bad); say(A.say, ...L.checkSay(res, t));
    setApButtons();
  }
  async function confirmMove() {
    const A = P.ui.ap;
    if (P.busy || !P.plan) return;
    if (robotStale()) { P.plan = null; say(A.say, L.TXT.staleNotSent, 'bad'); setApButtons(); return; }
    P.busy = true; setApButtons();
    const res = await AM().executeMove(api, P.plan, P.target, P.speed, estopped);
    P.busy = false; P.plan = null;
    say(A.say, ...L.moveSay(res));
    setApButtons();
  }

  /* ---------- kurulum ---------- */
  function buildUpload() {
    const U = P.ui.up = { file: el('input', { type: 'file', accept: L.FILE_ACCEPT, 'data-f': 'file' }),
      name: el('input', { type: 'text', 'data-f': 'newname', maxLength: 80, placeholder: 'Ad (boşsa dosya adı)' }),
      kind: select('newkind', L.KIND_OPTS), unit: select('newunit', L.UNIT_OPTS), say: el('div', { class: 'say' }) };
    U.file.addEventListener('change', onFileChosen);
    U.go = btn('Yükle', 'upload', upload, 'primary');
    U.title = el('h2'); U.note = el('div', { class: 'say' }); U.lic = el('div', { class: 'say muted', 'data-f': 'license', hidden: true }); renderUploadNote();
    return el('div', { class: 'card' }, [U.title, U.file, U.name,
      el('div', { class: 'pose-grid two' }, [field('Tür', U.kind), field('Dosya birimi', U.unit)]), U.go, U.say, U.note, U.lic]);
  }
  function buildSelected() {
    const Q = P.ui.s = { title: el('h2'), name: el('input', { type: 'text', maxLength: 80, 'data-f': 'name' }),
      kind: select('kind', L.KIND_OPTS), unit: select('unit', L.UNIT_OPTS), scale: input('scale'), size: el('div', { class: 'muted' }),
      toolNote: el('div', { class: 'say warn', text: L.TXT.toolNote }),
      del: btn(L.TXT.delIdle, 'delete', removePart), tcp: btn('TCP’ye yerleştir', 'tcp', placeAtTcp), gz: [] };
    P.ui.f = {}; P.ui.gimbal = el('div', { class: 'say' }); P.ui.selSay = el('div', { class: 'say' });
    const grid = el('div', { class: 'pose-grid' });
    const typed = () => { P.editSeq += 1; };                                // kullanıcı bir girdiye yazdı (bkz. save)
    for (const [k, label] of L.POSE_KEYS) { P.ui.f[k] = input(k); P.ui.f[k].addEventListener('input', typed); P.ui.f[k].addEventListener('change', () => applyFields()); grid.append(field(label, P.ui.f[k])); }
    for (const e of [Q.name, Q.kind, Q.unit, Q.scale]) e.addEventListener('input', typed);
    for (const e of [Q.kind, Q.unit, Q.scale]) e.addEventListener('change', () => applyFields());
    Q.name.addEventListener('change', () => { const p = sel(); if (p && Q.name.value.trim()) save(p.id, { name: Q.name.value.trim() }); });
    for (const [m, t] of L.GIZMO_OPTS) { const b = btn(t, `gizmo-${m}`, () => setGizmo(m)); Q.gz.push([m, b]); }
    Q.card = el('div', { class: 'card', hidden: true }, [el('h2', { text: 'Seçili parça' }), field('Ad', Q.name),
      el('div', { class: 'pose-grid two' }, [field('Tür', Q.kind), field('Birim', Q.unit)]), field('Ölçek (çarpan)', Q.scale), Q.size,
      Q.title, grid, P.ui.gimbal, Q.toolNote, el('div', { class: 'row' }, [Q.tcp, ...Q.gz.map((g) => g[1]), Q.del]), P.ui.selSay]);
    return Q.card;
  }
  function buildApproach() {
    const A = P.ui.ap = { d: input('d', { value: String(L.DEFAULT_D_MM) }), speed: input('speed', { value: String(L.DEFAULT_SPEED_PCT) }),
      orient: select('orient', Object.entries(L.ORIENT_TEXT)), mode: select('orientmode', L.MODE_TEXT), modeHint: el('div', { class: 'say' }),
      info: el('div', { class: 'muted', text: L.TXT.noPoint }), say: el('div', { class: 'say' }), warn: el('div', { class: 'say warn' }), why: el('div', { class: 'say' }) };
    A.mode.value = SL().defaultMoveOrientation(P.modeKey); A.orientField = field('Sabit yönelimde alet nasıl baksın', A.orient);
    for (const e of [A.d, A.speed, A.orient, A.mode]) e.addEventListener('input', () => { if (P.plan || P.target) clearPlan(L.TXT.settingChanged); });
    A.mode.addEventListener('input', showMoveMode);
    A.near = btn('Yaklaş (mesafe kadar önünde dur)', 'approach', () => approach(false));
    A.go0 = btn('Git (yüzeye değ, d = 0)', 'touch', () => approach(true));
    A.ok = btn('Onayla ve hareket et', 'confirm', confirmMove, 'primary'); A.ok.hidden = true;
    return el('div', { class: 'card' }, [el('h2', { text: 'Dokun-git / Yaklaş' }), el('div', { class: 'say', text: L.TXT.howTo }),
      A.info, el('div', { class: 'pose-grid' }, [field('Mesafe d (mm)', A.d), field('Hız (%)', A.speed)]), field('Yönelim', A.mode), A.modeHint, A.orientField,
      el('div', { class: 'row' }, [A.near, A.go0]), A.why, A.warn, A.say, A.ok, el('div', { class: 'say warn', text: L.ASSUMPTION })]);
  }
  function buildSide(viewTools) {                                           // yan sütun: görünüm araçları, liste, yükleme, seçili parça, dokun-git
    const U = P.ui;
    U.list = el('ul', { class: 'part-list' }); U.empty = el('div', { class: 'muted', text: 'Henüz parça yok.' });
    U.count = el('div', { class: 'muted' }); U.listSay = el('div', { class: 'say' });
    return el('div', {}, [viewTools, el('div', { class: 'card' }, [el('h2', { text: 'Parçalar' }), U.count, U.list, U.empty, U.listSay]),
      buildUpload(), buildSelected(), buildApproach()]);
  }
  function buildViewTools() {
    const row = el('div', { class: 'row' });
    for (const [n, t] of L.CAMERA_OPTS) row.append(btn(t, `cam-${n}`, () => { if (P.scene) P.scene.cameraPreset(n); }));
    return row;
  }
  function hook() {
    unhook();
    const H = P.scene; if (!H) return;
    P.unsub = [H.onPick(onPick), H.onGizmoChange(onGizmo)]; H.setPickLayers(L.PICK_LAYERS);
  }
  /* Paylaşılan tuvali bu sayfanın kutusuna al; HubScene gerçeklenmemişse/WebGL yoksa sayfa liste yöneticisi olarak çalışır. */
  async function attachScene() {
    await HubPages.mountView(P.view);
    let H = null;
    try { H = await HubPages.scene(); } catch (e) { H = null; }
    P.scene = H && H.implemented ? H : null;
    hook(); setApButtons();
  }
  function unhook() { for (const f of P.unsub) f(); P.unsub = []; }

  HubPages.register({
    id: 'parts', title: 'Parçalar', icon: '🧩', order: 25, styles: ['/pages/parts.css'], scripts: ['/scene_logic.js', '/parts_logic.js', '/approach.js'],
    async mount(root) {
      L = window.PartsLogic;
      P.root = root; P.view = el('div', { class: 'parts-view' });
      root.append(el('div', { class: 'page-grid two' }, [P.view, buildSide(buildViewTools())]));   // 3B tuval ızgaranın DOĞRUDAN çocuğu (sticky için)
      syncMoveMode(); await attachScene(); await reload(); P.fresh = true;                   // show() mount'tan hemen sonra onShow çağırır: ikinci yükleme gereksiz
    },
    onShow() {
      if (P.fresh) { P.fresh = false; return; }
      attachScene().then(reload);
    },
    onHide() {                                                              // sayfadan çıkınca: gizmo kapanır, plan düşer, işaretler kalkar
      unhook(); clearTimeout(P.delTimer); P.delTimer = null; P.ui.s.del.textContent = L.TXT.delIdle;
      if (P.scene) { P.scene.disableGizmo(); P.scene.setPickLayers(L.DEFAULT_PICK); P.scene.select(null); }
      P.gizmo = null; P.pick = null; clearPlan(''); drop(L.AP.point); P.ui.ap.info.textContent = L.TXT.noPoint;
    },
    onState() { if (!P.root) return; if (estopped() || robotStale()) P.plan = null; syncMoveMode(); setApButtons(); },
  });
})();
