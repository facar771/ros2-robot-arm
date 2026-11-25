/* Sayfa: Robot parametreleri (P4). Profil depodan YALNIZ OKUNUR; düzenleme TASLAKTIR: doğrulanır, farkı gösterilir,
 * veri klasörüne kaydedilir / YAML olarak indirilir. Robota ya da Robot API'ye UYGULAMA YOK (KARAR GEREKLİ: Robot_1).
 * P4b: KAYITLI taslak "SİM'e uygula" ile yalnız SİM arka ucunun BELLEĞİNE geçici yüklenir; "Sıfırla" gerçek profile döner.
 * Doğrulamanın hakemi sunucudur (POST /api/params/validate); bu dosya yalnız çizer ve değişen alanları gönderir.
 * Veri textContent ile yazılır (innerHTML yok). Mantık: /params_form.js (saf), pot kuralları: /pot_logic.js. */
'use strict';

(() => {
  const PL = () => window.ParamsLogic;
  const D = { data: null, fields: new Map(), cur: {}, bad: new Map(), inputs: new Map(), errBoxes: [], derived: {}, res: null,
              seq: 0, timer: null, pending: false, busy: false, live: {}, liveEls: new Map(), liveTimer: null, potMissing: false,
              sim: null, program: false, simBusy: false, simMsg: [], simTimer: null };
  const R = {};                                                    // sabit arayüz öğeleri
  const NS = 'http://www.w3.org/2000/svg';
  const TAG = { measured: 'ok', estimated: 'est', placeholder: 'ph' };
  const tag = (q) => (q ? el('span', { class: `pr-tag ${TAG[q.tag] || 'ph'}`, text: q.label }) : '');
  const canControl = () => !!(S.snap && S.snap.me && S.snap.me.can_control);
  const edits = () => PL().changed([...D.fields.values()], D.cur, D.bad);

  /* Üst çubuk rozeti "TASLAK PROFİL (SİM)": shell.js (S1) yalnız SİM/TEZGÂH/GERÇEK pill'ini çizer; taslak profil etkinken
   * HER sayfada görünmesi gerekir. Bu betik uygulama açılışında yüklenir (mount tembeldir), rozet anlık görüntüden beslenir.
   * İSTEK S1: ShellLogic.modeInfo `snap.robot.profile_override`'ı okuyup #pill-profile'ı kendisi çizerse bu blok kalkar. */
  function syncBadge() {
    const o = S.snap && S.snap.robot && S.snap.robot.profile_override, on = !!(o && o.active), bar = document.querySelector('.pills');
    let pill = document.getElementById('pill-profile');
    if (!pill && on && bar) {
      pill = el('span', { class: 'pill', id: 'pill-profile', role: 'status', text: 'TASLAK PROFİL (SİM)' });
      pill.dataset.state = 'warn';
      const mode = document.getElementById('pill-mode');
      bar.insertBefore(pill, mode ? mode.nextSibling : null);
    }
    if (!pill) return;
    pill.hidden = !on;
    if (on) pill.title = `Taslak profil: ${o.changes} alan değişti. Yalnız SİM belleğinde; gerçek profil ve robot değişmedi.`;
  }
  setInterval(syncBadge, 700);

  /* ---------- alan öğeleri ---------- */
  function input(key, extra = {}) {
    const f = D.fields.get(key);
    if (!f) return el('span', { class: 'muted', text: '—' });
    const i = el('input', { type: 'text', inputMode: f.kind === 'float' ? 'decimal' : 'numeric', autocomplete: 'off', spellcheck: false,
      value: PL().fmt(D.cur[key], f.decimals), class: 'pr-in', 'data-key': key, ...extra });
    i.setAttribute('aria-label', f.label + (f.unit ? ` (${f.unit})` : ''));
    i.addEventListener('input', () => onEdit(key, i.value));
    D.inputs.set(key, i);
    return i;
  }
  const labeled = (key, text) => {
    const f = D.fields.get(key);
    return f ? el('label', { class: 'pr-lab' }, [el('span', { text: text || f.label }), input(key), el('span', { class: 'muted', text: f.unit })]) : '';
  };
  const errBox = (match) => { const e = el('div', { class: 'pr-errs', role: 'alert', hidden: true }); D.errBoxes.push({ e, match }); return e; };
  const card = (title, kids, badge) => el('section', { class: 'card pr-card' }, [el('h2', {}, [title, badge ? [' ', badge] : ''].flat()), ...kids]);
  const keysOf = (n) => (D.data.axes.find((a) => a.joint === n) || { keys: [] }).keys;

  /* ---------- bölümler ---------- */
  function axesTable() {
    const head = ['Eksen', 'Tür', 'Yön', ...PL().CORE.map((c) => PL().CORE_LABEL[c]), 'Adım/° · µs/°', 'Kalite'];
    const body = [];
    for (const a of D.data.axes) {
      const n = a.joint, f = (s) => `axes.${n}.${s}`;
      const dirCell = D.fields.has(f('direction')) ? input(f('direction'), { size: 3 }) : (D.derived[n] = el('span', { class: 'muted' }));
      const dv = el('span', { class: 'pr-derived' }); D.derived[`${n}:v`] = dv;
      const cells = [el('td', { text: n }), el('td', { text: a.motor === 'hobby_servo' ? 'servo' : a.motor === 'step' ? 'step' : '—' }),
        el('td', {}, [dirCell]), ...PL().CORE.map((c) => el('td', {}, [input(f(c))])), el('td', {}, [dv]),
        el('td', {}, [tag((D.fields.get(f('min_deg')) || {}).quality || (D.fields.get(f('home_deg')) || {}).quality)])];
      head.forEach((h, k) => cells[k].setAttribute('data-label', h));
      const more = keysOf(n).filter((k) => !PL().CORE.some((c) => k === f(c)) && k !== f('direction'));
      const tr = el('tr', {}, cells);
      body.push(tr);
      body.push(el('tr', { class: 'pr-more' }, [el('td', { colSpan: head.length }, [
        more.length ? el('details', {}, [el('summary', { text: `${n} · sürücü ayrıntıları (${more.length})` }),
          el('div', { class: 'pr-grid' }, more.map((k) => labeled(k)))]) : '', errBox((fld) => fld === `axes.${n}` || fld.startsWith(`axes.${n}.`))])]));
    }
    return el('div', { class: 'pr-wrap' }, [el('table', { class: 'pr-tbl pr-axes' }, [el('thead', {}, [el('tr', {}, head.map((h) => el('th', { text: h })))]),
      el('tbody', {}, body)])]);
  }

  function kinematics() {
    const rows = D.data.joints.map((n) => el('tr', {}, [el('td', { text: n, 'data-label': 'Eklem' }),
      ...'xyz'.split('').map((ax) => el('td', { 'data-label': `ofset ${ax}` }, [input(`kin.${n}.offset_${ax}_mm`)]))]));
    const links = [...D.fields.values()].filter((f) => f.key.endsWith('.length_mm'));
    const worst = links.map((f) => f.quality).filter(Boolean).some((q) => q.tag !== 'measured');
    return card('Kinematik', [el('p', { class: 'muted', text: 'Eklem ofsetleri ve link uzunlukları (mm). CAD kestirimi: ölçülene kadar tahmini.' }),
      el('div', { class: 'pr-wrap' }, [el('table', { class: 'pr-tbl' }, [el('thead', {}, [el('tr', {}, ['Eklem', 'ofset x', 'ofset y', 'ofset z'].map((h) => el('th', { text: h })))]),
        el('tbody', {}, rows)])]),
      el('div', { class: 'pr-grid' }, links.map((f) => el('label', { class: 'pr-lab' }, [el('span', { text: f.label }), input(f.key), tag(f.quality)]))),
      errBox((fld) => fld.startsWith('kin.'))], worst ? el('span', { class: 'pr-tag est', text: 'tahmini' }) : '');
  }

  function boxCard() {
    const mk = () => { const s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 240 170'); s.setAttribute('class', 'pr-svg'); s.setAttribute('role', 'img'); return s; };
    R.svgTop = mk(); R.svgSide = mk();
    R.svgTop.setAttribute('aria-label', 'Güvenli kutu, üstten (X–Y)'); R.svgSide.setAttribute('aria-label', 'Güvenli kutu, yandan (X–Z)');
    R.home = el('div', { class: 'say' });
    R.view3d = el('div', { class: 'pr-3d', hidden: true });
    return card('Güvenli kutu', [el('p', { class: 'muted', text: 'Robotun çalışma kutusu (dünya, mm). Home pozunda alet ucu kutunun içinde olmalı.' }),
      el('div', { class: 'pr-grid' }, ['x', 'y', 'z'].flatMap((ax) => [labeled(`safety.${ax}_min_mm`, `${ax} en küçük`), labeled(`safety.${ax}_max_mm`, `${ax} en büyük`)])),
      R.home, el('div', { class: 'pr-svgs' }, [R.svgTop, R.svgSide]), R.view3d, errBox((fld) => fld.startsWith('safety'))]);
  }

  const toolCard = () => card('Alet ofseti (TCP)', [el('p', { class: 'muted', text: D.data.active_tool ? `Aktif alet: ${D.data.active_tool}` : 'Aktif alet yok: taban TCP.' }),
    el('div', { class: 'pr-grid' }, ['x_mm', 'y_mm', 'z_mm', 'w_deg', 'p_deg', 'r_deg'].map((k) => labeled(`tcp.${k}`))), errBox((fld) => fld.startsWith('tcp'))]);

  function potCard() {
    const head = ['Eksen', 'Kanal', 'Ham en küçük', 'Ham en büyük', 'Açı @ en küçük °', 'Açı @ en büyük °', 'Canlı ham', 'Canlı açı', ''];
    const rows = D.data.pot_names.map((n) => {
      const live = el('td', { class: 'pr-live', text: '—' }), ang = el('td', { class: 'pr-live', text: '—' });
      D.liveEls.set(n, { live, ang });
      const cap = (field, label) => { const b = el('button', { class: 'small', type: 'button', text: label });
        b.addEventListener('click', () => capture(n, field)); return b; };
      const tds = [el('td', { text: n }), ...['channel', 'raw_min', 'raw_max', 'angle_at_min_deg', 'angle_at_max_deg'].map((s) => el('td', {}, [input(`pot.${n}.${s}`)])),
        live, ang, el('td', { class: 'pr-caps' }, [cap('raw_min', 'Min yakala'), cap('raw_max', 'Max yakala')])];
      tds.forEach((t, k) => t.setAttribute('data-label', head[k]));
      return [el('tr', {}, tds), el('tr', { class: 'pr-more' }, [el('td', { colSpan: head.length }, [errBox((fld) => fld.startsWith(`pot.${n}.`))])])];
    }).flat();
    R.potState = el('div', { class: 'say muted', text: 'Canlı pot verisi bekleniyor…' });
    return card('Pot kalibrasyonu', [el('p', { class: 'muted', text: `Ham ADC ↔ eklem açısı. Ham ${window.PotLogic ? PotLogic.INVALID_LOW : 8} ve altı / ${window.PotLogic ? PotLogic.INVALID_HIGH : 1015} ve üstü GEÇERSİZ. Kalibrasyon yalnız TASLAKTA tutulur (profil şemasında yok).` }),
      R.potState, el('div', { class: 'pr-wrap' }, [el('table', { class: 'pr-tbl pr-pot' }, [el('thead', {}, [el('tr', {}, head.map((h) => el('th', { text: h })))]), el('tbody', {}, rows)])])]);
  }

  /* ---------- düzenleme + doğrulama ---------- */
  function onEdit(key, text) {
    const f = D.fields.get(key), p = PL().parse(text, f.kind);
    if (p.error) D.bad.set(key, p.error); else { D.bad.delete(key); D.cur[key] = p.value; }
    schedule();
  }
  function schedule() {
    D.pending = true; clearTimeout(D.timer); paintGate();
    D.timer = setTimeout(validate, 250);
  }
  async function validate() {
    const mine = ++D.seq, e = edits();
    try {
      D.res = Object.keys(e).length ? await api('/api/params/validate', 'POST', { edits: e })
        : { ok: true, issues: [], changes: [], derived: D.data.derived, home_tcp_mm: D.data.home_tcp_mm };
    } catch (err) { if (mine !== D.seq) return; D.res = { ok: false, issues: [{ field: '_general', severity: 'error', message: err.message }], changes: [] }; }
    if (mine !== D.seq) return;                                    // daha yeni bir düzenleme var: bu yanıt bayat
    D.pending = false; paint();
  }

  /* ---------- çizim ---------- */
  const GROUP = { safety: 'güvenli kutu', tcp: 'alet ofseti' };
  const label = (field) => (D.fields.get(field) || {}).label || GROUP[field] || field;
  function paint() {
    const issues = [...((D.res && D.res.issues) || []), ...[...D.bad].map(([field, message]) => ({ field, severity: 'error', message }))];
    const map = PL().byField(issues), claimed = new Set();
    for (const [key, i] of D.inputs) {
      const msgs = map[key] || [], err = PL().hasError(map, key);
      i.classList.toggle('bad', err); i.classList.toggle('warn', !err && msgs.length > 0); i.setAttribute('aria-invalid', err ? 'true' : 'false');
      i.title = msgs.map((m) => m.message).join('\n');
    }
    for (const { e, match } of D.errBoxes) {
      const mine = issues.filter((x) => match(x.field));
      mine.forEach((x) => claimed.add(x));
      e.replaceChildren(...mine.map((x) => el('div', { class: x.severity === 'error' ? 'bad' : 'warn', text: `${label(x.field)}: ${x.message}` })));
      e.hidden = !mine.length;
    }
    const rest = issues.filter((x) => !claimed.has(x));
    R.general.replaceChildren(...rest.map((x) => el('div', { class: x.severity === 'error' ? 'bad' : 'warn', text: x.message })));
    R.general.hidden = !rest.length;
    paintDerived(); paintBox(); paintDiff(); paintGate();
  }

  function paintDerived() {
    const dv = (D.res && D.res.derived) || D.data.derived;
    for (const a of D.data.axes) {
      const d = dv[a.joint] || {}, cell = D.derived[`${a.joint}:v`];
      cell.textContent = d.steps_per_deg !== undefined ? `${d.steps_per_deg.toFixed(3)} adım/°`
        : d.us_per_deg !== undefined ? `${Math.abs(d.us_per_deg).toFixed(2)} µs/°` : '—';
      const sd = D.derived[a.joint];
      if (sd && d.servo_dir) sd.textContent = d.servo_dir > 0 ? '+ (servo)' : '− (servo)';
    }
  }

  function boxValues() { const v = {}; for (const [k, f] of D.fields) if (k.startsWith('safety.')) v[k] = D.cur[k] === undefined ? f.value : D.cur[k]; return v; }
  function paintBox() {
    const box = PL().boxOf(boxValues()), tcp = (D.res && D.res.home_tcp_mm) || D.data.home_tcp_mm;
    const inside = !((D.res && D.res.issues) || []).some((i) => i.field === 'safety' && i.severity === 'error');
    R.home.textContent = tcp ? `Home'da alet ucu: x ${tcp[0].toFixed(1)}, y ${tcp[1].toFixed(1)}, z ${tcp[2].toFixed(1)} mm — kutunun ${inside ? 'İÇİNDE' : 'DIŞINDA'}`
      : 'Home alet ucu hesaplanamadı (profil hatası)';
    R.home.className = `say ${inside ? '' : 'bad'}`;
    for (const [svg, view] of [[R.svgTop, 'top'], [R.svgSide, 'side']]) {
      svg.replaceChildren();
      if (!box) continue;
      const p = PL().project(box, tcp, view, 240, 170, 18), mk = (t, a) => { const n = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) n.setAttribute(k, v); return n; };
      svg.append(mk('rect', { x: p.rect.x, y: p.rect.y, width: Math.max(0, p.rect.w), height: Math.max(0, p.rect.h), class: 'pr-box' }));
      if (p.dot) svg.append(mk('circle', { cx: p.dot.x, cy: p.dot.y, r: 4, class: inside ? 'pr-dot' : 'pr-dot out' }));
      const t = mk('text', { x: 6, y: 12, class: 'pr-svg-t' }); t.textContent = view === 'top' ? 'üstten (X→, Y↑)' : 'yandan (X→, Z↑)'; svg.append(t);
    }
    scene3d(box);
  }

  async function scene3d(box) {                                    // S2 (HubScene) bitince açılır; öncesinde yalnız 2B önizleme
    try {
      if (!window.HubPages || !HubPages.scene) return;
      const sc = await HubPages.scene();
      if (!sc || !sc.implemented) return;
      if (R.view3d.hidden) { R.view3d.hidden = false; await HubPages.mountView(R.view3d); }
      sc.removeObject('params:safebox');
      if (!box) return;
      const c = ['x', 'y', 'z'].map((a) => (box[a][0] + box[a][1]) / 2000), s = ['x', 'y', 'z'].map((a) => (box[a][1] - box[a][0]) / 1000);
      sc.addObject({ id: 'params:safebox', kind: 'box', layer: 'safebox', geometry: { size_m: s }, pose: { pos_m: c, rpy_deg: [0, 0, 0] }, color: '#4f8cff', opacity: 0.18, label: 'güvenli kutu (taslak)' });
    } catch (e) { console.error(e); }
  }

  function paintDiff() {
    const ch = (D.res && D.res.changes) || [];
    const show = (v, f) => (v === null || v === undefined ? '—' : PL().fmt(v, f ? f.decimals : 3));
    R.diff.replaceChildren(...(ch.length ? [el('div', { class: 'pr-wrap' }, [el('table', { class: 'pr-tbl' }, [el('thead', {}, [el('tr', {}, ['Alan', 'Önce', 'Sonra', 'Birim', ''].map((h) => el('th', { text: h })))]),
      el('tbody', {}, ch.map((c) => { const f = D.fields.get(c.field); return el('tr', {}, [el('td', { text: c.label }), el('td', { class: 'pr-num', text: show(c.before, f) }),
        el('td', { class: 'pr-num pr-after', text: show(c.after, f) }), el('td', { text: c.unit }), el('td', {}, [el('span', { class: 'pr-tag est', text: 'tahmini' })])]); }))])])]
      : [el('div', { class: 'muted', text: 'Değişiklik yok: depodaki değerler gösteriliyor.' })]));
    R.diffN.textContent = ch.length ? `${ch.length} değişiklik` : '';
  }

  function paintGate() {
    const g = PL().saveGate({ res: D.res, edits: edits(), badCount: D.bad.size, canControl: canControl(), busy: D.busy, pending: D.pending });
    R.save.disabled = !g.ok; R.status.textContent = g.why; R.status.className = g.ok ? 'muted' : 'pr-why';
    const n = Object.keys(edits()).length;
    R.undo.disabled = !n && !D.bad.size; R.yaml.disabled = !(n && D.res && D.res.ok && !D.pending && !D.bad.size);
    R.drop.hidden = !(D.data && D.data.draft && D.data.draft.exists);
    paintSim();
  }

  /* ---------- eylemler ---------- */
  async function save() {
    D.busy = true; paintGate();
    try { const r = await api('/api/params/draft', 'POST', { edits: edits() }); toast(`Taslak kaydedildi (${r.changes} değişiklik) — robota UYGULANMADI`); await load(); }
    catch (e) { toast(e.message, true); if (e.body && e.body.issues) { D.res = e.body; paint(); } }
    D.busy = false; paintGate();
  }
  async function download() {
    try {
      const res = await fetch('/api/params/yaml', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ edits: edits() }) });
      if (!res.ok) { const j = await res.json().catch(() => ({})); toast(j.message || `HTTP ${res.status}`, true); return; }
      const url = URL.createObjectURL(await res.blob()), a = el('a', { href: url, download: `${D.data.robot}-taslak.yaml` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 3000);
    } catch (e) { toast(e.message, true); }
  }
  async function dropDraft() {
    try { await api('/api/params/draft', 'DELETE'); toast('Kayıtlı taslak silindi'); await load(); } catch (e) { toast(e.message, true); }
  }
  function revert() { for (const [k, f] of D.fields) { D.cur[k] = f.value; D.inputs.get(k).value = PL().fmt(f.value, f.decimals); } D.bad.clear(); schedule(); }

  /* ---------- SİM'e uygula / Sıfırla (P4b): KAYITLI taslak yalnız SİM belleğine geçici yüklenir ---------- */
  function simCard() {
    const mk = (text, cls, fn) => { const b = el('button', { class: `small ${cls}`, type: 'button', text }); b.addEventListener('click', fn); return b; };
    R.simState = el('div', { class: 'say', role: 'status' }); R.simWhy = el('div', { class: 'pr-why' });
    R.simErrs = el('div', { class: 'pr-errs', role: 'alert', hidden: true });
    R.simApply = mk("SİM'e uygula", 'primary', simApply); R.simReset = mk('Sıfırla (gerçek profile dön)', '', simReset);
    R.simCard = card("SİM'de dene (geçici)", [el('p', { class: 'muted', text: 'Kayıtlı taslak yalnız SİM arka ucuna GEÇİCİ yüklenir: eklem limitleri, güvenli kutu, hız/ivme tavanları. Gerçek robota, Robot API\u2019ye ve profil dosyasına YAZILMAZ; Hub yeniden başlayınca gerçek profil döner. Gerçek robot kipinde kapalı.' }),
      R.simState, el('div', { class: 'pr-sim-btns' }, [R.simApply, R.simReset]), R.simWhy, R.simErrs]);
    R.simCard.classList.add('pr-sim');
    return R.simCard;
  }
  function paintSim() {
    if (!R.simApply) return;
    const dr = D.data && D.data.draft, saved = (dr && dr.edits) || {}, o = D.sim && D.sim.override, on = !!(o && o.active);
    const g = PL().simGate({ sim: D.sim, hasDraft: !!(dr && dr.exists && Object.keys(saved).length), stale: !!(dr && dr.profile_stale),
      dirty: !PL().sameEdits(edits(), saved), canControl: canControl(), busy: D.simBusy, programRunning: D.program });
    R.simApply.disabled = !g.apply.ok; R.simReset.disabled = !g.reset.ok;
    R.simState.textContent = PL().overrideText(o); R.simCard.classList.toggle('on', on);
    const why = new Set();                                         // neden kapalı? (Sıfırla nedeni yalnız etkin profil varken anlamlı)
    if (!g.apply.ok) why.add(g.apply.why);
    if (!g.reset.ok && on) why.add(g.reset.why);
    R.simWhy.textContent = [...why].join(' · ');
    R.simErrs.replaceChildren(...D.simMsg.map((t) => el('div', { class: 'bad', text: t })));
    R.simErrs.hidden = !D.simMsg.length;
  }
  async function refreshSim() {
    if (typeof document.hidden !== 'undefined' && document.hidden) return;
    try { D.sim = await api('/api/params/sim'); } catch (e) { D.sim = null; }
    try { const p = await api('/api/program/state'); D.program = !!(p.runner && p.runner.running); } catch (e) { D.program = false; }   // program sayfası yoksa sorun değil
    paintSim();
  }
  const simReasons = (e) => {                                      // sunucunun NEDENİ: geçersiz taslakta alan alan
    const iss = ((e.body && e.body.issues) || []).filter((i) => i.severity === 'error').slice(0, 6);
    return iss.length ? iss.map((i) => `${label(i.field)}: ${i.message}`) : [e.message];
  };
  async function simCall(path, okText) {
    D.simBusy = true; D.simMsg = []; paintSim();
    try { const r = await api(path, 'POST', {}); toast(okText(r)); }
    catch (e) { D.simMsg = simReasons(e); toast(e.message, true); }
    D.simBusy = false; await refreshSim();
  }
  const simApply = () => simCall('/api/params/sim/apply', (r) => `SİM'e taslak profil uygulandı (${r.override.changes} alan)${r.rehomed ? ' — robot yeni limit dışındaydı: home\u2019a alındı' : ''}`);
  const simReset = () => simCall('/api/params/sim/reset', (r) => (r.noop ? 'Zaten gerçek profil' : `Gerçek profile dönüldü${r.rehomed ? ' — robot home\u2019a alındı' : ''}`));

  /* ---------- canlı pot (kalibrasyon yardımı; Hub'da pot uç noktası henüz yok: İSTEK Robot_3/Robot_10) ---------- */
  function capture(name, field) {
    const ch = D.cur[`pot.${name}.channel`], row = D.live[ch];
    if (!window.PotLogic || !PL().capturable(row)) { toast(`${name}: canlı geçerli/taze ham değer yok (önce kanalı gir)`, true); return; }
    const key = `pot.${name}.${field}`; D.inputs.get(key).value = String(row.raw); onEdit(key, String(row.raw));
  }
  async function livePoll() {
    if (!window.PotLogic || (typeof document.hidden !== 'undefined' && document.hidden)) return;
    try {
      const n = PotLogic.normalize((S.snap && S.snap.pot) || (D.potMissing ? null : await api('/api/pot')));
      D.live = Object.fromEntries(n.rows.map((r) => [r.ch, r]));
      R.potState.textContent = n.rows.length ? `${n.rows.length} kanal canlı${n.hz ? ` · ${n.hz} Hz` : ''}` : 'Kanal yok: firmware henüz pot örneği göndermedi';
    } catch (e) {
      D.live = {}; if (e.status === 404) D.potMissing = true;
      R.potState.textContent = e.status === 404 ? 'Canlı okuma yok: Hub’da pot uç noktası (GET /api/pot) henüz yok; ham değerleri elle gir.' : `Canlı pot alınamadı: ${e.message}`;
    }
    for (const [name, { live, ang }] of D.liveEls) {
      const row = D.live[D.cur[`pot.${name}.channel`]], st = row && PotLogic.status(row);
      live.textContent = row && row.raw !== null ? `${row.raw}${st.level === 'ok' ? '' : ' ⚠'}` : '—'; live.title = st ? st.text : '';
      const cal = { raw_min: D.cur[`pot.${name}.raw_min`], raw_max: D.cur[`pot.${name}.raw_max`], angle_at_min_deg: D.cur[`pot.${name}.angle_at_min_deg`], angle_at_max_deg: D.cur[`pot.${name}.angle_at_max_deg`] };
      const a = row && row.raw !== null ? PotLogic.angle(row.raw, cal) : null; ang.textContent = a === null ? '—' : `${a.toFixed(1)}°`;
    }
  }

  /* ---------- yükleme ---------- */
  function build(root) {
    D.fields = new Map(D.data.fields.map((f) => [f.key, f])); D.cur = {}; D.bad.clear(); D.inputs.clear(); D.errBoxes = []; D.derived = {}; D.liveEls.clear();
    for (const f of D.data.fields) D.cur[f.key] = f.value;
    const dr = D.data.draft;
    if (dr && dr.exists) for (const [k, v] of Object.entries(dr.edits || {})) if (D.fields.has(k)) D.cur[k] = v;
    R.general = el('div', { class: 'pr-errs', role: 'alert', hidden: true });
    R.diff = el('div'); R.diffN = el('span', { class: 'muted' });
    const mkBtn = (text, cls, fn) => { const b = el('button', { class: `small ${cls}`, type: 'button', text }); b.addEventListener('click', fn); return b; };
    R.save = mkBtn('Taslak kaydet', 'primary', save); R.yaml = mkBtn('YAML indir', '', download); R.undo = mkBtn('Geri al', '', revert); R.drop = mkBtn('Kayıtlı taslağı sil', '', dropDraft);
    R.status = el('span', { class: 'muted' });
    const notes = [...D.data.notes, ...(dr && dr.error ? [dr.error] : []), ...(dr && dr.stale ? ['Kayıtlı taslak, profilin eski bir sürümüne göre alınmış: depo profili sonradan değişti. Farkı kontrol et.'] : []),
      ...(dr && dr.exists && Object.keys(dr.edits || {}).length ? [`Kayıtlı taslak yüklendi (${dr.saved_at || ''}).`] : [])];
    root.replaceChildren(
      el('div', { class: 'pr-banner', role: 'note' }, [el('b', { text: 'TASLAK — robota UYGULANMAZ. ' }),
        `Robot: ${D.data.robot}. Değerler depo profilinden okunur; değişiklikler ayrı klasöre kaydedilir. Yalnız SİM'e geçici yüklenebilir (aşağıda); gerçek robota kim, nasıl uygulayacak: Robot_1 kararı.`]),
      ...notes.map((t) => el('div', { class: 'pr-note', text: t })), R.general,
      card('Eksenler', [el('p', { class: 'muted', text: 'Açı sınırları, hız/ivme tavanları ve home (°). Adım/° = motor adım/tur × mikroadım × dişli / 360.' }), axesTable()]),
      kinematics(), boxCard(), toolCard(), potCard(),
      card('Değişiklikler (önce → sonra)', [R.diff], R.diffN), simCard(),
      el('div', { class: 'pr-bar' }, [R.save, R.yaml, R.undo, R.drop, R.status]));
    D.res = null; D.pending = false;
    if (Object.keys(edits()).length) schedule(); else { D.res = { ok: true, issues: D.data.issues || [], changes: [] }; paint(); }
    paintBox();
  }

  async function load() {
    try { D.data = await api('/api/params'); build(D.root); }
    catch (e) { D.root.replaceChildren(el('div', { class: 'card' }, [el('h2', { text: 'Robot parametreleri' }),
      el('div', { class: 'say bad', role: 'alert', text: `Parametreler yüklenemedi: ${e.message}` })])); }
  }

  HubPages.register({
    id: 'params', title: 'Parametreler', icon: '⚙', order: 50,
    scripts: ['/pot_logic.js', '/params_form.js'], styles: ['/pages/params.css'],
    mount(root) { D.root = root; return load(); },
    onState() {
      const r = S.snap && S.snap.robot;
      if (D.data && D.sim && r) { D.sim.override = r.profile_override || null; D.sim.mode = r.mode; }   // başka istemci uygulayınca/sıfırlayınca hemen
      if (D.data) paintGate();
    },
    onShow() { if (D.data && !Object.keys(edits()).length && !D.busy) load(); clearInterval(D.liveTimer); D.liveTimer = setInterval(livePoll, 250);
      clearInterval(D.simTimer); refreshSim(); D.simTimer = setInterval(refreshSim, 1500); },
    onHide() { clearInterval(D.liveTimer); D.liveTimer = null; clearInterval(D.simTimer); D.simTimer = null; },
  });
})();
