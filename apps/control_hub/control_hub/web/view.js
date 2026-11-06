/* Robot görünümü (L9c): bağımlılıksız Canvas 2D, ortografik, sürükleyerek döner, SALT OKUNUR.
 * Veri: GET /api/model (robot modeli, bir kez) + Hub durumundaki joints (rad). Eklem açılarından ileri kinematik
 * burada hesaplanır; link = çubuk/kapsül. Bu görünüm bir ÖLÇÜM ya da güvenlik işlevi DEĞİLDİR.
 * BAYATLIK: durum 1 s'den eskiyse (WS sessiz, robot bağlantısı yok ya da robot verisi eski) görünüm griye döner
 * ve kırmızı uyarı bandı çıkar; donmuş model yanıltır. Tazelik yalnız WS mesajlarıyla (durum ya da "tick") sayılır:
 * WS yokken HTTP yoklaması görünümü "taze" yapmaz. Komut yolu YOK. Veri textContent ile yazılır. */
'use strict';

window.RobotView = (() => {
  const STALE_MS = 1000;
  const V = { mounted: false, model: null, why: null, snap: null, live: 0, stateAt: 0, q: {}, target: {},
              yaw: -0.65, pitch: 0.42, zoom: 1, frames: null, framesFor: null, framesAt: 0, drag: null,
              raf: 0, last: 0, tries: 0, retry: null };
  const q$ = (id) => document.getElementById(id);

  /* ---- küçük 3B matematik: dönüşüm = {R: 9 (satır-sıra), t: 3} ---- */
  const I3 = () => ({ R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0] });
  const mulR = (a, b) => { const r = new Array(9);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
    return r; };
  const rotV = (R, v) => [R[0] * v[0] + R[1] * v[1] + R[2] * v[2], R[3] * v[0] + R[4] * v[1] + R[5] * v[2], R[6] * v[0] + R[7] * v[1] + R[8] * v[2]];
  const compose = (A, B) => { const t = rotV(A.R, B.t); return { R: mulR(A.R, B.R), t: [t[0] + A.t[0], t[1] + A.t[1], t[2] + A.t[2]] }; };
  const trans = (v) => ({ R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: v.slice() });
  function axisAngle(ax, a) {
    const n = Math.hypot(ax[0], ax[1], ax[2]) || 1, x = ax[0] / n, y = ax[1] / n, z = ax[2] / n, c = Math.cos(a), s = Math.sin(a), k = 1 - c;
    return { R: [c + x * x * k, x * y * k - z * s, x * z * k + y * s, y * x * k + z * s, c + y * y * k, y * z * k - x * s,
      z * x * k - y * s, z * y * k + x * s, c + z * z * k], t: [0, 0, 0] };
  }
  const rotZ = (a) => axisAngle([0, 0, 1], a), rotY = (a) => axisAngle([0, 1, 0], a), rotX = (a) => axisAngle([1, 0, 0], a);
  const wprRot = (w) => compose(compose(rotZ(w[2]), rotY(w[1])), rotX(w[0]));     // R = Rz(R)·Ry(P)·Rx(W)
  const quatRot = (qq) => { const [x, y, z, w] = qq, n = Math.hypot(x, y, z, w) || 1, a = x / n, b = y / n, c = z / n, d = w / n;
    return { R: [1 - 2 * (b * b + c * c), 2 * (a * b - c * d), 2 * (a * c + b * d), 2 * (a * b + c * d), 1 - 2 * (a * a + c * c),
      2 * (b * c - a * d), 2 * (a * c - b * d), 2 * (b * c + a * d), 1 - 2 * (a * a + b * b)], t: [0, 0, 0] }; };
  const apply = (T, p) => { const r = rotV(T.R, p); return [r[0] + T.t[0], r[1] + T.t[1], r[2] + T.t[2]]; };

  /* ---- model ---- */
  function validModel(m) {
    return m && Array.isArray(m.joints) && Array.isArray(m.links) && m.joints.length > 0 && m.safe_box && m.tcp;
  }
  async function loadModel() {
    clearTimeout(V.retry); V.retry = null;
    try {
      V.model = null;
      const m = await api('/api/model');
      if (!validModel(m)) throw Object.assign(new Error('model eksik/beklenmedik biçimde'), { status: 0 });
      if (m.view_model_version !== 1) throw Object.assign(new Error(`desteklenmeyen model sürümü: ${m.view_model_version}`), { status: 0 });
      V.model = m; V.why = null; V.q = {}; V.target = {};
    } catch (e) {
      const body = e.body || {};
      V.why = (e.status === 501 ? 'bu robot arka ucu model vermiyor' : (body.error || body.message || e.message || 'bilinmeyen hata')) +
        (e.status ? ` (HTTP ${e.status})` : '');
      V.tries += 1; V.retry = setTimeout(loadModel, Math.min(30000, 3000 * V.tries));   // sessiz kalma: kart nedeni gösterir
    }
    render();
  }
  /* Modelle durumdaki eklem adlarının eşleşmesi: eşleşmezse YANLIŞ eklemi çizerdi, çizmeyiz. */
  function jointProblem() {
    const js = V.snap && V.snap.robot && V.snap.robot.joints;
    if (!V.model) return V.why || 'model yükleniyor';
    if (!js || !js.length) return 'robottan eklem durumu gelmiyor';
    const have = new Map(js.map((j) => [j.name, j.rad]));
    const miss = V.model.joints.map((j) => j.name).filter((n) => !have.has(n) || typeof have.get(n) !== 'number');
    return miss.length ? `model eklemleri durumda yok: ${miss.join(', ')}` : null;
  }

  /* ---- tazelik ---- */
  function age() {
    const now = performance.now(), r = (V.snap && V.snap.robot) || {};
    let worst = now - V.live;                                            // WS'ten son mesaj
    if (typeof r.stale_s === 'number') worst = Math.max(worst, r.stale_s * 1000 + (now - V.stateAt));   // robot verisi yaşı
    return worst;
  }
  function staleReason() {
    const r = (V.snap && V.snap.robot) || {};
    if (!V.live || performance.now() - V.live > STALE_MS) return 'Hub’la canlı bağlantı yok ya da 1 s’den uzun süredir sessiz';
    if (r.connected === false) return `robot bağlı değil${r.problem ? ': ' + r.problem : ''}`;
    if (typeof r.stale_s === 'number' && age() > STALE_MS) return 'robot verisi 1 s’den eski';
    return null;
  }

  /* ---- kinematik ---- */
  function forward(m, qmap) {
    const fr = { [m.root]: I3() };
    for (const j of m.joints) {
      const P = fr[j.parent] || I3();
      fr[j.child] = compose(compose(P, trans(j.origin)), axisAngle(j.axis, qmap[j.name] || 0));
    }
    return fr;
  }
  function shapesOf(m, fr) {
    const out = [];
    const push = (T, sh, origin, wpr, color) => {
      let B = compose(T, trans(origin || [0, 0, 0]));
      if (wpr) B = compose(B, wprRot(wpr));
      if (sh.type === 'sphere') { out.push({ k: 'dot', a: B.t, w: sh.radius * 2, color }); return; }
      const len = sh.type === 'cylinder' ? sh.length : sh.size[2];
      const w = sh.type === 'cylinder' ? sh.radius * 2 : (sh.size[0] + sh.size[1]) / 2;
      const z = rotV(B.R, [0, 0, len / 2]);
      out.push({ k: sh.type === 'box' ? 'box' : 'cyl', a: [B.t[0] - z[0], B.t[1] - z[1], B.t[2] - z[2]],
        b: [B.t[0] + z[0], B.t[1] + z[1], B.t[2] + z[2]], w, len, color });
    };
    for (const l of m.links) { const T = fr[l.name]; if (T && l.shape) push(T, l.shape, l.origin, null, l.color); }
    if (m.tool && m.tool.shapes) {
      const base = fr[m.tcp.frame] || I3();
      for (const s of m.tool.shapes) push(base, s, s.origin, s.wpr, m.tool.color || [0.7, 0.7, 0.7, 1]);
    }
    return out;
  }
  function tcpPose(m, fr) {
    const F = fr[m.tcp.frame] || I3();
    return compose(compose(F, trans(m.tcp.offset || [0, 0, 0])), wprRot(m.tcp.wpr || [0, 0, 0]));
  }

  /* ---- projeksiyon ---- */
  function proj(p, cam) {
    const cy = Math.cos(V.yaw), sy = Math.sin(V.yaw), cp = Math.cos(V.pitch), sp = Math.sin(V.pitch);
    const px = p[0] - 0.3, py = p[1], pz = p[2] - 0.45;                  // dönme merkezi: çalışma alanının ortası
    const xr = px * cy - py * sy, yr = px * sy + py * cy;
    return { x: cam.cx + xr * cam.s, y: cam.cy - (pz * cp - yr * sp) * cam.s, d: yr * cp + pz * sp };
  }
  const css = (c, a = 1, gray = false) => {
    if (gray) { const g = Math.round(((c[0] + c[1] + c[2]) / 3) * 120 + 40); return `rgba(${g},${g},${g},${a})`; }
    return `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;
  };

  /* ---- çerçeveler (aktif jog çerçevesinin eksenleri) ---- */
  function activeFrameName() { return (window.JogPanel && JogPanel.frame) ? JogPanel.frame() : 'World'; }
  async function refreshFrames() {
    V.framesAt = performance.now(); V.framesFor = activeFrameName();
    try { V.frames = await api('/api/frames'); } catch (e) { V.frames = null; }
  }
  function frameAxes(fname, tcpT) {
    if (fname === 'World') return { T: I3(), label: 'World', len: 0.12 };
    if (fname === 'Tool') return tcpT ? { T: tcpT, label: 'Tool', len: 0.06 } : null;
    const f = V.frames && (V.frames.frames || []).find((x) => x.name === fname);
    if (f && f.pose) {
      const T = f.pose.quaternion_xyzw ? quatRot(f.pose.quaternion_xyzw) : I3();
      T.t = f.pose.position.slice();
      return { T, label: `User: ${fname}`, len: 0.1 };
    }
    const s = V.frames && (V.frames.spheres || []).find((x) => x.name === fname);
    if (s) return { T: trans(s.center), label: `Sphere: ${fname}`, len: 0.08, sphere: s };
    return null;
  }

  /* ---- çizim ---- */
  function draw(now) {
    const cv = q$('rv-canvas'); if (!cv) return;
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const stale = staleReason(), gray = !!stale;
    const m = V.model;
    const cam = { cx: w / 2, cy: h / 2, s: Math.min(w, h * 1.25) / 1.95 * V.zoom };
    // yumuşatma: hedef eklemlere üstel yaklaşım (5 Hz akıcı değil); bayatken DONDURULUR
    const dt = Math.min(0.1, (now - (V.last || now)) / 1000); V.last = now;
    if (!gray) for (const n of Object.keys(V.target)) {
      const cur = V.q[n] === undefined ? V.target[n] : V.q[n];
      V.q[n] = cur + (V.target[n] - cur) * (1 - Math.exp(-dt / 0.1));
    }
    const P = (p) => proj(p, cam);
    const line = (a, b, col, wd, dash) => { const A = P(a), B = P(b); g.beginPath(); g.moveTo(A.x, A.y); g.lineTo(B.x, B.y);
      g.strokeStyle = col; g.lineWidth = wd; g.setLineDash(dash || []); g.stroke(); g.setLineDash([]); };
    // yer ızgarası (z = 0)
    for (let i = -4; i <= 8; i++) { const v = i * 0.1; line([v, -0.5, 0], [v, 0.5, 0], 'rgba(120,130,150,.15)', 1);
      if (i >= -5 && i <= 5) line([-0.4, i * 0.1, 0], [0.8, i * 0.1, 0], 'rgba(120,130,150,.15)', 1); }
    if (!m) return;
    const sb = m.safe_box, X = sb.x, Y = sb.y, Z = sb.z, boxCol = gray ? 'rgba(150,150,150,.6)' : 'rgba(210,153,34,.85)';
    const corners = [];
    for (const x of X) for (const y of Y) for (const z of Z) corners.push([x, y, z]);
    for (const [i, j] of [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]]) line(corners[i], corners[j], boxCol, 1.2, [5, 4]);
    const problem = jointProblem();
    if (problem) return;
    const fr = forward(m, V.q), tcpT = tcpPose(m, fr);
    // aktif çerçeve eksenleri
    const fa = frameAxes(activeFrameName(), tcpT);
    if (fa) {
      const cols = ['#f85149', '#3fb950', '#58a6ff'];
      [[1, 0, 0], [0, 1, 0], [0, 0, 1]].forEach((u, i) => line(fa.T.t, apply(fa.T, u.map((v) => v * fa.len)), gray ? '#888' : cols[i], 2.5));
      if (fa.sphere) { const c = fa.sphere.center, r = fa.sphere.radius;
        for (let k = 0; k < 48; k++) { const a0 = k / 48 * 2 * Math.PI, a1 = (k + 1) / 48 * 2 * Math.PI;
          line([c[0] + r * Math.cos(a0), c[1] + r * Math.sin(a0), c[2]], [c[0] + r * Math.cos(a1), c[1] + r * Math.sin(a1), c[2]], 'rgba(88,166,255,.5)', 1); } }
      const L = P(fa.T.t); g.fillStyle = gray ? '#999' : '#c9d1d9'; g.font = '11px system-ui'; g.fillText(fa.label, L.x + 6, L.y - 6);
    }
    // linkler: derinlik sırasıyla (uzaktan yakına)
    const items = shapesOf(m, fr).map((s) => ({ s, d: (P(s.a).d + (s.b ? P(s.b).d : P(s.a).d)) / 2 })).sort((a, b) => b.d - a.d);
    for (const { s } of items) {
      const A = P(s.a), col = css(s.color || [0.7, 0.7, 0.7, 1], 1, gray);
      if (s.k === 'dot') { g.beginPath(); g.arc(A.x, A.y, Math.max(2, s.w * cam.s / 2), 0, 7); g.fillStyle = col; g.fill(); continue; }
      const B = P(s.b);
      g.beginPath(); g.moveTo(A.x, A.y); g.lineTo(B.x, B.y); g.lineCap = (s.k === 'cyl' && s.len > s.w) ? 'round' : 'butt';       // kısa-kalın silindir (taban) kapsül olmasın
      g.strokeStyle = col; g.lineWidth = Math.max(2, s.w * cam.s); g.stroke();
      g.strokeStyle = 'rgba(0,0,0,.35)'; g.lineWidth = 1; g.stroke();
      g.lineCap = 'butt';
    }
    // TCP: çizilen zincirin ucu (model) + robotun bildirdiği TCP (halka)
    const T = P(tcpT.t); g.fillStyle = gray ? '#999' : '#fff'; g.beginPath(); g.arc(T.x, T.y, 4, 0, 7); g.fill();
    const rep = V.snap && V.snap.robot && V.snap.robot.tcp && V.snap.robot.tcp.position;
    if (rep) { const R = P(rep); g.beginPath(); g.arc(R.x, R.y, 7, 0, 7); g.strokeStyle = gray ? '#999' : '#3fb950'; g.lineWidth = 1.5; g.stroke(); }
  }
  function loop(now) {
    V.raf = 0;
    if (!V.mounted || document.hidden) return;
    if (now - V.last >= 33) { draw(now); updateBanner(); }
    V.raf = requestAnimationFrame(loop);
  }
  function kick() { if (!V.raf && V.mounted) V.raf = requestAnimationFrame(loop); }

  /* ---- arayüz ---- */
  function modeNote(mode) {
    if (mode === 'real') return ['KESTİRİM', 'Gerçek robot: açık çevrim (encoder’sız/servo) eksenlerde konum komut sayacından kestirimdir; çizim ölçüm değildir. API eksen bazında ayırt etmediği için tüm çizim kestirim sayılır.'];
    if (mode === 'fake') return ['SAHTE ROBOT', 'Sahte robot: çizilen duruş gerçek kinematik değil, yalnızca arayüz denemesi.'];
    return [`SİMÜLASYON${mode ? ' (' + mode + ')' : ''}`, 'Simülasyon durumu; gerçek robotu göstermez.'];
  }
  function updateBanner() {
    const why = staleReason(), band = q$('rv-band');
    if (band) { band.hidden = !why; if (why) band.textContent = `ACİL DUR: veri bayat, görünüm donmuş olabilir (${why}). Robot hareket ediyorsa ACİL DUR düğmesini kullan.`; }
    const cv = q$('rv-canvas'); if (cv) cv.classList.toggle('stale', !!why);
  }
  function render() {
    if (!V.mounted) return;
    const reason = V.model ? jointProblem() : (V.why || 'model yükleniyor…');    // null = çizilebilir
    q$('rv-fail').hidden = !reason;
    q$('rv-failtxt').textContent = `Görünüm kullanılamıyor: ${reason || ''}`;
    q$('rv-body').hidden = !!reason;
    const [tag, txt] = modeNote(V.snap && V.snap.robot && V.snap.robot.mode);
    q$('rv-tag').textContent = tag; q$('rv-tag').title = txt; q$('rv-note').textContent = txt;
    q$('rv-tag').classList.toggle('est', tag === 'KESTİRİM');
    updateBanner(); kick();
  }
  function setView(yaw, pitch) { V.yaw = yaw; V.pitch = pitch; kick(); }

  function mount() {
    if (V.mounted) return;
    V.mounted = true;
    const cv = el('canvas', { id: 'rv-canvas', class: 'rv-canvas', role: 'img',
      'aria-label': 'Robotun ortografik görünümü (salt okunur)', tabindex: '0' });
    const presets = [['Ön', 0, 0.12], ['Yan', -Math.PI / 2, 0.12], ['Üst', 0, 1.5], ['Eğik', -0.65, 0.42]].map(([n, y, p]) => {
      const b = el('button', { class: 'small', type: 'button', text: n }); b.addEventListener('click', () => setView(y, p)); return b; });
    q$('panels').append(el('div', { class: 'card', id: 'p-view' }, [
      el('h2', {}, [document.createTextNode('Robot görünümü '), el('span', { class: 'rv-tag', id: 'rv-tag', text: '' })]),
      el('div', { class: 'say jg-msg bad', id: 'rv-fail', role: 'alert', hidden: true }, [
        el('span', { id: 'rv-failtxt' }), document.createTextNode(' '),
        (() => { const b = el('button', { class: 'small', type: 'button', text: 'Yeniden dene' }); b.addEventListener('click', () => { V.tries = 0; loadModel(); }); return b; })()]),
      el('div', { class: 'rv-band', id: 'rv-band', role: 'alert', hidden: true }),
      el('div', { id: 'rv-body' }, [cv, el('div', { class: 'row' }, presets),
        el('div', { class: 'say', id: 'rv-note' }),
        el('div', { class: 'say', text: 'Sürükle: döndür · tekerlek/iki parmak: yakınlaştır · turuncu kesikli kutu: robotun etkin güvenli kutusu (model verisi) · beyaz nokta: çizilen zincirin ucu · yeşil halka: robotun bildirdiği TCP. Salt-okunur görünüm; ölçüm değildir.' })])]));
    cv.addEventListener('pointerdown', (ev) => { cv.setPointerCapture(ev.pointerId); V.drag = { x: ev.clientX, y: ev.clientY, yaw: V.yaw, pitch: V.pitch }; });
    cv.addEventListener('pointermove', (ev) => { if (!V.drag) return;
      V.yaw = V.drag.yaw + (ev.clientX - V.drag.x) * 0.01;
      V.pitch = Math.max(-0.2, Math.min(1.5, V.drag.pitch + (ev.clientY - V.drag.y) * 0.01)); });
    for (const t of ['pointerup', 'pointercancel']) cv.addEventListener(t, () => { V.drag = null; });
    cv.addEventListener('wheel', (ev) => { ev.preventDefault(); V.zoom = Math.max(0.5, Math.min(3, V.zoom * (ev.deltaY < 0 ? 1.1 : 0.9))); }, { passive: false });
    cv.style.touchAction = 'none';
    document.addEventListener('visibilitychange', kick);
    setInterval(() => { if (V.mounted && performance.now() - V.framesAt > 1000 && (V.framesFor !== activeFrameName() || performance.now() - V.framesAt > 10000)) refreshFrames(); }, 500);
    loadModel(); refreshFrames(); render();
  }

  /* app.js çağırır: durum her değiştiğinde ve WS'ten HER mesajda (durum/tick) */
  function onState(s) {
    V.snap = s; V.stateAt = performance.now();
    const js = (s.robot && s.robot.joints) || [];
    for (const j of js) if (typeof j.rad === 'number') V.target[j.name] = j.rad;
    for (const n of Object.keys(V.target)) if (V.q[n] === undefined) V.q[n] = V.target[n];   // ilk durumda yumuşatma yok
    render();
  }
  function onLive() { V.live = performance.now(); }
  return { mount, onState, onLive, staleReason, _V: V };
})();
