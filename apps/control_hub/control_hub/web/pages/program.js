/* Sayfa: Program (P2). AR4 program ekranı: waypoint tablosu, Move J/L adımları, ZORUNLU kuru çalıştırma ("Önizle"),
 * sunucu tarafı koşucu (Çalıştır/Adım/Duraklat/Devam/Durdur). Veri yalnız textContent ile yazılır.
 * Güvenlik: Çalıştır yalnız geçerli önizleme varken açılır, ama asıl denetim SUNUCUDADIR (arayüze güvenilmez). ACİL DUR üst
 * çubuktadır, burada kopyası yok. HubScene (S2) varsa 3B işaret/yol/gizmo kullanılır; yoksa üstten tuval önizlemesi (sahte 3B). */
'use strict';

(() => {
  const POLL_MS = 500, CHECK_WAIT_MS = 350, ARM_MS = 3000, DEFAULT_SPEED = 25, MAX_FILE = 1_000_000;
  const ZERO6 = [0, 0, 0, 0, 0, 0], NAME_RE = /^[0-9A-Za-zÇĞİÖŞÜçğıöşü][0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]{0,39}$/;
  const KIND = { move_j: 'Eklem hareketi (J)', move_l: 'Doğrusal hareket (L)', wait: 'Bekle', tool: 'Alet', comment: 'Not', loop: 'Döngü' };

  /* ---- saf mantık (DOM yok; node testi doğrudan çağırır) ---- */
  const L = {
    blank: () => ({ version: 1, name: '', waypoints: [], steps: [] }),
    nums(text, min, max) {                     // "1, 2 3" -> [1,2,3]; NaN/∞/yanlış adet -> null
      const t = String(text).trim(), a = t ? t.split(/[\s,;]+/).map(Number) : [];
      return a.length >= min && a.length <= max && a.every(Number.isFinite) ? a : null;
    },
    fmt: (a, d = 3) => (a || []).map((v) => String(+Number(v).toFixed(d))).join(', '),
    unique(base, names) { const b = base.replace(/ \d+$/, ''); let n = 1; while (names.includes(`${b} ${n}`)) n++; return `${b} ${n}`; },
    key: (line, sub) => (sub == null ? `${line}` : `${line}.${sub}`),
    walk(prog, fn) { prog.steps.forEach((s, i) => { fn(s, `${i}`); if (s.kind === 'loop') s.steps.forEach((b, j) => fn(b, `${i}.${j}`)); }); },
    dangling(prog) {                           // satır anahtarı -> neden: silinmiş/yanlış waypoint'e bağlı adımlar
      const names = new Set(prog.waypoints.map((w) => w.name)), out = new Map();
      L.walk(prog, (s, k) => { if ('waypoint' in s && !names.has(s.waypoint)) out.set(k, `waypoint yok: '${s.waypoint}'`); });
      return out;
    },
    empty: (prog) => !prog.steps.some((s) => (s.kind === 'loop' ? s.steps.some((b) => b.kind !== 'comment') : s.kind !== 'comment')),
    sig: (prog, speed) => JSON.stringify([prog, speed]),
    rename(prog, from, to) { L.walk(prog, (s) => { if (s.waypoint === from) s.waypoint = to; }); },
    swap(arr, i, d) { const j = i + d; if (j < 0 || j >= arr.length) return false; [arr[i], arr[j]] = [arr[j], arr[i]]; return true; },
    path: (wps) => wps.map((w, i) => ({ n: i + 1, p: w.pos_m })).filter((o) => o.p),
    gate({ prog, check, sig, running, estop, canControl }) {   // Çalıştır kapısı: neden metni düğmenin yanında görünür
      const no = (why) => ({ ok: false, why }), d = L.dangling(prog);
      if (!canControl) return no('Kontroller kilitli');
      if (estop) return no('ACİL DUR aktif');
      if (running) return no('Program çalışıyor');
      if (L.empty(prog)) return no('Program boş');
      if (d.size) return no(`Silinmiş waypoint'e bağlı ${d.size} adım var`);
      if (!check || check.sig !== sig) return no('Önce "Önizle" ile kuru çalıştır');
      return check.res.ok ? { ok: true, why: '' } : no(`Önizlemede ${check.res.violations} ihlal var`);
    },
    status(run) {                              // koşucu durumundan tek satır metin
      if (!run || !run.state || run.state === 'idle') return '';
      const er = run.error, at = er ? `satır ${er.line + 1}${er.sub != null ? `.${er.sub + 1}` : ''}` : '';
      const main = run.state === 'failed' ? `HATA (${at}): ${er ? er.message : run.say}` : run.say || '';
      return main + (run.running ? ` · ${run.done}/${run.total}${run.iteration ? ` · tur ${run.iteration}` : ''}` : '') + (run.pause_reason ? ` · ${run.pause_reason}` : '');
    },
    prevText(valid, note) {
      if (note || !valid) return note || 'Önizleme yok / program değişti';
      return valid.res.ok ? `✓ Önizleme geçti (${valid.res.rows.length} hareket denetlendi)` : `✗ ${valid.res.violations} ihlal: kırmızı satırlara bak`;
    },
    rowInfo({ dangling, check, run }, key) {   // satır rengi + nedenler (kuru çalıştırma, silinmiş waypoint, çalışma hatası)
      const msgs = [], row = check && check.res.rows.find((r) => L.key(r.line, r.sub) === key), er = run && run.error; let cls = '';
      if (dangling.has(key)) { cls = 'bad'; msgs.push(dangling.get(key)); }
      if (row && row.ok) cls = cls || 'ok';
      if (row && !row.ok) { cls = 'bad'; msgs.push(...row.violations.map((v) => v.message)); }
      if (er && L.key(er.line, er.sub) === key) { cls = 'bad'; msgs.push(`Çalışma hatası: ${er.message}`); }
      if (run && run.running && run.line != null && L.key(run.line, run.sub) === key) cls = 'run';
      return { cls, msgs };
    },
  };

  /* ---- durum ve G/Ç ---- */
  const Q = {}, P = { prog: L.blank(), slug: null, dirty: false, speed: DEFAULT_SPEED, check: null, note: '', run: null, pose: null,
    backend: null, estop: false, real: false, control: true, confirm: null, armed: null, shown: false, bound: false, seq: 0, ids: [],
    rows: new Map(), timer: null, tgt: { pos: [0, 0, 0], rpy: [0, 0, 0], ok: null, why: 'Henüz denetlenmedi' } };
  const E = el, on = (e, ev, fn) => { e.addEventListener(ev, fn); return e; };
  const btn = (text, fn, cls = 'small', title = '') => on(E('button', { type: 'button', class: cls, text, title }), 'click', fn);
  const inp = (value, fn, props = {}) => on(E('input', { type: 'text', value: String(value ?? ''), autocomplete: 'off', ...props }), 'change', (ev) => fn(ev.target));
  const sig = () => L.sig(P.prog, P.speed);
  const whyOf = (r) => { const e = r.body && r.body.errors && r.body.errors[0]; return e ? `${r.message}: ${e.path}: ${e.message}` : r.message; };
  const numField = (v, min, max, apply, props, lo, hi) => inp(v, (i) => {   // geçersiz değer modele YAZILMAZ, alan kırmızı olur
    const a = L.nums(i.value, min, max), ok = !!a && (lo == null || (a[0] >= lo && a[0] <= hi));
    i.classList.toggle('bad', !ok); if (ok) apply(a);
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
    if (w) renderWps();
    if (s) renderSteps();
    paint(); syncScene();
  }

  /* ---- waypoint tablosu ---- */
  function vecInp(w, key, lo, hi, title, digits) {   // boş bırakmak alanı siler (waypoint başka veri taşımalı; sunucu denetler)
    return inp(w[key] ? L.fmt(w[key], digits) : '', (i) => {
      const a = i.value.trim() === '' ? [] : L.nums(i.value, lo, hi);
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
      const v = e.value.trim(), bad = !NAME_RE.test(v) || wps.some((x, k) => k !== i && x.name === v);
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
    const p = P.pose || {}, w = { name: L.unique('Nokta', P.prog.waypoints.map((x) => x.name)) };
    if (p.joints_deg) w.joints_deg = p.joints_deg.map((v) => +v.toFixed(3));
    if (p.pos_m && p.rpy_deg) { w.pos_m = p.pos_m.map((v) => +v.toFixed(5)); w.rpy_deg = p.rpy_deg.map((v) => +v.toFixed(3)); }
    if (!w.joints_deg && !w.pos_m) toast('Robot pozu okunamadı', true); else { P.prog.waypoints.push(w); changed(true, true); }
  }

  /* ---- adımlar ---- */
  function newStep(kind) {
    const last = P.prog.waypoints[P.prog.waypoints.length - 1], pose = P.pose || {};
    if (kind === 'move_j') return last && last.joints_deg ? { kind, waypoint: last.name } : { kind, joints_deg: (pose.joints_deg || ZERO6).slice() };
    if (kind === 'move_l') return last && last.pos_m ? { kind, waypoint: last.name, offset_m: [0, 0, 0] }
      : { kind, pos_m: (pose.pos_m || [0, 0, 0]).slice(), rpy_deg: (pose.rpy_deg || [0, 0, 0]).slice() };   // poz yoksa 0: denetim kırmızı verir
    if (kind === 'wait') return { kind, s: 1 };
    if (kind === 'tool') return { kind, open_pct: typeof pose.open_pct === 'number' ? pose.open_pct : 50 };
    return kind === 'comment' ? { kind, text: '' } : { kind: 'loop', count: 2, steps: [] };
  }

  function wpSelect(cur, apply) {
    const names = P.prog.waypoints.map((w) => w.name), opts = [E('option', { value: '', text: '— seç —' }), ...names.map((n) => E('option', { value: n, text: n }))];
    if (cur && !names.includes(cur)) opts.push(E('option', { value: cur, text: `${cur} (YOK)` }));   // silinmiş waypoint görünür kalır
    const sel = on(E('select', { title: 'Waypoint' }, opts), 'change', () => apply(sel.value));
    sel.value = cur; return sel;
  }

  function moveEditor(s) {                     // move_j / move_l: waypoint'e bağlı ya da doğrudan değer
    const j = s.kind === 'move_j', byWp = 'waypoint' in s, put = (k) => (a) => { s[k] = a; changed(false, false); };
    const src = on(E('select', { title: 'Hedef kaynağı' }, [E('option', { value: 'wp', text: 'Waypoint' }), E('option', { value: 'val', text: 'Değer' })]), 'change', () => {
      const from = P.prog.waypoints.find((x) => x.name === s.waypoint) || P.pose || {};
      for (const k of ['waypoint', 'offset_m', 'joints_deg', 'pos_m', 'rpy_deg']) delete s[k];
      if (src.value === 'wp') { s.waypoint = (P.prog.waypoints[0] || {}).name || ''; if (!j) s.offset_m = [0, 0, 0]; }
      else if (j) s.joints_deg = (from.joints_deg || ZERO6).slice();
      else { s.pos_m = (from.pos_m || [0, 0, 0]).slice(); s.rpy_deg = (from.rpy_deg || [0, 0, 0]).slice(); }
      changed(false, true);
    });
    src.value = byWp ? 'wp' : 'val';
    if (byWp) return [src, wpSelect(s.waypoint, (v) => { s.waypoint = v; changed(false, true); }),
      ...(j ? [] : [numField(L.fmt(s.offset_m), 3, 3, put('offset_m'), { title: 'Ofset x,y,z (m)' })])];
    if (j) return [src, numField(L.fmt(s.joints_deg, 2), 1, 8, put('joints_deg'), { title: 'Eklemler (°)' })];
    return [src, numField(L.fmt(s.pos_m, 4), 3, 3, put('pos_m'), { title: 'x,y,z (m)' }), numField(L.fmt(s.rpy_deg, 2), 3, 3, put('rpy_deg'), { title: 'rpy (°)' })];
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
      const head = E('div', { class: 'pg-h' }, [E('span', { class: 'pg-n', text: String(i + 1) }), E('b', { text: KIND[s.kind] }), E('span', { class: 'pg-sp' }),
        btn('↑', () => { if (L.swap(list, i, -1)) changed(false, true); }, 'small ic', 'Yukarı'),
        btn('↓', () => { if (L.swap(list, i, 1)) changed(false, true); }, 'small ic', 'Aşağı'),
        btn('✕', () => { list.splice(i, 1); changed(false, true); }, 'small ic', 'Sil')]);
      const row = E('div', { class: 'pg-step', 'data-kind': s.kind }, [head, E('div', { class: 'pg-b' }, editor(s, key)), why]);
      P.rows.set(key, { row, why }); return row;
    });
  }

  function adder(list, inLoop) {               // döngü içinde döngü YOK (tek seviye)
    const sel = E('select', { title: 'Adım türü' }, Object.keys(KIND).filter((k) => !(inLoop && k === 'loop')).map((k) => E('option', { value: k, text: KIND[k] })));
    return E('div', { class: 'pg-add' }, [sel, btn('+ Adım ekle', () => { list.push(newStep(sel.value)); changed(false, true); })]);   // 500 sınırı: sunucu reddeder
  }
  function renderSteps() {
    P.rows.clear();
    Q.steps.replaceChildren(...(P.prog.steps.length ? stepList(P.prog.steps, null) : [E('div', { class: 'say', text: 'Program boş. Adım ekle.' })]), adder(P.prog.steps, false));
  }

  /* ---- hedef öğretme: sayısal alan + (varsa) 3B gizmo / onPick; hedef önce ulaşılabilirlik denetiminden geçer ---- */
  function setTarget(pos, rpy, fromScene) {
    if (pos) P.tgt.pos = pos.slice(0, 3).map((v) => +v.toFixed(4));
    if (rpy) P.tgt.rpy = rpy.slice(0, 3).map((v) => +v.toFixed(2));
    Q.tp.value = L.fmt(P.tgt.pos, 4); Q.tr.value = L.fmt(P.tgt.rpy, 2);
    P.tgt.ok = null; P.tgt.why = 'Denetleniyor…'; paintTarget();
    if (!fromScene) syncScene();               // sürükleme sırasında sahneyi yeniden kurma (gizmo kopar)
    clearTimeout(P.tgtTimer); P.tgtTimer = setTimeout(checkTarget, CHECK_WAIT_MS);
  }

  async function checkTarget() {
    const my = ++P.seq, r = await call('/api/program/check_move', 'POST', { step: { kind: 'move_l', pos_m: P.tgt.pos, rpy_deg: P.tgt.rpy }, speed_pct: P.speed });
    if (my !== P.seq) return;                  // eski yanıt yenisini ezmesin
    P.tgt.ok = !r.failed && r.ok === true;
    P.tgt.why = r.failed ? r.message : P.tgt.ok ? 'Ulaşılabilir' : ((r.violations || [])[0] || {}).message || 'Reddedildi';
    paintTarget();
  }

  const paintTarget = () => {
    Q.dot.className = `pg-dot ${P.tgt.ok === true ? 'ok' : P.tgt.ok === false ? 'bad' : ''}`; Q.tw.textContent = P.tgt.why; Q.tadd.disabled = P.tgt.ok !== true || !P.control;
  };
  function addTarget() {                       // kırmızı/denetlenmemiş hedef waypoint OLAMAZ
    if (P.tgt.ok === true) { P.prog.waypoints.push({ name: L.unique('Nokta', P.prog.waypoints.map((x) => x.name)), pos_m: P.tgt.pos.slice(), rpy_deg: P.tgt.rpy.slice() }); changed(true, true); }
  }

  /* ---- 3B sahne (S2 gelince) + üstten tuval önizlemesi (sahte) ---- */
  function bindScene() {                       // S2 yoksa sessizce atlanır (özellik algılama); iskelet "uygulanmadı" fırlatabilir
    const H = window.HubScene; if (!H || P.bound) return;
    try {
      if (H.onPick) H.onPick((e) => { if (P.shown && e && Array.isArray(e.point_m)) setTarget(e.point_m, null, false); });
      if (H.onGizmoChange) H.onGizmoChange((e) => { const p = e && (e.pose || e); if (P.shown && p && p.pos_m) setTarget(p.pos_m, p.rpy_deg, true); });
      P.bound = true;
    } catch (e) { console.error(e); }
  }

  function clearScene() {
    try { if (window.HubScene && HubScene.removeObject) P.ids.forEach((id) => HubScene.removeObject(id)); } catch (e) { console.error(e); }
    P.ids = [];
  }

  function syncScene() {
    drawPath();
    const H = window.HubScene; if (!H || !H.addObject || !P.shown) return;
    try {
      clearScene();
      const add = (o) => { H.addObject(o); P.ids.push(o.id); }, pts = L.path(P.prog.waypoints);
      pts.forEach(({ n, p }) => add({ id: `pg-wp-${n}`, kind: 'marker', layer: 'markers', pose: { pos_m: p, rpy_deg: P.prog.waypoints[n - 1].rpy_deg }, color: '#4f8cff', label: String(n) }));
      if (pts.length > 1) add({ id: 'pg-path', kind: 'polyline', layer: 'path', geometry: { points_m: pts.map((o) => o.p) }, color: '#e3b341' });
      add({ id: 'pg-target', kind: 'frame', layer: 'markers', pose: { pos_m: P.tgt.pos, rpy_deg: P.tgt.rpy }, label: 'Hedef' });
      if (H.enableGizmo) H.enableGizmo('pg-target', { mode: 'translate', space: 'world' });
    } catch (e) { console.error(e); }
  }

  function drawPath() {                        // üstten (x–y): numaralı işaretler + yol çizgisi
    const c = Q.cv, g = c && c.getContext && c.getContext('2d'); if (!g) return;
    const pts = L.path(P.prog.waypoints), W = c.width, H = c.height, pad = 30;
    g.fillStyle = '#0a0e14'; g.fillRect(0, 0, W, H); g.font = '14px system-ui';
    if (!pts.length) { g.fillStyle = '#8b949e'; g.fillText('TCP konumlu waypoint yok', pad, H / 2); return; }
    const xs = pts.map((o) => o.p[0]), ys = pts.map((o) => o.p[1]), x0 = Math.min(...xs), y0 = Math.min(...ys);
    const k = (Math.min(W, H) - 2 * pad) / Math.max(Math.max(...xs) - x0, Math.max(...ys) - y0, 0.05), at = (p) => [pad + (p[0] - x0) * k, H - pad - (p[1] - y0) * k];
    g.strokeStyle = '#e3b341'; g.lineWidth = 2; g.beginPath();
    pts.forEach((o, i) => { const [x, y] = at(o.p); if (i) g.lineTo(x, y); else g.moveTo(x, y); }); g.stroke();
    pts.forEach((o) => { const [x, y] = at(o.p); g.fillStyle = '#4f8cff'; g.beginPath(); g.arc(x, y, 11, 0, 7); g.fill();
      g.fillStyle = '#fff'; g.textAlign = 'center'; g.fillText(String(o.n), x, y + 5); g.textAlign = 'start'; });
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
    P.check = failed ? null : { sig: s0, res: r }; P.note = failed ? whyOf(r) : ''; paint();   // imza İSTEK anındaki programa bağlı: yanıt beklenirken düzenlendiyse geçersiz
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
    const run = P.run || {}, running = !!run.running, ro = running || !P.control, g = L.gate(gateCtx());
    const valid = P.check && P.check.sig === sig() ? P.check : null, ctx = { dangling: L.dangling(P.prog), check: valid, run };
    for (const [k, r] of P.rows) { const { cls, msgs } = L.rowInfo(ctx, k); r.row.className = `pg-step ${cls}`; r.why.textContent = msgs.join(' · '); }
    Q.run.disabled = !g.ok; Q.step.disabled = running ? run.state !== 'paused' || !P.control : !g.ok; Q.prev.disabled = ro || L.empty(P.prog);
    Q.pause.disabled = !(run.state === 'running' && P.control); Q.resume.disabled = !(run.state === 'paused' && P.control); Q.stop.disabled = !(running && P.control);
    Q.gate.textContent = running ? '' : g.ok ? 'Önizleme geçti: çalıştırılabilir.' : g.why;
    Q.prevmsg.textContent = L.prevText(valid, P.note); Q.prevmsg.className = `pg-prev ${valid ? (valid.res.ok ? 'ok' : 'bad') : ''}`;
    Q.status.textContent = L.status(run); Q.status.className = `say pg-status ${run.state || ''}`;
    for (const fs of [Q.fsFile, Q.fsWp, Q.fsSteps, Q.fsSpeed]) fs.disabled = ro;   // çalışırken düzenleme KAPALI
    Q.exp.hidden = !P.slug || P.dirty; Q.exp.href = P.slug ? `/api/program/${P.slug}/export` : '';
    Q.mock.hidden = !(P.backend && P.backend.measured === false); Q.mock.textContent = `${P.backend ? P.backend.label : ''} hareket servisi: ölçüm değildir, gerçek robot hareket etmez.`;
    Q.confirm.hidden = !P.confirm;
    Q.dirty.textContent = P.dirty ? '● kaydedilmemiş' : P.slug ? `kayıtlı: ${P.slug}` : '';
    paintTarget();
  }

  /* ---- kaydet / aç / sil / içe-dışa aktar ---- */
  async function loadList() {
    const r = await call('/api/program'); if (r.failed) return;
    Q.open.replaceChildren(E('option', { value: '', text: 'Kayıtlı programlar…' }), ...r.programs.map((p) => E('option', { value: p.slug, text: `${p.name} (${p.steps ?? '?'} adım)` })));
  }

  function adopt(prog, slug, dirty) { P.prog = prog; P.slug = slug; P.dirty = dirty; P.check = null; P.note = ''; render(); }
  async function save() {
    P.prog.name = Q.name.value.trim();
    const r = await call('/api/program/save', 'POST', { program: P.prog });
    if (r.failed) { toast(`Kaydedilemedi: ${whyOf(r)}`, true); return; }
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
    const r = f.size > MAX_FILE ? { failed: true, message: 'Dosya çok büyük (1 MB üstü)' } : await call('/api/program/import', 'POST', { text: await f.text() });
    if (r.failed) { toast(`İçe aktarılamadı: ${whyOf(r)}`, true); return; }
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
    Q.cv = E('canvas', { class: 'pg-cv', width: 640, height: 320, title: 'Üstten yol önizlemesi (x–y)' }); Q.view = E('div', { class: 'pg-view' });
    Q.tp = numField(L.fmt(P.tgt.pos, 4), 3, 3, (a) => setTarget(a, null), { title: 'Hedef x,y,z (m)', placeholder: 'x, y, z (m)' });
    Q.tr = numField(L.fmt(P.tgt.rpy, 2), 3, 3, (a) => setTarget(null, a), { title: 'Hedef r,p,y (°)', placeholder: 'r, p, y (°)' });
    Q.dot = E('span', { class: 'pg-dot' }); Q.tw = E('span', { class: 'say' }); Q.tadd = btn('Waypoint olarak ekle', addTarget);
    root.append(E('div', { class: 'page-grid two pg' }, [
      E('div', { class: 'pg-side' }, [Q.view, sec('Yol önizlemesi (üstten, sahte 3B)', [Q.cv, E('div', { class: 'say', text: window.HubScene ? 'İşaretler ve yol 3B sahnede de çizilir.' : '3B işaret ve yol çizgisi sahne API’si (S2) gelince eklenir.' })]),
        sec('Hedef öğret', [E('div', { class: 'pg-tgt' }, [Q.tp, Q.tr]), E('div', { class: 'row' }, [Q.dot, Q.tw]), Q.tadd])]),
      E('div', { class: 'pg-main' }, [Q.mock, sec('Çalıştır', [Q.fsSpeed, Q.prevmsg, E('div', { class: 'pg-btns' }, [Q.run, Q.step, Q.pause, Q.resume, Q.stop]), Q.confirm, Q.gate, Q.status], 'pg-runcard'),
        sec('Program', [E('div', { class: 'row' }, [Q.dirty, Q.exp]), Q.fsFile]), sec('Waypoint tablosu', [Q.fsWp]), sec('Adımlar', [Q.fsSteps])])]));
  }

  HubPages.register({
    id: 'program', title: 'Program', icon: '▶', order: 30, styles: ['/pages/program.css'], logic: L,
    mount(root) {
      build(root); P.shown = true; render(); loadList();
      refresh().then(() => { if (P.pose && P.pose.pos_m) setTarget(P.pose.pos_m, P.pose.rpy_deg, true); });
      HubPages.mountView(Q.view); bindScene(); P.timer = setInterval(refresh, POLL_MS);
    },
    onShow() { P.shown = true; HubPages.mountView(Q.view); bindScene(); syncScene(); refresh(); if (!P.timer) P.timer = setInterval(refresh, POLL_MS); },
    onHide() {                                 // K3h: sayfa gizlenince çalışan program DURAKLAR, 3B'deki işaretler kalkar
      P.shown = false; clearInterval(P.timer); P.timer = null; clearScene();
      if (P.run && P.run.state === 'running') call('/api/program/pause', 'POST');
    },
    onState(s) { P.estop = !!(s.estop && s.estop.latched); P.real = !!(s.robot && s.robot.mode === 'real'); P.control = !!(s.me && s.me.can_control); paint(); },
  });
})();
