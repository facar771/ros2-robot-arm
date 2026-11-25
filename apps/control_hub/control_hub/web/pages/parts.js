/* Sayfa: Parçalar (S2). İş parçası / takım / ortam içe aktarma (STL, OBJ), yerleştirme (sayı alanları + 3B gizmo +
 * "TCP'ye yerleştir"), takım TCP ofseti kaydı ve DOKUN-GİT / YAKLAŞ.
 * Sahne: HubScene (UI_CONTRACT §9-C); nesne kimlikleri `parts:` önekli (parça = `parts:<uuid>`, yardımcılar `parts:ap-…`).
 * GÜVENLİK: hareket yalnız "önce kontrol, sonra onay" akışıyla gider (approach.js: checkMove/executeMove): kontrolden
 * geçmeyen hedefe, bayat plana, bayat robot verisinde ya da ACİL DUR'da komut GİTMEZ. ACİL DUR yalnız üst çubuktadır.
 * Yönelim kipi: SİM'de Serbest (rpy_deg gitmez), gerçek/tezgâhta Sabit; kip her çağrıda `orientation` olarak gider.
 * Bu sayfa hareketi TUTMAZ (tek seferlik komut); yine de sayfadan çıkınca plan düşer. Parça konumu ÖLÇÜM değildir
 * (gerçek parçayla uyuştuğu VARSAYILIR). Veri yalnız textContent ile yazılır. */
'use strict';

(() => {
  const SL = () => window.SceneLogic, AM = () => window.ApproachMove;
  const PICK_LAYERS = ['workpiece', 'environment'];                       // takım robota bağlıdır: hedef olamaz
  const DEFAULT_PICK = ['workpiece', 'tool', 'environment', 'markers'];    // başka sayfalar için eski hâl
  const STEP_EXT = /\.(step|stp|iges|igs)$/i, MAX_BYTES = 50 * 1024 * 1024, B64_CHUNK = 0x8000, DELETE_ARM_MS = 4000;
  const DEFAULT_D_MM = 50, DEFAULT_SPEED_PCT = 10;                         // KARAR GEREKLİ: varsayılanlar (PR'da)
  const TINT = { pending: 0xf0883e, ok: 0x3fb950, bad: 0xda3633, tool: 0xd29922 };
  const AP = { point: 'parts:ap-point', line: 'parts:ap-line', frame: 'parts:ap-frame', ghost: 'parts:ap-ghost', tgt: 'parts:ap-tgt' };
  const PART_SCENE_ID = /^parts:[0-9a-f]{32}$/;                           // yalnız parça nesneleri; ap-* yardımcıları buraya girmez
  const POSE_KEYS = [['x', 'x (mm)'], ['y', 'y (mm)'], ['z', 'z (mm)'], ['r', 'roll (°)'], ['p', 'pitch (°)'], ['w', 'yaw (°)']];
  const ORIENT_TEXT = { normal: 'Takım yüzeye dik baksın (−normal)', down: 'Hep aşağı bak', keep: 'Mevcut yönelimi koru' };
  /* Hareket yönelimi (UI_CONTRACT §9-B `orientation`): varsayılan SİM'de 'free', gerçek robotta 'fixed' (scene_logic.js). */
  const MODE_TEXT = [['free', 'Serbest (robot yönelimi seçer)'], ['fixed', 'Sabit (yönelimi aşağıdan seç)']];
  const MODE_HINT = {
    free: ['Serbest: yalnız KONUM hedeflenir, yönelimi robot seçer; alet yüzeye dik BAKMAYABİLİR. Dik/aşağı bakış gerekiyorsa Sabit seç.', ''],
    freeReal: ['Serbest yönelim GERÇEK ya da bilinmeyen robotta: alet beklenmedik açıyla gelebilir. Hedefi 3B’de iyi kontrol et.', 'warn'],
    fixed: ['Sabit: alet yönelimi seçtiğin gibi tutulur; bilek tekilliği yanındaki hedefler reddedilebilir (o zaman Serbest dene).', ''],
  };
  const ASSUMPTION = 'Not: 3B’deki parça konumunun gerçek parçayla uyuştuğu VARSAYILIR; bu bir ölçüm değildir (tahmini).';

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
  const estopped = () => !!(S.snap && S.snap.estop && S.snap.estop.latched);
  const canControl = () => !!(S.snap && S.snap.me && S.snap.me.can_control);
  const robotStale = () => !!(S.snap && S.snap.robot && S.snap.robot.stale);
  const sceneId = (p) => `parts:${p.id}`;
  const finite3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === 'number' && Number.isFinite(x));
  function reportedTcp() {                                                 // robotun BİLDİRDİĞİ TCP (§9-A: pos_m, rpy_deg W-P-R)
    const t = S.snap && S.snap.robot && S.snap.robot.tcp;
    const pos = t && (t.pos_m || t.position), rpy = t && (t.rpy_deg || t.wpr_deg);
    return finite3(pos) && finite3(rpy) ? { pos_m: pos.slice(), rpy_deg: rpy.slice() } : null;
  }
  function toBase64(buf) {                                                 // parça parça: büyük dosyada yığın taşmasın
    const u = new Uint8Array(buf); let s = '';
    for (let i = 0; i < u.length; i += B64_CHUNK) s += String.fromCharCode.apply(null, u.subarray(i, i + B64_CHUNK));
    return btoa(s);
  }

  /* ---------- sahne köprüsü (3B yoksa sayfa yine liste yöneticisi olarak çalışır) ---------- */
  const has = (id) => !!P.scene && P.scene.list().includes(id);
  const put = (spec) => { if (!P.scene) return false; if (has(spec.id)) P.scene.removeObject(spec.id); return P.scene.addObject(spec).ok; };
  const drop = (id) => { if (has(id)) P.scene.removeObject(id); };
  function syncScene() {
    const H = P.scene; if (!H) return;
    const want = new Map(P.parts.map((p) => [sceneId(p), p]));
    for (const id of H.list()) if (PART_SCENE_ID.test(id) && !want.has(id)) H.removeObject(id);
    for (const [id, p] of want) {
      const spec = SL().partSpec(p), cur = H.info(id);
      if (cur && (cur.layer !== spec.layer || cur.attach !== spec.attach)) H.removeObject(id);      // tür değişti: katman/bağlama yeniden kurulur
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
        el('b', { text: p.name }),
        el('span', { class: 'muted', text: `${SL().KIND_LABEL[p.kind]} · ${SL().sizeText(p.size_m)} · ${p.triangles} üçgen` })]);
      main.addEventListener('click', () => choose(p.id, true));
      const row = el('li', { class: 'part-row' + (p.id === P.selId ? ' sel' : ''), 'data-id': p.id },
        [main, btn(p.visible ? 'Gizle' : 'Göster', 'vis', () => save(p.id, { visible: !p.visible }))]);
      if (p.size_warning) row.append(el('div', { class: 'say warn', text: p.size_warning }));
      return row;
    });
    U.list.replaceChildren(...rows);
    U.empty.hidden = rows.length > 0;
    U.count.textContent = `${P.parts.length} / ${(P.limits && P.limits.max_parts) || '?'} parça`;
  }
  async function reload() {
    try {
      const r = await api('/api/parts');
      P.parts = r.parts || []; P.limits = r.limits || null; P.step = r.step || null; renderUploadNote();
      say(P.ui.listSay, (r.problems || []).join(' · '), 'warn');
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
      say(P.ui.selSay, note || 'Kaydedildi', note ? 'warn' : 'ok');                // not: "Kaydedildi" onu ezmesin
    } catch (e) { say(P.ui.selSay, e.message, 'bad'); return; }
    syncScene(); renderList(); renderSelected(P.editSeq !== seq);
  }

  /* ---------- yükleme ---------- */
  const stepOn = () => !!(P.step && P.step.available);                    // S2b: STEP/IGES dönüştürücüsü kurulu mu (sunucu bildirir)
  function onFileChosen() {
    const U = P.ui.up, f = U.file.files && U.file.files[0], step = !!f && STEP_EXT.test(f.name || '');
    U.go.disabled = step && !stepOn();                                     // dönüştürücü yoksa STEP/IGES yüklenmez
    U.unit.disabled = step && stepOn(); if (U.unit.disabled) U.unit.value = 'mm';   // STEP/IGES: birim DOSYADAN okunur, parça mm olur
    if (!step) say(U.say, '', '');
    else if (stepOn()) say(U.say, 'STEP/IGES: birim dosyadan okunur, parça mm olarak kaydedilir. Dönüşüm en çok 20 sn sürer.', '');
    else say(U.say, (P.step && P.step.message) || 'STEP için dönüştürücü kurulu değil, STL’e çevirip yükle.', 'warn');
  }
  function renderUploadNote() {                                            // başlık, sınır notu ve LGPL bildirimi (dönüştürücü varsa)
    const U = P.ui.up, on = stepOn(), n = 'En çok 50 MB, 500 000 üçgen. ';
    U.title.textContent = on ? 'Parça ekle (STL / OBJ / STEP / IGES)' : 'Parça ekle (STL / OBJ)';
    U.note.textContent = n + (on ? 'STEP/IGES alt süreçte dönüştürülür (en çok 20 sn).' : 'STEP/IGES için dönüştürücü kurulu değil: CAD’den STL’e çevirip yükle.');
    U.lic.hidden = !on; U.lic.textContent = on ? `${(P.step && P.step.notice) || 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)'}. Lisans ve kaynak: docs/THIRD_PARTY.md` : '';
  }
  async function upload() {
    const U = P.ui.up, f = U.file.files && U.file.files[0];
    if (!f) { say(U.say, 'Önce bir dosya seç', 'bad'); return; }
    if (f.size > MAX_BYTES) { say(U.say, `Dosya ${MAX_BYTES / 1048576} MB sınırını aşıyor`, 'bad'); return; }
    U.go.disabled = true; say(U.say, STEP_EXT.test(f.name) ? 'Dönüştürülüyor (en çok 20 sn)…' : 'Yükleniyor…', '');
    try {
      const body = { filename: f.name, data_b64: toBase64(await f.arrayBuffer()), kind: U.kind.value, unit: U.unit.value };
      if (U.name.value.trim()) body.name = U.name.value.trim();
      const r = await api('/api/parts', 'POST', body);
      say(U.say, (r.warnings || []).length ? r.warnings.join(' · ') : 'Yüklendi', r.warnings && r.warnings.length ? 'warn' : 'ok');
      U.file.value = ''; U.name.value = ''; U.unit.disabled = false;
      await reload(); choose(r.part.id, true);
    } catch (e) { say(U.say, e.message, 'bad'); }
    U.go.disabled = false;
  }

  /* ---------- seçili parça: yerleştirme ---------- */
  function fillFields(pose) { const v = SL().poseFields(pose); for (const [k] of POSE_KEYS) P.ui.f[k].value = v[k]; warnGimbal(pose.rpy_deg); }
  function warnGimbal(rpy) { say(P.ui.gimbal, SL().nearGimbal(rpy) ? 'Pitch ±90° yakınında: roll ve yaw birleşir (kilitlenme).' : '', 'warn'); }
  function renderSelected(keepInputs) {                                   // keepInputs: kullanıcı yazıyor, girdilere dokunma
    const p = sel(), Q = P.ui.s;
    Q.card.hidden = !p; if (!p) return;
    const tool = p.kind === 'tool';
    if (!keepInputs) { Q.name.value = p.name; Q.kind.value = p.kind; Q.unit.value = p.unit; Q.scale.value = String(p.scale); }
    Q.title.textContent = tool ? 'Takım ofseti (flanşa göre)' : 'Yerleşim (dünya çerçevesi)';
    Q.toolNote.hidden = !tool; Q.tcp.hidden = tool;
    Q.size.textContent = `Boyut (ölçekli): ${SL().sizeText(p.size_m)} · ${p.triangles} üçgen`;
    if (!keepInputs) fillFields(tool ? p.tool_offset : p.pose);
    for (const [m, b] of Q.gz) b.classList.toggle('on', P.gizmo === m);
  }
  function choose(id, focus) {
    if (P.selId !== id && P.gizmo) setGizmo('off');
    P.selId = id; renderList(); renderSelected();
    if (P.scene && has(`parts:${id}`)) { P.scene.select(`parts:${id}`); if (focus) P.scene.focus(`parts:${id}`); }
  }
  function readSelected() {
    const p = sel(), f = {}; if (!p) return null;
    for (const [k] of POSE_KEYS) f[k] = P.ui.f[k].value;
    const pr = SL().readPoseFields(f), sc = SL().readScale(P.ui.s.scale.value);
    if (!pr.ok || !sc.ok) { say(P.ui.selSay, (pr.ok ? sc : pr).message, 'bad'); return null; }
    warnGimbal(pr.pose.rpy_deg);
    return { [p.kind === 'tool' ? 'tool_offset' : 'pose']: pr.pose, scale: sc.scale, unit: P.ui.s.unit.value };
  }
  function applyFields(note) {
    const p = sel(); if (!p) return;
    if (P.ui.s.kind.value !== p.kind) { save(p.id, { kind: P.ui.s.kind.value }); return; }   // tür değişince sunucu varsayılan ofseti kurar
    const patch = readSelected(); if (patch) save(p.id, patch, note);
  }
  function placeAtTcp() {
    const t = reportedTcp();
    if (!t || robotStale()) { say(P.ui.selSay, robotStale() ? 'Robot verisi bayat: TCP konumu alınamadı' : 'Robotun TCP konumu bilinmiyor', 'bad'); return; }
    fillFields(t);
    applyFields('Parça, robotun BİLDİRDİĞİ TCP konumuna konuldu (tahmini; ölçüm değil).');
  }
  async function setGizmo(mode) {
    const H = P.scene, p = sel();
    if (!H || !p) return;
    if (mode === 'off') { H.disableGizmo(); P.gizmo = null; }
    else {
      const r = H.enableGizmo(sceneId(p), { mode });
      if (!r.ok) { say(P.ui.selSay, r.message, 'bad'); return; }
      P.gizmo = mode;
    }
    for (const [m, b] of P.ui.s.gz) b.classList.toggle('on', P.gizmo === m);
  }
  function onGizmo(ev) {                                                    // sürüklerken alanlar canlı; bırakınca (phase 'end') TEK kayıt
    const p = sel(); if (!p || ev.id !== sceneId(p)) return;
    fillFields(ev.pose);
    if (ev.phase === 'end') save(p.id, { [p.kind === 'tool' ? 'tool_offset' : 'pose']: ev.pose });
  }
  function removePart() {
    const p = sel(), b = P.ui.s.del; if (!p) return;
    if (!P.delTimer) {                                                     // iki adım: yanlışlıkla silinmesin (confirm() penceresi yok)
      b.textContent = 'Emin misin? Sil'; P.delTimer = setTimeout(() => { b.textContent = 'Sil'; P.delTimer = null; }, DELETE_ARM_MS); return;
    }
    clearTimeout(P.delTimer); P.delTimer = null; b.textContent = 'Sil';
    api(`/api/parts/${p.id}`, 'DELETE').then(() => { if (P.gizmo) setGizmo('off'); P.selId = null; return reload(); })
      .catch((e) => say(P.ui.selSay, e.message, 'bad'));
  }

  /* ---------- dokun-git / yaklaş ---------- */
  const blockReason = () => (estopped() ? 'ACİL DUR aktif: hareket yok' : !canControl() ? 'Bu oturum salt-okunur: hareket yok'
    : robotStale() ? 'Robot verisi bayat: hareket yok' : !P.scene ? '3B görünüm yok: dokunarak hedef seçilemez'
    : !P.pick ? 'Önce 3B’de parçanın yüzeyine dokun' : '');
  function setApButtons() {
    const A = P.ui.ap, why = blockReason(), off = !!why || P.busy;
    A.near.disabled = off; A.go0.disabled = off;
    A.ok.hidden = !P.plan; A.ok.disabled = P.busy || estopped() || !canControl() || robotStale();
    say(A.why, why && !P.plan ? why : '', estopped() ? 'bad' : '');
  }
  function drawPoint() {
    put({ id: AP.point, kind: 'marker', layer: 'markers', geometry: { radius_m: 0.006 }, pose: { pos_m: P.pick.point_m, rpy_deg: [0, 0, 0] }, color: TINT.pending, opacity: 1 });
    const mm = (x) => Math.round(x * 10000) / 10, n = P.pick.normal.map((x) => Math.round(x * 100) / 100).join(' ');
    P.ui.ap.info.textContent = `Nokta (mm): ${P.pick.point_m.map(mm).join(' · ')}  ·  yüzey normali: ${n}`;
  }
  function drawTarget(t) {
    put({ id: AP.line, kind: 'polyline', layer: 'path', geometry: { points_m: [P.pick.point_m, t.pos_m] }, color: TINT.pending });
    if (t.orientation === 'free') {                                        // yönelim yok: çerçeve/gölge takım çizilmez, yalnız hedef noktası
      drop(AP.frame); drop(AP.ghost);
      put({ id: AP.tgt, kind: 'marker', layer: 'path', geometry: { radius_m: 0.008 }, pose: { pos_m: t.pos_m, rpy_deg: [0, 0, 0] }, color: TINT.pending, opacity: 0.9 });
      return;
    }
    drop(AP.tgt);
    put({ id: AP.frame, kind: 'frame', layer: 'path', geometry: { size_m: 0.05 }, pose: t });
    const tool = P.parts.find((p) => p.kind === 'tool' && p.visible), off = P.scene && P.scene.tcpOffset();
    if (tool && off) {                                                     // gölge takım: bu hedefte takım nerede olur
      put({ id: AP.ghost, kind: 'mesh', layer: 'path', geometry: { url: tool.url, scale: tool.unit_m * tool.scale },
        pose: SL().ghostToolPose(t, off, tool.tool_offset), color: TINT.tool, opacity: 0.4 });
    } else drop(AP.ghost);
  }
  function tint(c) { for (const id of [AP.line, AP.ghost, AP.tgt]) if (has(id)) P.scene.updateObject(id, { color: c }); }
  function clearPlan(msg) {
    P.plan = null; P.target = null; for (const id of [AP.frame, AP.line, AP.ghost, AP.tgt]) drop(id);
    P.ui.ap.warn.textContent = ''; say(P.ui.ap.say, msg || '', ''); setApButtons();
  }
  function onPick(res) {                                                    // §9-C: yalnız isabette çağrılır (boşluğa dokunuş gelmez)
    if (!res || !finite3(res.point_m) || !finite3(res.normal)) return;
    P.pick = res; clearPlan(''); drawPoint();
    if (PART_SCENE_ID.test(res.objectId || '')) choose(res.objectId.slice('parts:'.length), false);
    setApButtons();
  }
  /* Yönelim kipi: SİM'de varsayılan Serbest, gerçek/tezgâhta Sabit. Mod DEĞİŞİNCE (sim↔real) varsayılan yeniden uygulanır ve plan düşer;
   * kullanıcı kipi elle değiştirdiyse aynı modda durum yayınları onu ezmez. Mod bilinmiyorsa (robot bildirimi yok) dokunulmaz. */
  function showMoveMode() {
    const A = P.ui.ap, free = A.mode.value === 'free', [txt, cls] = MODE_HINT[free ? (P.modeKey === 'sim' ? 'free' : 'freeReal') : 'fixed'];
    A.orientField.hidden = free; say(A.modeHint, txt, cls);
  }
  function syncMoveMode() {
    const A = P.ui.ap, key = window.ShellLogic ? window.ShellLogic.modeInfo(S.snap).key : 'unknown';
    if (key !== 'unknown' && key !== P.modeKey) {
      P.modeKey = key; const want = SL().defaultMoveOrientation(key);
      if (A.mode.value !== want) { A.mode.value = want; if (P.plan || P.target) clearPlan('Mod değişti: yönelim varsayılanı uygulandı, yeniden kontrol et'); }
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
    say(A.warn, t.gimbal ? 'Hedef yönelimi pitch ±90° yakınında (kilitlenme): roll/yaw birleşir.' : '', 'warn');
    P.busy = true; say(A.say, 'Kontrol ediliyor…', ''); setApButtons();
    const res = await AM().checkMove(api, t, cfg.speedPct);
    P.busy = false;
    if (P.target !== t) { setApButtons(); return; }                        // bu arada başka hedef seçildi
    if (res.ok) { P.plan = res.plan; tint(TINT.ok); say(A.say, `Kontrol geçti${t.orientation === 'free' ? ' (yönelimi robot seçer)' : ''}. Onaylarsan robot hareket eder.`, 'ok'); }
    else { P.plan = null; tint(TINT.bad); say(A.say, `${res.message} — hareket GÖNDERİLMEDİ`, 'bad'); }
    setApButtons();
  }
  async function confirmMove() {
    const A = P.ui.ap;
    if (P.busy || !P.plan) return;
    if (robotStale()) { P.plan = null; say(A.say, 'Robot verisi bayat: hareket GÖNDERİLMEDİ, yeniden kontrol et', 'bad'); setApButtons(); return; }
    P.busy = true; setApButtons();
    const res = await AM().executeMove(api, P.plan, P.target, P.speed, estopped);
    P.busy = false; P.plan = null;
    say(A.say, res.ok ? `Hareket komutu gönderildi${res.job_id ? ` (iş ${res.job_id})` : ''}.` : res.message, res.ok ? 'ok' : 'bad');
    setApButtons();
  }

  /* ---------- kurulum ---------- */
  function buildUpload() {
    const U = P.ui.up = { file: el('input', { type: 'file', accept: '.stl,.obj,.step,.stp,.iges,.igs', 'data-f': 'file' }),
      name: el('input', { type: 'text', 'data-f': 'newname', maxLength: 80, placeholder: 'Ad (boşsa dosya adı)' }),
      kind: select('newkind', [['workpiece', 'İş parçası'], ['tool', 'Takım'], ['environment', 'Ortam']]),
      unit: select('newunit', [['mm', 'mm'], ['in', 'inç'], ['m', 'm']]), say: el('div', { class: 'say' }) };
    U.file.addEventListener('change', onFileChosen);
    U.go = btn('Yükle', 'upload', upload, 'primary');
    U.title = el('h2'); U.note = el('div', { class: 'say' }); U.lic = el('div', { class: 'say muted', 'data-f': 'license', hidden: true }); renderUploadNote();
    return el('div', { class: 'card' }, [U.title, U.file, U.name,
      el('div', { class: 'pose-grid two' }, [field('Tür', U.kind), field('Dosya birimi', U.unit)]), U.go, U.say, U.note, U.lic]);
  }
  function buildSelected() {
    const Q = P.ui.s = { title: el('h2'), name: el('input', { type: 'text', maxLength: 80, 'data-f': 'name' }),
      kind: select('kind', [['workpiece', 'İş parçası'], ['tool', 'Takım'], ['environment', 'Ortam']]),
      unit: select('unit', [['mm', 'mm'], ['in', 'inç'], ['m', 'm']]), scale: input('scale'), size: el('div', { class: 'muted' }),
      toolNote: el('div', { class: 'say warn', text: 'Takım ofseti yalnız KAYDEDİLİR ve 3B’de çizilir; robota uygulanmaz.' }),
      del: btn('Sil', 'delete', removePart), tcp: btn('TCP’ye yerleştir', 'tcp', placeAtTcp), gz: [] };
    P.ui.f = {}; P.ui.gimbal = el('div', { class: 'say' }); P.ui.selSay = el('div', { class: 'say' });
    const grid = el('div', { class: 'pose-grid' });
    const typed = () => { P.editSeq += 1; };                                // kullanıcı bir girdiye yazdı (bkz. save)
    for (const [k, label] of POSE_KEYS) { P.ui.f[k] = input(k); P.ui.f[k].addEventListener('input', typed); P.ui.f[k].addEventListener('change', () => applyFields()); grid.append(field(label, P.ui.f[k])); }
    for (const e of [Q.name, Q.kind, Q.unit, Q.scale]) e.addEventListener('input', typed);
    for (const e of [Q.kind, Q.unit, Q.scale]) e.addEventListener('change', () => applyFields());
    Q.name.addEventListener('change', () => { const p = sel(); if (p && Q.name.value.trim()) save(p.id, { name: Q.name.value.trim() }); });
    for (const [m, t] of [['translate', 'Taşı'], ['rotate', 'Döndür'], ['off', 'Gizmo kapat']]) {
      const b = btn(t, `gizmo-${m}`, () => setGizmo(m)); Q.gz.push([m, b]);
    }
    Q.card = el('div', { class: 'card', hidden: true }, [el('h2', { text: 'Seçili parça' }), field('Ad', Q.name),
      el('div', { class: 'pose-grid two' }, [field('Tür', Q.kind), field('Birim', Q.unit)]), field('Ölçek (çarpan)', Q.scale), Q.size,
      Q.title, grid, P.ui.gimbal, Q.toolNote, el('div', { class: 'row' }, [Q.tcp, ...Q.gz.map((g) => g[1]), Q.del]), P.ui.selSay]);
    return Q.card;
  }
  function buildApproach() {
    const A = P.ui.ap = { d: input('d', { value: String(DEFAULT_D_MM) }), speed: input('speed', { value: String(DEFAULT_SPEED_PCT) }),
      orient: select('orient', Object.entries(ORIENT_TEXT)), mode: select('orientmode', MODE_TEXT), modeHint: el('div', { class: 'say' }), info: el('div', { class: 'muted', text: 'Henüz nokta seçilmedi.' }),
      say: el('div', { class: 'say' }), warn: el('div', { class: 'say warn' }), why: el('div', { class: 'say' }) };
    A.mode.value = SL().defaultMoveOrientation(P.modeKey); A.orientField = field('Sabit yönelimde alet nasıl baksın', A.orient);
    for (const e of [A.d, A.speed, A.orient, A.mode]) e.addEventListener('input', () => { if (P.plan || P.target) clearPlan('Ayar değişti: yeniden kontrol et'); });
    A.mode.addEventListener('input', showMoveMode);
    A.near = btn('Yaklaş (mesafe kadar önünde dur)', 'approach', () => approach(false));
    A.go0 = btn('Git (yüzeye değ, d = 0)', 'touch', () => approach(true));
    A.ok = btn('Onayla ve hareket et', 'confirm', confirmMove, 'primary'); A.ok.hidden = true;
    return el('div', { class: 'card' }, [el('h2', { text: 'Dokun-git / Yaklaş' }),
      el('div', { class: 'say', text: '3B’de parça yüzeyine dokun: nokta ve yüzey normali alınır. Önce kontrol edilir, sonra onayınla hareket eder.' }),
      A.info, el('div', { class: 'pose-grid' }, [field('Mesafe d (mm)', A.d), field('Hız (%)', A.speed)]), field('Yönelim', A.mode), A.modeHint, A.orientField,
      el('div', { class: 'row' }, [A.near, A.go0]), A.why, A.warn, A.say, A.ok, el('div', { class: 'say warn', text: ASSUMPTION })]);
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
    for (const [n, t] of [['iso', 'İzometrik'], ['top', 'Üst'], ['front', 'Ön'], ['side', 'Yan']]) {
      row.append(btn(t, `cam-${n}`, () => { if (P.scene) P.scene.cameraPreset(n); }));
    }
    return row;
  }
  function hook() {
    unhook();
    const H = P.scene; if (!H) return;
    P.unsub = [H.onPick(onPick), H.onGizmoChange(onGizmo)]; H.setPickLayers(PICK_LAYERS);
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
    id: 'parts', title: 'Parçalar', icon: '🧩', order: 25, styles: ['/pages/parts.css'], scripts: ['/scene_logic.js', '/approach.js'],
    async mount(root) {
      P.root = root; P.view = el('div', { class: 'parts-view' });
      root.append(el('div', { class: 'page-grid two' }, [P.view, buildSide(buildViewTools())]));   // 3B tuval ızgaranın DOĞRUDAN çocuğu (sticky için)
      syncMoveMode(); await attachScene(); await reload(); P.fresh = true;                   // show() mount'tan hemen sonra onShow çağırır: ikinci yükleme gereksiz
    },
    onShow() {
      if (P.fresh) { P.fresh = false; return; }
      attachScene().then(reload);
    },
    onHide() {                                                              // sayfadan çıkınca: gizmo kapanır, plan düşer, işaretler kalkar
      unhook(); clearTimeout(P.delTimer); P.delTimer = null; P.ui.s.del.textContent = 'Sil';
      if (P.scene) { P.scene.disableGizmo(); P.scene.setPickLayers(DEFAULT_PICK); P.scene.select(null); }
      P.gizmo = null; P.pick = null; clearPlan(''); drop(AP.point); P.ui.ap.info.textContent = 'Henüz nokta seçilmedi.';
    },
    onState() { if (!P.root) return; if (estopped() || robotStale()) P.plan = null; syncMoveMode(); setApButtons(); },
  });
})();
