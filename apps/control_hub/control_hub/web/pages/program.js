/* Sayfa: Program (P2). AR4 program ekranı: waypoint tablosu, Move J/L adımları, ZORUNLU kuru çalıştırma ("Önizle"),
 * sunucu tarafı koşucu (Çalıştır/Adım/Duraklat/Devam/Durdur). Veri yalnız textContent ile yazılır.
 * Güvenlik: Çalıştır yalnız geçerli önizleme varken açılır, ama asıl denetim SUNUCUDADIR (arayüze güvenilmez). ACİL DUR üst
 * çubuktadır, burada kopyası yok. 3B (HubScene, S2): numaralı işaret, yol çizgisi, gizmo ile hedef, parçaya dokun.
 * R12: kararlar (metinler, düğme kapalılıkları, model düzenleme, sahne nesneleri) DOM'suz program_logic.js'tedir (window.ProgramLogic);
 * burası yalnız DOM çizer ve ağa konuşur. */
'use strict';

(() => {
  const POLL_MS = 500, CHECK_WAIT_MS = 350, ARM_MS = 3000, DEFAULT_SPEED = 25;
  let L = null;                                // window.ProgramLogic: aşağıdaki `ready` yükler

  /* ---- durum ve G/Ç ---- */
  const Q = {}, P = { prog: null, slug: null, dirty: false, speed: DEFAULT_SPEED, check: null, note: '', run: null, pose: null,
    backend: null, estop: false, real: false, control: true, confirm: null, armed: null, shown: false, bound: false, tgtOn: false, seq: 0, ids: [],
    rows: new Map(), timer: null, tgt: { pos: [0, 0, 0], rpy: [0, 0, 0], ok: null, why: 'Henüz denetlenmedi' } };
  /* Mantık betiği, sayfa açılmadan burada yüklenir: Algoritmalar sayfası Program sayfası HİÇ açılmamışken HubProgram.addSteps çağırabilir
   * (P.prog hazır olmalı). mount da bunu bekler; yüklenemezse hata yalnız bu sayfanın kutusunda görünür. */
  const ready = HubPages.loadScript('/program_logic.js').then(() => { L = window.ProgramLogic; P.prog = L.blank(); });
  ready.catch(() => {});
  const E = el, on = (e, ev, fn) => { e.addEventListener(ev, fn); return e; };
  const btn = (text, fn, cls = 'small', title = '') => on(E('button', { type: 'button', class: cls, text, title }), 'click', fn);
  const inp = (value, fn, props = {}) => on(E('input', { type: 'text', value: String(value ?? ''), autocomplete: 'off', ...props }), 'change', (ev) => fn(ev.target));
  const sig = () => L.sig(P.prog, P.speed);
  const numField = (v, min, max, apply, props, lo, hi) => inp(v, (i) => {   // geçersiz değer modele YAZILMAZ, alan kırmızı olur
    const a = L.numCheck(i.value, min, max, lo, hi);
    i.classList.toggle('bad', !a); if (a) apply(a);
  }, props);
  const scalar = (v, lo, hi, apply, props) => numField(v, 1, 1, ([x]) => apply(x), { inputMode: 'decimal', ...props }, lo, hi);

  async function call(path, method = 'GET', body) {   // hata fırlatmaz: {failed:true,status,error,message,body}
    try { return await api(path, method, body); } catch (e) { return { failed: true, status: e.status, error: (e.body || {}).error, message: e.message, body: e.body || {} }; }
  }
  function sure(key, text, fn) {               // iki dokunuş: yanlışlıkla silme / değişiklik kaybı olmasın
    if (P.armed === key) { P.armed = null; fn(); return; }
    P.armed = key; toast(`${text} — onaylamak için tekrar bas`); setTimeout(() => { if (P.armed === key) P.armed = null; }, ARM_MS);
  }

  /* Her düzenleme önizlemeyi GEÇERSİZ kılar (kabul kriteri); w/s: waypoint/adım listesi yeniden çizilsin mi. */
  function changed(w, s) {
    P.dirty = true; P.check = null; P.note = '';
    if (w) { renderWps(); syncScene(); }
    if (s) renderSteps();
    paint();
  }

  /* ---- waypoint tablosu ---- */
  function vecInp(w, key, lo, hi, title, digits) {   // boş bırakmak alanı siler (waypoint başka veri taşımalı; sunucu denetler)
    return inp(w[key] ? L.fmt(w[key], digits) : '', (i) => {
      const a = L.vec(i.value, lo, hi);
      i.classList.toggle('bad', !a); if (!a) return;
      if (a.length) w[key] = a; else delete w[key];
      changed(false, false);
    }, { title, placeholder: title });
  }

  async function gotoWp(w, joint) {
    const step = joint ? { kind: 'move_j', joints_deg: w.joints_deg } : { kind: 'move_l', pos_m: w.pos_m, rpy_deg: w.rpy_deg }, r = await call('/api/program/goto', 'POST', { step, speed_pct: P.speed });
    toast(r.failed ? r.message : `'${w.name}' konumuna gidiliyor`, !!r.failed);
  }

  function wpRow(w, i) {
    const wps = P.prog.waypoints, go = [];
    const name = inp(w.name, (e) => {
      const v = e.value.trim(), bad = L.nameBad(v, wps.map((x) => x.name), i);
      e.classList.toggle('bad', bad); if (bad) { toast('Ad geçersiz ya da kullanımda', true); return; }
      L.rename(P.prog, w.name, v); w.name = v; changed(false, true);   // adımlar yeni ada taşınır
    }, { title: 'Waypoint adı' });
    if (w.joints_deg) go.push(btn('Git J', () => gotoWp(w, true), 'small', 'Eklem hareketiyle git'));
    if (w.pos_m) go.push(btn('Git L', () => gotoWp(w, false), 'small', 'Doğrusal hareketle git'));
    return E('div', { class: 'pg-wp' }, [E('span', { class: 'pg-n', text: String(i + 1) }), name, vecInp(w, 'joints_deg', 1, 8, 'Eklemler (°)', 2),
      vecInp(w, 'pos_m', 3, 3, 'TCP x,y,z (m)', 4), vecInp(w, 'rpy_deg', 3, 3, 'rpy (°)', 2), E('div', { class: 'pg-act' }, [...go,
        btn('↑', () => { if (L.swap(wps, i, -1)) changed(true, true); }, 'small ic', 'Yukarı'),
        btn('↓', () => { if (L.swap(wps, i, 1)) changed(true, true); }, 'small ic', 'Aşağı'),
        btn('Kopya', () => { wps.splice(i + 1, 0, { ...JSON.parse(JSON.stringify(w)), name: L.unique(w.name, wps.map((x) => x.name)) }); changed(true, true); }),
        btn('✕', () => { wps.splice(i, 1); changed(true, true); }, 'small ic', 'Sil (bağlı adımlar kırmızı olur)')])]);
  }

  function renderWps() {
    const wps = P.prog.waypoints, none = E('div', { class: 'say', text: 'Waypoint yok. Robotu götürüp “Şu anki pozu kaydet”e bas.' });
    Q.wps.replaceChildren(...(wps.length ? wps.map(wpRow) : [none]));
  }
  async function savePose() {
    await refresh();
    const w = L.poseWaypoint(P.pose, P.prog.waypoints.map((x) => x.name));
    if (!w) toast('Robot pozu okunamadı', true); else { P.prog.waypoints.push(w); changed(true, true); }
  }

  /* ---- adımlar ---- */
  function wpSelect(cur, apply) {
    const opts = L.wpOptions(P.prog.waypoints.map((w) => w.name), cur).map((o) => E('option', o));
    const sel = on(E('select', { title: 'Waypoint' }, opts), 'change', () => apply(sel.value));
    sel.value = cur; return sel;
  }

  function moveEditor(s) {                     // move_j / move_l: waypoint'e bağlı ya da doğrudan değer
    const j = s.kind === 'move_j', byWp = 'waypoint' in s, put = (k) => (a) => { s[k] = a; changed(false, false); };
    const free = s.orientation === 'free' ? [E('span', { class: 'pg-free', text: 'yönelim: serbest', title: "orientation:'free': yönelimi çözücü belirler" })] : [];
    const src = on(E('select', { title: 'Hedef kaynağı' }, [E('option', { value: 'wp', text: 'Waypoint' }), E('option', { value: 'val', text: 'Değer' })]), 'change', () => { L.retarget(s, src.value, P.prog, P.pose); changed(false, true); });
    src.value = byWp ? 'wp' : 'val';
    if (byWp) return [src, wpSelect(s.waypoint, (v) => { s.waypoint = v; changed(false, true); }),
      ...(j ? [] : [numField(L.fmt(s.offset_m), 3, 3, put('offset_m'), { title: 'Ofset x,y,z (m)' }), ...free])];
    if (j) return [src, numField(L.fmt(s.joints_deg, 2), 1, 8, put('joints_deg'), { title: 'Eklemler (°)' })];
    return [src, numField(L.fmt(s.pos_m, 4), 3, 3, put('pos_m'), { title: 'x,y,z (m)' }),
      ...(free.length ? free : [numField(L.fmt(s.rpy_deg, 2), 3, 3, put('rpy_deg'), { title: 'rpy (°)' })])];
  }

  function editor(s, key) {
    if (s.kind === 'move_j' || s.kind === 'move_l') return moveEditor(s);
    if (s.kind === 'wait') return [scalar(s.s, 0, 3600, (v) => { s.s = v; changed(false, false); }, { title: 'Süre (sn)' }), E('span', { text: 'sn' })];
    if (s.kind === 'tool') return [scalar(s.open_pct, 0, 100, (v) => { s.open_pct = v; changed(false, false); }, { title: 'Açıklık %' }), E('span', { text: '% açık' })];
    if (s.kind === 'comment') return [inp(s.text, (e) => { s.text = e.value; changed(false, false); }, { title: 'Not', maxLength: 200 })];
    const count = scalar(s.count, 1, 1000, (v) => { s.count = Math.round(v); count.value = s.count; changed(false, false); }, { title: 'Tekrar sayısı' });
    return [E('span', { text: 'Tekrar:' }), count, E('div', { class: 'pg-nest' }, stepList(s.steps, key)), adder(s.steps, true)];
  }

  function stepList(list, parent) {
    return list.map((s, i) => {
      const key = parent == null ? `${i}` : `${parent}.${i}`, why = E('div', { class: 'pg-why' });
      const head = E('div', { class: 'pg-h' }, [E('span', { class: 'pg-n', text: String(i + 1) }), E('b', { text: L.KIND[s.kind] }), E('span', { class: 'pg-sp' }),
        btn('↑', () => { if (L.swap(list, i, -1)) changed(false, true); }, 'small ic', 'Yukarı'),
        btn('↓', () => { if (L.swap(list, i, 1)) changed(false, true); }, 'small ic', 'Aşağı'),
        btn('✕', () => { list.splice(i, 1); changed(false, true); }, 'small ic', 'Sil')]);
      const row = E('div', { class: 'pg-step', 'data-kind': s.kind }, [head, E('div', { class: 'pg-b' }, editor(s, key)), why]);
      P.rows.set(key, { row, why }); return row;
    });
  }

  function adder(list, inLoop) {
    const sel = E('select', { title: 'Adım türü' }, L.kinds(inLoop).map((k) => E('option', { value: k, text: L.KIND[k] })));
    return E('div', { class: 'pg-add' }, [sel, btn('+ Adım ekle', () => { list.push(L.newStep(sel.value, P.prog, P.pose)); changed(false, true); })]);   // 500 sınırı: sunucu reddeder
  }
  function renderSteps() {
    P.rows.clear();
    Q.steps.replaceChildren(...(P.prog.steps.length ? stepList(P.prog.steps, null) : [E('div', { class: 'say', text: 'Program boş. Adım ekle.' })]), adder(P.prog.steps, false));
  }

  /* ---- hedef öğretme: sayısal alan + (varsa) 3B gizmo / onPick; hedef önce ulaşılabilirlik denetiminden geçer ---- */
  function setTarget(pos, rpy, fromScene) {
    if (pos) P.tgt.pos = L.roundPos(pos);
    if (rpy) P.tgt.rpy = L.roundRpy(rpy);
    Q.tp.value = L.fmt(P.tgt.pos, 4); Q.tr.value = L.fmt(P.tgt.rpy, 2);
    P.tgt.ok = null; P.tgt.why = 'Denetleniyor…'; paintTarget();
    if (!fromScene) syncScene();               // sürükleme sırasında sahneyi yeniden kurma (gizmo kopar)
    clearTimeout(P.tgtTimer); P.tgtTimer = setTimeout(checkTarget, CHECK_WAIT_MS);
  }

  async function checkTarget() {
    const my = ++P.seq, r = await call('/api/program/check_move', 'POST', { step: { kind: 'move_l', pos_m: P.tgt.pos, rpy_deg: P.tgt.rpy }, speed_pct: P.speed });
    if (my !== P.seq) return;                  // eski yanıt yenisini ezmesin
    const v = L.targetVerdict(r); P.tgt.ok = v.ok; P.tgt.why = v.why;
    paintTarget();
  }

  const paintTarget = () => { const u = L.targetUi(P.tgt, P.control); Q.dot.className = u.cls; Q.tw.textContent = u.why; Q.tadd.disabled = u.addDisabled; };
  function addTarget() {                       // kırmızı/denetlenmemiş hedef waypoint OLAMAZ
    if (P.tgt.ok === true) { P.prog.waypoints.push(L.targetWaypoint(P.tgt, P.prog.waypoints.map((x) => x.name))); changed(true, true); }
  }

  /* ---- 3B sahne (HubScene, UI_CONTRACT §9-C): numaralı işaret, yol çizgisi, hedef çerçevesi + gizmo, parçaya dokun ----
   * İşlevler fırlatmaz, {ok:false} döner: her çağrının sonucu denetlenir. Kimlikler/katmanlar/nesne tanımları: program_logic.js. */
  const sceneOf = () => { const H = window.HubScene; return H && H.implemented !== false ? H : null; };

  function bindScene() {                       // bir kez abone ol; 3B yoksa atla (tablo ve sayısal hedef yine çalışır)
    const H = sceneOf(); if (!H || P.bound) return; P.bound = true;
    H.onPick((e) => { if (P.shown && e && Array.isArray(e.point_m)) setTarget(e.point_m, null, false); });
    H.onGizmoChange((e) => { if (P.shown && e && e.id === L.SC.tgt && e.pose && e.pose.pos_m) setTarget(e.pose.pos_m, e.pose.rpy_deg, true); });
  }

  function clearScene() {                      // sayfa gizlenince: işaretler, hedef ve gizmo kalkar; seçim katmanları varsayılana döner
    const H = sceneOf(); if (!H) return;
    [...P.ids, L.SC.tgt].forEach((id) => H.removeObject(id));
    H.disableGizmo(); H.setPickLayers(L.PICK_ALL); P.ids = []; P.tgtOn = false;
  }

  function syncScene() {                       // işaret + yol her waypoint değişiminde yenilenir; hedef çerçevesi kalır (gizmo kopmasın)
    const H = sceneOf(); if (!H || !P.shown) return;
    P.ids.forEach((id) => H.removeObject(id)); P.ids = [];
    const errs = [], put = (o, keep) => { const r = H.addObject(o); if (r.ok) { if (keep) P.ids.push(o.id); } else errs.push(r.message || r.error); };
    const pts = L.path(P.prog.waypoints), pose = { pos_m: P.tgt.pos, rpy_deg: P.tgt.rpy };
    pts.forEach((o) => put(L.markerSpec(o), true));
    if (pts.length > 1) put(L.pathSpec(pts), true);
    if (P.tgtOn) H.setPose(L.SC.tgt, pose);
    else { put(L.targetSpec(pose), false); P.tgtOn = errs.length === 0; if (P.tgtOn) H.enableGizmo(L.SC.tgt, { mode: 'translate', space: 'world' }); }
    H.setPickLayers(L.PICK); Q.scene.textContent = errs.length ? `3B sahne: ${errs[0]}` : '';
  }

  /* ---- çalıştırma ---- */
  const gateCtx = () => ({ prog: P.prog, check: P.check, sig: sig(), running: !!(P.run && P.run.running), estop: P.estop, canControl: P.control });

  async function refresh() {                   // durum + (çalışıyorsa) kalp atışı: 3 sn sessizlikte sunucu DURAKLATIR
    const r = await call('/api/program/state'); if (r.failed) return;
    P.run = r.runner; P.pose = r.pose; P.backend = r.backend; P.estop = !!r.estop; paint(); if (r.runner.running && P.shown) call('/api/program/heartbeat', 'POST');
  }
  async function preview() {
    P.note = 'Denetleniyor…'; paint();
    const s0 = sig(), r = await call('/api/program/check', 'POST', { program: P.prog, speed_pct: P.speed }), failed = !!r.failed;
    P.check = failed ? null : { sig: s0, res: r }; P.note = failed ? L.whyOf(r) : ''; paint();   // imza İSTEK anındaki programa bağlı: yanıt beklenirken düzenlendiyse geçersiz
  }

  async function start(mode, confirmed) {
    const g = L.gate(gateCtx()); if (!g.ok) { toast(g.why, true); return; }
    if (P.real && !confirmed) { P.confirm = mode; paint(); return; }   // gerçek mod: ek onay (yalnız uyarı; robot kapıları ayrı)
    P.confirm = null;
    const s0 = sig(), r = await call('/api/program/run', 'POST', { program: P.prog, speed_pct: P.speed, mode, confirm_real: P.real ? true : undefined });
    if (r.failed) {
      if (r.error === 'confirm_required') P.confirm = mode;
      if (r.error === 'program_invalid' && r.body.check) P.check = { sig: s0, res: r.body.check };
      toast(r.message, true);
    }
    await refresh();
  }

  async function control(op) {                 // step / pause / resume / stop
    const r = await call(`/api/program/${op}`, 'POST'); if (r.failed) toast(r.message, true); await refresh();
  }

  function paint() {
    if (!Q.run) return;
    const run = P.run || {}, g = L.gate(gateCtx()), b = L.buttons({ run, control: P.control, gate: g, empty: L.empty(P.prog) });
    const valid = P.check && P.check.sig === sig() ? P.check : null, ctx = { dangling: L.dangling(P.prog), check: valid, run };
    for (const [k, r] of P.rows) { const { cls, msgs } = L.rowInfo(ctx, k); r.row.className = `pg-step ${cls}`; r.why.textContent = msgs.join(' · '); }
    Q.run.disabled = b.run; Q.step.disabled = b.step; Q.prev.disabled = b.prev; Q.pause.disabled = b.pause; Q.resume.disabled = b.resume; Q.stop.disabled = b.stop;
    Q.gate.textContent = b.gate;
    Q.prevmsg.textContent = L.prevText(valid, P.note); Q.prevmsg.className = L.prevClass(valid);
    Q.status.textContent = L.status(run); Q.status.className = `say pg-status ${run.state || ''}`;
    for (const fs of [Q.fsFile, Q.fsWp, Q.fsSteps, Q.fsSpeed]) fs.disabled = b.ro;   // çalışırken düzenleme KAPALI
    const ex = L.exportLink(P.slug, P.dirty), mock = L.mockBanner(P.backend);
    Q.exp.hidden = ex.hidden; Q.exp.href = ex.href; Q.mock.hidden = mock.hidden; Q.mock.textContent = mock.text;
    Q.confirm.hidden = !P.confirm;
    Q.dirty.textContent = L.dirtyText(P.dirty, P.slug);
    paintTarget();
  }

  /* ---- kaydet / aç / sil / içe-dışa aktar ---- */
  async function loadList() {
    const r = await call('/api/program'); if (r.failed) return;
    Q.open.replaceChildren(E('option', { value: '', text: 'Kayıtlı programlar…' }), ...r.programs.map((p) => E('option', { value: p.slug, text: L.listLabel(p) })));
  }

  function adopt(prog, slug, dirty) { P.prog = prog; P.slug = slug; P.dirty = dirty; P.check = null; P.note = ''; render(); }
  async function save() {
    P.prog.name = Q.name.value.trim();
    const r = await call('/api/program/save', 'POST', { program: P.prog });
    if (r.failed) { toast(`Kaydedilemedi: ${L.whyOf(r)}`, true); return; }
    P.slug = r.slug; P.dirty = false; toast(`Kaydedildi: ${r.slug}`); await loadList(); paint();
  }

  async function openSlug(slug) {
    const r = slug ? await call(`/api/program/${slug}`) : { failed: true, message: '' };
    if (r.failed) { if (r.message) toast(r.message, true); } else adopt(r.program, slug, false);
  }
  async function deleteSlug() {
    const r = await call(`/api/program/${P.slug}`, 'DELETE');
    if (r.failed) toast(r.message, true); else { toast('Silindi'); adopt(L.blank(), null, false); loadList(); }
  }

  async function importFile(f) {
    if (!f) return;
    const r = L.fileTooBig(f.size) ? { failed: true, message: 'Dosya çok büyük (1 MB üstü)' } : await call('/api/program/import', 'POST', { text: await f.text() });
    if (r.failed) { toast(`İçe aktarılamadı: ${L.whyOf(r)}`, true); return; }
    adopt(r.program, null, true); toast('İçe aktarıldı: önce Önizle');
  }
  function render() { Q.name.value = P.prog.name; renderWps(); renderSteps(); paint(); syncScene(); }

  /* ---- sayfa iskeleti ---- */
  function build(root) {
    const sec = (title, kids, cls = '') => E('div', { class: `card pg-card ${cls}` }, [E('h2', { text: title }), ...kids]), fs = (kids, cls = '') => E('fieldset', { class: `pg-fs ${cls}` }, kids);
    Q.mock = E('div', { class: 'banner', role: 'status', hidden: true });
    Q.name = inp('', (e) => { P.prog.name = e.value.trim(); changed(false, false); }, { title: 'Program adı', placeholder: 'Program adı', maxLength: 40 });
    Q.open = on(E('select', { title: 'Kayıtlı program aç' }), 'change', () => { const go = () => openSlug(Q.open.value); if (P.dirty) sure('open', 'Kaydedilmemiş değişiklikler kaybolur', go); else go(); });
    Q.exp = E('a', { class: 'small pg-link', text: 'JSON indir', download: '', hidden: true });
    Q.imp = on(E('input', { type: 'file', accept: '.json,application/json', hidden: true }), 'change', () => { importFile(Q.imp.files[0]); Q.imp.value = ''; });
    Q.dirty = E('span', { class: 'say' }); Q.wps = E('div', { class: 'pg-list' }); Q.steps = E('div', { class: 'pg-list' });
    Q.fsFile = fs([Q.name, btn('Kaydet', save), Q.open, btn('Yeni', () => sure('new', 'Program sıfırlanır', () => adopt(L.blank(), null, false))),
      btn('Sil', () => { if (P.slug) sure('del', `'${P.slug}' silinecek`, deleteSlug); }), btn('JSON içe aktar', () => Q.imp.click()), Q.imp]);
    Q.fsWp = fs([Q.wps, btn('Şu anki pozu kaydet', savePose)]); Q.fsSteps = fs([Q.steps]);
    Q.speed = on(E('input', { type: 'range', min: 1, max: 100, value: P.speed, title: 'Hız %' }), 'input', () => { P.speed = +Q.speed.value; Q.spd.textContent = `%${P.speed}`; changed(false, false); });
    Q.spd = E('b', { text: `%${P.speed}` }); Q.prev = btn('Önizle', preview);
    Q.fsSpeed = fs([E('span', { text: 'Hız' }), Q.speed, Q.spd, Q.prev], 'pg-speed');
    Q.prevmsg = E('div', { class: 'pg-prev' }); Q.gate = E('div', { class: 'say' }); Q.status = E('div', { class: 'say pg-status' });
    Q.run = btn('Çalıştır', () => start('run', false), 'primary'); Q.step = btn('Adım', () => (P.run && P.run.running ? control('step') : start('step', false)));
    Q.pause = btn('Duraklat', () => control('pause')); Q.resume = btn('Devam', () => control('resume')); Q.stop = btn('Durdur', () => control('stop'), 'small danger');
    Q.confirm = E('div', { class: 'banner danger pg-confirm', role: 'alertdialog', hidden: true }, [E('div', { text: 'GERÇEK robot hareket edecek. ACİL DUR yanında mı?' }),
      E('div', { class: 'row' }, [btn('Evet, çalıştır', () => start(P.confirm || 'run', true), 'small danger'), btn('Vazgeç', () => { P.confirm = null; paint(); })])]);
    Q.view = E('div', { class: 'pg-view' }); Q.scene = E('div', { class: 'say', role: 'status' });
    Q.tp = numField(L.fmt(P.tgt.pos, 4), 3, 3, (a) => setTarget(a, null), { title: 'Hedef x,y,z (m)', placeholder: 'x, y, z (m)' });
    Q.tr = numField(L.fmt(P.tgt.rpy, 2), 3, 3, (a) => setTarget(null, a), { title: 'Hedef r,p,y (°)', placeholder: 'r, p, y (°)' });
    Q.dot = E('span', { class: 'pg-dot' }); Q.tw = E('span', { class: 'say' }); Q.tadd = btn('Waypoint olarak ekle', addTarget);
    root.append(E('div', { class: 'page-grid two pg' }, [
      E('div', { class: 'pg-side' }, [Q.view, Q.scene,
        sec('Hedef öğret', [E('div', { class: 'pg-tgt' }, [Q.tp, Q.tr]), E('div', { class: 'row' }, [Q.dot, Q.tw]), Q.tadd])]),
      E('div', { class: 'pg-main' }, [Q.mock, sec('Çalıştır', [Q.fsSpeed, Q.prevmsg, E('div', { class: 'pg-btns' }, [Q.run, Q.step, Q.pause, Q.resume, Q.stop]), Q.confirm, Q.gate, Q.status], 'pg-runcard'),
        sec('Program', [E('div', { class: 'row' }, [Q.dirty, Q.exp]), Q.fsFile]), sec('Waypoint tablosu', [Q.fsWp]), sec('Adımlar', [Q.fsSteps])])]));
  }

  /* Algoritmalar sayfası adımları buradan aktarır: sona eklenir, önizleme geçersiz olur. Sayfa henüz açılmamış olabilir
   * (Q boş): adımlar modele yazılır, mount render() ile gösterir. Sınırlar sunucuda da denetlenir (Önizle/Kaydet). */
  window.HubProgram = { addSteps(steps, name) {
    if (!L) return { ok: false, message: 'Program mantığı yükleniyor: birkaç saniye sonra tekrar dene' };
    const r = L.addSteps(P.prog, steps, name, !!(P.run && P.run.running));
    if (!r.ok) return r;
    P.dirty = true; P.check = null; P.note = '';
    if (Q.steps) { Q.name.value = P.prog.name; renderSteps(); paint(); }
    return r;
  } };

  HubPages.register({
    id: 'program', title: 'Program', icon: '▶', order: 30, styles: ['/pages/program.css'],
    async mount(root) {
      await ready;                             // mantık betiği hazır olmadan kurulmaz (hata sayfa kutusunda görünür)
      build(root); P.shown = true; render(); loadList(); P.timer = setInterval(refresh, POLL_MS);
      refresh().then(() => { if (P.pose && P.pose.pos_m) setTarget(P.pose.pos_m, P.pose.rpy_deg, false); });
      await HubPages.mountView(Q.view); bindScene(); syncScene();             // HubScene tuval kurulunca vardır
    },
    onShow() { P.shown = true; refresh(); if (!P.timer) P.timer = setInterval(refresh, POLL_MS); HubPages.mountView(Q.view).then(() => { bindScene(); syncScene(); }); },
    onHide() {                                 // K3h: sayfa gizlenince çalışan program DURAKLAR, 3B'deki işaretler kalkar
      P.shown = false; clearInterval(P.timer); P.timer = null; clearScene();
      if (P.run && P.run.state === 'running') call('/api/program/pause', 'POST');
    },
    onState(s) { const st = L.fromState(s); P.estop = st.estop; P.real = st.real; P.control = st.control; paint(); },
  });
})();
