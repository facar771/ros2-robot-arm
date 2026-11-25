/* Sayfa: Kontrol (P1; AR4 "Main Controls" karşılığı). 3B görünüm + canlı poz + eklem kaydırıcıları (min/max, hedef
 * ve gerçek ayrı) + jog (jog.js) + alet + Home + çerçeveler/düzlem öğretme (frames.js) + küre sihirbazı (sphere.js).
 * Adlandırılmış poz düğmesi YOK. "Git" önce /api/move/check sonra /api/move; ihlalde hareket komutu gitmez.
 * Sayfadan ayrılınca basılı-tut bırakılır (K3-h). Telefonda 3B kartı kompakt ve sabittir (jog.css). */
'use strict';

(() => {
  const L = new Proxy({}, { get: (_, k) => window.JogLogic[k] });      // jog_logic.js, sayfa betikleri yüklenince hazır olur
  const P = { view: null, joints: [], dirty: new Set(), gripDirty: false, built: false, snap: null, busy: false };
  const T = { on: false, pos: null, ok: false, seq: 0, scene: null, unsub: null, z: null };   // 3B tıkla-git durumu (P1b)
  const q = (id) => document.getElementById(id);
  const TOPBAR_VAR = '--topbar-h';
  const SLIDER_MARGIN_DEG = 0.1;       // anlık görüntüdeki limit 1 ondalığa yuvarlı: sunucunun reddedeceği uca çekilmesin

  const out = (id, text = '—') => el('output', { id, text });

  function buildPose() {
    return el('div', { class: 'card jog-pose', id: 'p-pose' }, [
      el('h2', { text: 'Canlı poz' }),
      el('div', { class: 'jp-badges' }, [el('span', { class: 'pill', id: 'jp-mode' }), el('span', { class: 'pill', id: 'jp-est' }),
                                          el('span', { class: 'pill', id: 'jp-stale', hidden: true, text: 'veri bayat' })]),
      el('div', { class: 'jp-pose', id: 'jp-pose-j' }), el('div', { class: 'jp-pose', id: 'jp-pose-t' })]);
  }
  function poseCells(host, labels, values, units, nears) {
    host.replaceChildren(...labels.map((l, i) => el('div', { class: `jp-cell${nears && nears[i] ? ' near' : ''}` },
      [el('span', { text: l }), el('b', { text: `${values[i]}${units}` })])));
  }

  function jointRow(j, i) {
    const range = el('input', { type: 'range', class: 'jp-slider', id: `jp-t${i}`, min: String(j.min_deg + SLIDER_MARGIN_DEG), max: String(j.max_deg - SLIDER_MARGIN_DEG),
                                step: '0.1', value: String(j.deg), 'aria-label': `${j.name} hedef` });
    range.addEventListener('input', () => { P.dirty.add(i); renderTargets(); });
    const actual = el('meter', { class: 'jp-actual', id: `jp-a${i}`, min: String(j.min_deg), max: String(j.max_deg),
                                 value: String(j.deg), 'aria-label': `${j.name} gerçek` });
    return el('div', { class: 'jp-jrow', 'data-joint': String(i) }, [
      el('div', { class: 'jp-jname' }, [el('b', { text: j.name }), el('span', { class: 'jp-warn', id: `jp-w${i}`, hidden: true })]),
      window.JogPanel.jointButton(i, -1, '−'),
      el('div', { class: 'jp-track' }, [range, actual,
        el('div', { class: 'jp-lim' }, [el('span', { text: `${L.poseRows({ joints: [j] }).joints[0].min}°` }),
                                         el('span', { text: `${L.poseRows({ joints: [j] }).joints[0].max}°` })])]),
      window.JogPanel.jointButton(i, +1, '+'),
      el('div', { class: 'jp-vals' }, [el('span', { text: 'Hedef ' }), out(`jp-tv${i}`), el('span', { text: ' · Gerçek ' }), out(`jp-av${i}`)])]);
  }

  function buildJoints(joints) {
    const host = q('jp-joints'); host.replaceChildren(...joints.map(jointRow));
    P.joints = joints.map((j) => ({ min: j.min_deg, max: j.max_deg })); P.built = true;
  }

  function targets() {
    return P.joints.map((_, i) => (P.dirty.has(i) ? Number(q(`jp-t${i}`).value) : ((P.snap.robot.joints[i] || {}).deg || 0)));
  }
  function renderTargets() {
    const t = targets();
    t.forEach((deg, i) => {
      const st = L.sliderState(deg, P.joints[i].min, P.joints[i].max);
      q(`jp-tv${i}`).textContent = `${deg.toFixed(1)}°`;
      const w = q(`jp-w${i}`), act = (P.snap.robot.joints[i] || {}).near_limit;
      w.hidden = !(st.near || act);
      w.textContent = st.out ? 'sınır dışı' : (st.near || act) ? 'limite yakın' : '';
    });
  }

  async function onGo() {
    if (P.busy) return;
    P.busy = true; apply();
    const r = await JogPanel.goJoints(targets());
    say(r.text, !r.ok); P.busy = false;
    if (r.ok) P.dirty.clear();
    apply();
  }
  async function onHome() {
    const home = L.homeDeg(P.snap && P.snap.robot);
    if (!home || P.busy) return;
    P.busy = true; apply();
    const r = await JogPanel.goJoints(home);
    say(r.ok ? 'Home’a gidiliyor.' : r.text, !r.ok); P.busy = false;
    if (r.ok) P.dirty.clear();
    apply();
  }
  function say(text, bad) { const n = q('jp-gmsg'); if (n) { n.textContent = text || ''; n.classList.toggle('bad', !!bad); } }

  /* ---- 3B tıkla-git (P1b): "Hedef seç" kipinde zemin düzlemine dokun → hedef işareti → kuru kontrol → "Git". Dokunma yalnız bu kipte
   * seçer; kip kapalıyken seçim düzlemi sahnede YOK, bu yüzden telefonda sürükle/döndür karışmaz. Kimlikler "jog:". ---- */
  const zone = () => L.zoneOf(P.snap && P.snap.robot);
  const tmsg = (text, bad) => { const n = q('jp-tmsg'); n.textContent = text || ''; n.classList.toggle('bad', !!bad); };
  const sceneOk = (r) => { if (!r || r.ok === false) { tmsg(`3B sahne: ${(r && (r.message || r.error)) || 'yok'}`, true); return false; } return true; };
  function zValue() { const z = zone(); if (!z) return null; const v = q('jp-tz').value; return L.clampZ(z, v === '' ? NaN : Number(v) / 1000); }
  function putMarker() {
    const H = T.scene; if (!H) return;
    H.removeObject(L.ID_TARGET);
    if (T.pos) sceneOk(H.addObject(L.markerSpec(T.pos)));
  }
  function showTarget() {
    q('jp-tinfo').textContent = T.pos ? `Hedef: X ${(T.pos[0] * 1000).toFixed(1)} · Y ${(T.pos[1] * 1000).toFixed(1)} · Z ${(T.pos[2] * 1000).toFixed(1)} mm` : 'Hedef yok: “Hedef seç”e basıp 3B zemine dokun.';
    q('jp-tclear').hidden = !T.pos; applyTarget();
  }
  async function checkTarget() {
    const mine = ++T.seq; T.ok = false; applyTarget();
    if (!T.pos) return tmsg('');
    tmsg('Kontrol ediliyor…');
    let v;
    try { v = L.checkVerdict(await api('/api/move/check', 'POST', L.linearBody(T.pos, JogPanel.speed(), P.snap.robot.mode))); }
    catch (e) { v = { ok: false, text: L.explain(e) }; }
    if (mine !== T.seq) return;                                   // hedef arada değişti: eski cevap yok sayılır
    T.ok = v.ok; tmsg(v.text, !v.ok); applyTarget();              // geçmezse neden kırmızı yazılır, "Git" kapalı kalır, hareket isteği gitmez
  }
  function setTarget(pos) { T.pos = pos; putMarker(); showTarget(); return checkTarget(); }
  function onPick(hit) {
    if (!T.on || L.targetDisabledReason(P.snap)) return;
    const t = L.pickToTarget(hit, zone(), zValue());
    if (t) setTarget(t);
  }
  async function setPickMode(on) {
    if (on === T.on) return;
    if (on) {
      const H = T.scene || await HubPages.scene();
      if (!H || H.implemented === false) return tmsg('3B sahne açılamadı (WebGL yok): 3B hedef seçimi kapalı.', true);
      T.scene = H; H.removeObject(L.ID_PLANE);
      if (!sceneOk(H.addObject(L.planeSpec(zone(), zValue())))) return;
      T.unsub = T.unsub || H.onPick(onPick);
    } else if (T.scene) { T.scene.removeObject(L.ID_PLANE); if (T.unsub) { T.unsub(); T.unsub = null; } }
    T.on = on; applyTarget();
  }
  function onZ() {
    const v = zValue(); if (v === null) return;
    q('jp-tz').value = (v * 1000).toFixed(0);
    if (T.on && T.scene) T.scene.setPose(L.ID_PLANE, { pos_m: [(zone().min[0] + zone().max[0]) / 2, (zone().min[1] + zone().max[1]) / 2, v] });
    if (T.pos) setTarget([T.pos[0], T.pos[1], v]);                 // yükseklik değişti: işaret taşınır, kontrol yeniden
  }
  async function onTargetGo() {
    if (!T.ok || !T.pos || P.busy) return;
    P.busy = true; applyTarget();
    try { await api('/api/move', 'POST', L.linearBody(T.pos, JogPanel.speed(), P.snap.robot.mode)); tmsg('Hareket başladı.'); }
    catch (e) { tmsg(L.explain(e), true); }
    P.busy = false; T.ok = false; applyTarget();                  // her gidişten sonra yeni kontrol gerekir
  }
  function clearTarget() { T.seq++; T.pos = null; T.ok = false; if (T.scene) T.scene.removeObject(L.ID_TARGET); tmsg(''); showTarget(); }
  function applyTarget() {
    const why = L.targetDisabledReason(P.snap), pick = q('jp-pick');
    if (!pick) return;
    pick.disabled = !!why; pick.setAttribute('aria-pressed', String(T.on)); pick.classList.toggle('on', T.on);
    pick.textContent = T.on ? 'Hedef seçiliyor… (bitir)' : 'Hedef seç';
    q('jp-tz').disabled = !!why; q('jp-tclear').disabled = !!why;
    q('jp-tgo').disabled = !!why || !T.ok || P.busy;               // ACİL DUR'da hepsi kapalı (423 sunucuda ayrıca)
    q('jp-twhy').textContent = why || '';
    if (why && T.on) setPickMode(false);
    if (why) T.ok = false;
  }
  function buildTarget() {
    const pick = el('button', { class: 'small', id: 'jp-pick', type: 'button', text: 'Hedef seç', 'aria-pressed': 'false' });
    pick.addEventListener('click', () => setPickMode(!T.on));
    const z = el('input', { type: 'number', id: 'jp-tz', step: '1', inputMode: 'decimal', 'aria-label': 'Hedef yükseklik Z (mm)' });
    z.addEventListener('change', onZ);
    const go = el('button', { class: 'primary', id: 'jp-tgo', type: 'button', text: 'Git', disabled: true });
    go.addEventListener('click', onTargetGo);
    const clr = el('button', { class: 'small', id: 'jp-tclear', type: 'button', text: 'Hedefi sil', hidden: true });
    clr.addEventListener('click', clearTarget);
    return el('div', { class: 'card', id: 'p-target' }, [el('h2', { text: '3B’de tıkla-git' }),
      el('div', { class: 'row' }, [pick, el('label', { class: 'jp-f' }, [el('span', { text: 'Z (mm)' }), z]), go, clr]),
      el('div', { class: 'say', id: 'jp-tinfo', text: 'Hedef yok: “Hedef seç”e basıp 3B zemine dokun.' }),
      el('div', { class: 'say jp-msg', id: 'jp-tmsg', role: 'alert' }), el('div', { class: 'say', id: 'jp-twhy' })]);
  }
  function initTargetZ() {
    const z = zone(), f = q('jp-tz'); if (!z || !f || f.value !== '') return;
    f.value = (L.defaultZ(z) * 1000).toFixed(0); f.min = (z.min[2] * 1000).toFixed(0); f.max = (z.max[2] * 1000).toFixed(0);   // varsayılan = güvenli kutu ortası
  }

  function buildPanels() {
    const go = el('button', { class: 'primary', id: 'jp-go', type: 'button', text: 'Git (önce kontrol)' });
    go.addEventListener('click', onGo);
    const home = el('button', { class: 'small', id: 'jp-home', type: 'button', text: 'Home' });
    home.addEventListener('click', onHome);
    const sync = el('button', { class: 'small', id: 'jp-sync', type: 'button', text: 'Hedefi gerçeğe eşitle' });
    sync.addEventListener('click', () => { P.dirty.clear(); renderTargets(); say(''); });
    const grip = el('input', { type: 'range', id: 'jp-grip', min: '0', max: '100', step: '1', value: '0', 'aria-label': 'Alet açıklığı' });
    grip.addEventListener('input', () => { P.gripDirty = true; q('jp-grip-t').textContent = `%${grip.value}`; });
    grip.addEventListener('change', async () => { const r = await JogPanel.setGripper(Number(grip.value)); say(r.text, !r.ok); });
    const gbtn = (pct, text) => {
      const b = el('button', { class: 'small', type: 'button', text, id: `jp-grip-${pct}` });
      b.addEventListener('click', async () => { grip.value = String(pct); P.gripDirty = true; q('jp-grip-t').textContent = `%${pct}`;
        const r = await JogPanel.setGripper(pct); say(r.text, !r.ok); });
      return b;
    };
    const jogSlot = el('div', { id: 'jp-jogslot' });
    return [buildPose(), buildTarget(),
      el('div', { class: 'card', id: 'p-joints' }, [el('h2', { text: 'Eklemler' }), el('div', { id: 'jp-joints', class: 'jp-joints' }),
        el('div', { class: 'row' }, [go, home, sync]), el('div', { class: 'say', id: 'jp-gwhy' }),
        el('div', { class: 'say jp-msg', id: 'jp-gmsg', role: 'alert' })]),
      jogSlot,
      el('div', { class: 'card', id: 'p-tool' }, [el('h2', { text: 'Alet (gripper)' }),
        el('div', { class: 'jp-grip' }, [gbtn(100, 'Aç'), grip, gbtn(0, 'Kapat')]),
        el('div', { class: 'jp-vals' }, [el('span', { text: 'Hedef ' }), out('jp-grip-t', '%0'), el('span', { text: ' · Gerçek ' }), out('jp-grip-a')])])];
  }

  function apply() {
    const s = P.snap, why = L.disabledReason(s);
    for (const id of ['jp-go', 'jp-home', 'jp-grip', 'jp-grip-0', 'jp-grip-100']) { const n = q(id); if (n) n.disabled = !!why || P.busy; }
    const hb = q('jp-home'); if (hb && !hb.disabled && !L.homeDeg(s && s.robot)) hb.disabled = true;
    for (const n of document.querySelectorAll('.jp-slider')) n.disabled = !!why;
    const w = q('jp-gwhy'); if (w) w.textContent = why || '';
    applyTarget();
  }

  function onState(s) {
    P.snap = s;
    JogPanel.onState(s);
    const r = s.robot || {};
    if (!q('p-pose')) return;
    initTargetZ();
    const b = L.badge(r), mode = q('jp-mode');
    mode.textContent = b.text; mode.dataset.mode = r.mode || 'sim';
    q('jp-est').textContent = b.estimated ? 'tahmini (ölçülmedi)' : 'ölçüldü';
    q('jp-stale').hidden = !b.stale;
    const rows = L.poseRows(r);
    poseCells(q('jp-pose-j'), rows.joints.map((j) => j.name.replace('joint_', 'J')), rows.joints.map((j) => j.deg), '°', rows.joints.map((j) => j.near));
    if (rows.tcp) poseCells(q('jp-pose-t'), ['X mm', 'Y mm', 'Z mm', 'RX °', 'RY °', 'RZ °'], [...rows.tcp.mm, ...rows.tcp.deg], '', null);
    if (!P.built && L.jointsUsable(r.joints)) buildJoints(r.joints);
    else if (!P.built && (r.joints || []).length) q('jp-joints').textContent = 'Bu robot eklem limiti bildirmiyor: kaydırıcılar kapalı.';
    if (P.built) {
      (r.joints || []).forEach((j, i) => {
        const a = q(`jp-a${i}`); if (a) a.value = String(j.deg);
        q(`jp-av${i}`).textContent = `${j.deg.toFixed(1)}°`;
        if (!P.dirty.has(i)) q(`jp-t${i}`).value = String(j.deg);
      });
      renderTargets();
    }
    const g = L.gripperPct(r), ga = q('jp-grip-a');
    ga.textContent = g === null ? 'veri yok' : `%${Math.round(g)}`;
    if (g !== null && !P.gripDirty && !(r.moving)) { q('jp-grip').value = String(g); q('jp-grip-t').textContent = `%${Math.round(g)}`; }
    if (!r.moving) P.gripDirty = false;
    apply();
  }

  /* Telefonda 3B kartı sabit: yapışkan üst çubuğun altına oturması için yüksekliği ölçer (CSSOM, CSP'ye uygun). */
  function trackTopbar() {
    const bar = document.querySelector('.topbar'); if (!bar || !P.view) return;
    const set = () => P.view.style.setProperty(TOPBAR_VAR, `${Math.round(bar.getBoundingClientRect().height)}px`);
    set(); window.addEventListener('resize', set);
    if (window.ResizeObserver) new ResizeObserver(set).observe(bar);
  }

  HubPages.register({
    id: 'jog', title: 'Kontrol', icon: '🕹', order: 20, styles: ['/pages/jog.css'],
    scripts: ['/sphere.js', '/frames.js', '/jog_logic.js', '/jog.js'],
    mount(root) {
      P.view = el('div', { class: 'jog-view' });
      const side = el('div', { class: 'jog-side' }, buildPanels());
      root.append(el('div', { class: 'page-grid two jog-grid' }, [P.view, side]));
      JogPanel.mount(q('jp-jogslot'));
      HubPages.mountView(P.view);
      trackTopbar();
    },
    onShow() { HubPages.mountView(P.view); },
    onHide() { if (window.JogPanel) { JogPanel.stopHold(); JogPanel.release(false); } setPickMode(false); clearTarget(); },
    onState,
  });
})();
