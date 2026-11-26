/* Program sayfasının SAF mantığı (R12): DOM yok, fetch yok, saat yok → node ile test edilir (tests/js/program_logic_check.js).
 * pages/program.js yalnız çizer ve ağa konuşur; "ne gösterilecek / hangi düğme kapalı / modele ne yazılacak" kararları buradadır.
 * Güvenlik: Çalıştır kapısı (gate) burada YALNIZ gösterim içindir; asıl denetim SUNUCUDADIR (arayüze güvenilmez).
 * Nesne anahtar SIRASI sunucuya ve 3B sahneye giden gövdeleri belirler; değiştirme (davranış korunur). */
'use strict';

(() => {
  const MAX_FILE = 1_000_000;                  // içe aktarılan JSON üst sınırı (bayt); sunucu da denetler
  const MAX_STEPS = 500, NAME_MAX = 40;        // adım üst sınırı (döngü gövdesi dahil) ve program adı uzunluğu
  const MARK_R_M = 0.012, TGT_SIZE_M = 0.08;   // 3B: waypoint işareti yarıçapı, hedef çerçevesi boyu
  const COLOR_WP = '#4f8cff', COLOR_PATH = '#e3b341';
  const ZERO6 = [0, 0, 0, 0, 0, 0], NAME_RE = /^[0-9A-Za-zÇĞİÖŞÜçğıöşü][0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]{0,39}$/;
  const KIND = { move_j: 'Eklem hareketi (J)', move_l: 'Doğrusal hareket (L)', wait: 'Bekle', tool: 'Alet', comment: 'Not', loop: 'Döngü' };
  const TARGET_KEYS = ['waypoint', 'offset_m', 'joints_deg', 'pos_m', 'rpy_deg', 'orientation'];   // move adımının hedef alanları
  /* 3B kimlikleri `program:` önekli (başkasınınkine dokunma); seçim katmanları: işaretler/hedef seçilmesin. */
  const SC = { wp: 'program:wp', path: 'program:path', tgt: 'program:target' };
  const PICK = ['workpiece', 'environment'], PICK_ALL = ['workpiece', 'tool', 'environment', 'markers'];

  const blank = () => ({ version: 1, name: '', waypoints: [], steps: [] });
  function nums(text, min, max) {              // "1, 2 3" -> [1,2,3]; NaN/∞/yanlış adet -> null
    const t = String(text).trim(), a = t ? t.split(/[\s,;]+/).map(Number) : [];
    return a.length >= min && a.length <= max && a.every(Number.isFinite) ? a : null;
  }
  const fmt = (a, d = 3) => (a || []).map((v) => String(+Number(v).toFixed(d))).join(', ');
  function unique(base, names) { const b = base.replace(/ \d+$/, ''); let n = 1; while (names.includes(`${b} ${n}`)) n++; return `${b} ${n}`; }
  const key = (line, sub) => (sub == null ? `${line}` : `${line}.${sub}`);
  function walk(prog, fn) { prog.steps.forEach((s, i) => { fn(s, `${i}`); if (s.kind === 'loop') s.steps.forEach((b, j) => fn(b, `${i}.${j}`)); }); }
  function dangling(prog) {                    // satır anahtarı -> neden: silinmiş/yanlış waypoint'e bağlı adımlar
    const names = new Set(prog.waypoints.map((w) => w.name)), out = new Map();
    walk(prog, (s, k) => { if ('waypoint' in s && !names.has(s.waypoint)) out.set(k, `waypoint yok: '${s.waypoint}'`); });
    return out;
  }
  const empty = (prog) => !prog.steps.some((s) => (s.kind === 'loop' ? s.steps.some((b) => b.kind !== 'comment') : s.kind !== 'comment'));
  const sig = (prog, speed) => JSON.stringify([prog, speed]);
  const total = (steps) => steps.reduce((n, s) => n + 1 + (s.steps ? s.steps.length : 0), 0);   // döngü gövdesi dahil
  function rename(prog, from, to) { walk(prog, (s) => { if (s.waypoint === from) s.waypoint = to; }); }
  function swap(arr, i, d) { const j = i + d; if (j < 0 || j >= arr.length) return false; [arr[i], arr[j]] = [arr[j], arr[i]]; return true; }
  const path = (wps) => wps.map((w, i) => ({ n: i + 1, p: w.pos_m })).filter((o) => o.p);

  function gate({ prog, check, sig: cur, running, estop, canControl }) {   // Çalıştır kapısı: neden metni düğmenin yanında görünür
    const no = (why) => ({ ok: false, why }), d = dangling(prog);
    if (!canControl) return no('Kontroller kilitli');
    if (estop) return no('ACİL DUR aktif');
    if (running) return no('Program çalışıyor');
    if (empty(prog)) return no('Program boş');
    if (d.size) return no(`Silinmiş waypoint'e bağlı ${d.size} adım var`);
    if (!check || check.sig !== cur) return no('Önce "Önizle" ile kuru çalıştır');
    return check.res.ok ? { ok: true, why: '' } : no(`Önizlemede ${check.res.violations} ihlal var`);
  }
  function status(run) {                       // koşucu durumundan tek satır metin
    if (!run || !run.state || run.state === 'idle') return '';
    const er = run.error, at = er ? `satır ${er.line + 1}${er.sub != null ? `.${er.sub + 1}` : ''}` : '';
    const main = run.state === 'failed' ? `HATA (${at}): ${er ? er.message : run.say}` : run.say || '';
    return main + (run.running ? ` · ${run.done}/${run.total}${run.iteration ? ` · tur ${run.iteration}` : ''}` : '') + (run.pause_reason ? ` · ${run.pause_reason}` : '');
  }
  function prevText(valid, note) {
    if (note || !valid) return note || 'Önizleme yok / program değişti';
    return valid.res.ok ? `✓ Önizleme geçti (${valid.res.rows.length} hareket denetlendi)` : `✗ ${valid.res.violations} ihlal: kırmızı satırlara bak`;
  }
  const prevClass = (valid) => `pg-prev ${valid ? (valid.res.ok ? 'ok' : 'bad') : ''}`;
  function rowInfo({ dangling: dang, check, run }, k) {   // satır rengi + nedenler (kuru çalıştırma, silinmiş waypoint, çalışma hatası)
    const msgs = [], row = check && check.res.rows.find((r) => key(r.line, r.sub) === k), er = run && run.error; let cls = '';
    if (dang.has(k)) { cls = 'bad'; msgs.push(dang.get(k)); }
    if (row && row.ok) cls = cls || 'ok';
    if (row && !row.ok) { cls = 'bad'; msgs.push(...row.violations.map((v) => v.message)); }
    if (er && key(er.line, er.sub) === k) { cls = 'bad'; msgs.push(`Çalışma hatası: ${er.message}`); }
    if (run && run.running && run.line != null && key(run.line, run.sub) === k) cls = 'run';
    return { cls, msgs };
  }
  /* Düğme kapalılıkları (true = kapalı) + kapı metni. Çalışırken düzenleme KAPALI (ro); sunucu yine de denetler. */
  function buttons({ run, control, gate: g, empty: none }) {
    const r = run || {}, running = !!r.running, ro = running || !control;
    return { ro, run: !g.ok, step: running ? r.state !== 'paused' || !control : !g.ok, prev: ro || none,
      pause: !(r.state === 'running' && control), resume: !(r.state === 'paused' && control), stop: !(running && control),
      gate: running ? '' : g.ok ? 'Önizleme geçti: çalıştırılabilir.' : g.why };
  }
  const dirtyText = (dirty, slug) => (dirty ? '● kaydedilmemiş' : slug ? `kayıtlı: ${slug}` : '');
  const exportLink = (slug, dirty) => ({ hidden: !slug || dirty, href: slug ? `/api/program/${slug}/export` : '' });   // kaydedilmemişi indirtme
  const mockBanner = (backend) => ({ hidden: !(backend && backend.mock),
    text: `${backend ? backend.label : ''} hareket servisi: ölçüm değildir, gerçek robot hareket etmez.` });
  const fromState = (s) => ({ estop: !!(s.estop && s.estop.latched), real: !!(s.robot && s.robot.mode === 'real'), control: !!(s.me && s.me.can_control) });
  const listLabel = (p) => `${p.name} (${p.steps ?? '?'} adım)`;
  const fileTooBig = (size) => size > MAX_FILE;
  const whyOf = (r) => { const e = r.body && r.body.errors && r.body.errors[0]; return e ? `${r.message}: ${e.path}: ${e.message}` : r.message; };

  /* ---- alan doğrulama: geçersiz değer modele YAZILMAZ ---- */
  function numCheck(text, min, max, lo, hi) {  // geçerliyse sayı dizisi, değilse null (aralık yalnız İLK değere uygulanır)
    const a = nums(text, min, max);
    return a && (lo == null || (a[0] >= lo && a[0] <= hi)) ? a : null;
  }
  const vec = (text, lo, hi) => (String(text).trim() === '' ? [] : nums(text, lo, hi));   // boş = alanı sil (waypoint başka veri taşımalı)
  const nameBad = (v, names, i) => !NAME_RE.test(v) || names.some((n, k) => k !== i && n === v);
  const kinds = (inLoop) => Object.keys(KIND).filter((k) => !(inLoop && k === 'loop'));   // döngü içinde döngü YOK (tek seviye)
  function wpOptions(names, cur) {             // silinmiş waypoint görünür kalır: "(YOK)"
    const opts = [{ value: '', text: '— seç —' }, ...names.map((n) => ({ value: n, text: n }))];
    if (cur && !names.includes(cur)) opts.push({ value: cur, text: `${cur} (YOK)` });
    return opts;
  }

  /* ---- model düzenleme (saf: yalnız verilen nesneleri değiştirir) ---- */
  function newStep(kind, prog, pose) {
    const last = prog.waypoints[prog.waypoints.length - 1], p = pose || {};
    if (kind === 'move_j') return last && last.joints_deg ? { kind, waypoint: last.name } : { kind, joints_deg: (p.joints_deg || ZERO6).slice() };
    if (kind === 'move_l') return last && last.pos_m ? { kind, waypoint: last.name, offset_m: [0, 0, 0] }
      : { kind, pos_m: (p.pos_m || [0, 0, 0]).slice(), rpy_deg: (p.rpy_deg || [0, 0, 0]).slice() };   // poz yoksa 0: denetim kırmızı verir
    if (kind === 'wait') return { kind, s: 1 };
    if (kind === 'tool') return { kind, open_pct: typeof p.open_pct === 'number' ? p.open_pct : 50 };
    return kind === 'comment' ? { kind, text: '' } : { kind: 'loop', count: 2, steps: [] };
  }
  function retarget(s, mode, prog, pose) {     // move adımı: waypoint'e bağlı <-> doğrudan değer; eski değerden yeni alan türetilir
    const j = s.kind === 'move_j', from = prog.waypoints.find((x) => x.name === s.waypoint) || pose || {};
    for (const k of TARGET_KEYS) delete s[k];
    if (mode === 'wp') { s.waypoint = (prog.waypoints[0] || {}).name || ''; if (!j) s.offset_m = [0, 0, 0]; }
    else if (j) s.joints_deg = (from.joints_deg || ZERO6).slice();
    else { s.pos_m = (from.pos_m || [0, 0, 0]).slice(); s.rpy_deg = (from.rpy_deg || [0, 0, 0]).slice(); }
  }
  function poseWaypoint(pose, names) {         // "Şu anki pozu kaydet": robot pozu okunamadıysa null
    const p = pose || {}, w = { name: unique('Nokta', names) };
    if (p.joints_deg) w.joints_deg = p.joints_deg.map((v) => +v.toFixed(3));
    if (p.pos_m && p.rpy_deg) { w.pos_m = p.pos_m.map((v) => +v.toFixed(5)); w.rpy_deg = p.rpy_deg.map((v) => +v.toFixed(3)); }
    return w.joints_deg || w.pos_m ? w : null;
  }
  function addSteps(prog, steps, name, running) {   // Algoritmalar sayfası adımları sona ekler; kabul edilmezse model DEĞİŞMEZ
    if (!Array.isArray(steps) || !steps.length) return { ok: false, message: 'Eklenecek adım yok' };
    if (running) return { ok: false, message: 'Program çalışıyor: bitince ekle' };
    if (total(prog.steps) + total(steps) > MAX_STEPS) return { ok: false, message: `En çok ${MAX_STEPS} adım (şu an ${total(prog.steps)})` };
    prog.steps.push(...JSON.parse(JSON.stringify(steps)));
    if (!prog.name && typeof name === 'string') prog.name = name.trim().slice(0, NAME_MAX);   // adsız programa ad: Önizle/Kaydet ad ister
    return { ok: true, total: total(prog.steps) };
  }

  /* ---- hedef öğretme: sayılar yuvarlanır, hedef ulaşılabilirlik denetiminden geçmeden waypoint OLAMAZ ---- */
  const roundPos = (pos) => pos.slice(0, 3).map((v) => +v.toFixed(4));
  const roundRpy = (rpy) => rpy.slice(0, 3).map((v) => +v.toFixed(2));
  function targetVerdict(r) {                  // /api/program/check_move yanıtı (ya da {failed:true,message}) -> {ok, why}
    const ok = !r.failed && r.ok === true;
    return { ok, why: r.failed ? r.message : ok ? 'Ulaşılabilir' : ((r.violations || [])[0] || {}).message || 'Reddedildi' };
  }
  const targetUi = (tgt, control) => ({ cls: `pg-dot ${tgt.ok === true ? 'ok' : tgt.ok === false ? 'bad' : ''}`, why: tgt.why, addDisabled: tgt.ok !== true || !control });
  const targetWaypoint = (tgt, names) => ({ name: unique('Nokta', names), pos_m: tgt.pos.slice(), rpy_deg: tgt.rpy.slice() });

  /* ---- 3B sahne nesne tanımları (HubScene.addObject gövdeleri) ---- */
  const markerSpec = ({ n, p }) => ({ id: `${SC.wp}${n}`, kind: 'marker', layer: 'markers', pose: { pos_m: p }, geometry: { radius_m: MARK_R_M, number: n }, color: COLOR_WP });
  const pathSpec = (pts) => ({ id: SC.path, kind: 'polyline', layer: 'path', geometry: { points_m: pts.map((o) => o.p) }, color: COLOR_PATH });
  const targetSpec = (pose) => ({ id: SC.tgt, kind: 'frame', layer: 'markers', pose, geometry: { size_m: TGT_SIZE_M }, label: 'Hedef' });

  window.ProgramLogic = { MAX_FILE, MAX_STEPS, NAME_MAX, ZERO6, NAME_RE, KIND, SC, PICK, PICK_ALL,
    blank, nums, fmt, unique, key, walk, dangling, empty, sig, total, rename, swap, path, gate, status, prevText, prevClass, rowInfo,
    buttons, dirtyText, exportLink, mockBanner, fromState, listLabel, fileTooBig, whyOf, numCheck, vec, nameBad, kinds, wpOptions,
    newStep, retarget, poseWaypoint, addSteps, roundPos, roundRpy, targetVerdict, targetUi, targetWaypoint,
    markerSpec, pathSpec, targetSpec };
  if (typeof module !== 'undefined') module.exports = window.ProgramLogic;
})();
