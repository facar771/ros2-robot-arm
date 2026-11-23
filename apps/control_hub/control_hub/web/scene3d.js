/* Robot 3B görünümü (Three.js, YEREL kopya: /vendor/three/). ES modülü; yalnız ilk 3B sayfasında yüklenir
 * (import('/scene3d.js')). Çıktı: window.Scene3D.
 *
 * Veri: GET /api/model (view_model.json v1: eklem zinciri, link şekilleri, güvenli kutu) + GET /api/meshes
 * (profildeki link STL'leri, varsa) + Hub durumundaki eklem açıları (WS). Mesh yoksa model primitifleri
 * (silindir/kutu) çizilir. SALT OKUNUR: komut yolu yok. Bu görünüm bir ÖLÇÜM ya da güvenlik işlevi DEĞİLDİR.
 * BAYATLIK: durum 1 s'den eskiyse görünüm griye döner, hareket DONAR ve kırmızı bant çıkar (scene_logic.js).
 * Veri textContent ile yazılır (innerHTML yok). Dünya çerçevesi Z yukarı (kamera.up = +Z). */
import * as THREE from '/vendor/three/three.module.js';

const SL = window.SceneLogic;
const V = { root: null, wrap: null, canvas: null, msg: null, band: null, tag: null, note: null,
            renderer: null, scene: null, camera: null, robot: null, nodes: new Map(), model: null, why: null,
            modelKey: '', snap: null, stateAt: 0, q: {}, target: {}, dirty: true, raf: 0, last: 0, tries: 0,
            retry: null, yaw: -0.65, pitch: 0.45, dist: 1.5, center: new THREE.Vector3(0.3, 0, 0.3),
            drag: null, attached: null, frames: null, framesAt: 0, framesFor: null, overlay: null,
            reported: null, glFailed: null, meshInfo: null, ro: null };

/* ---------- sahne ---------- */
function build() {
  V.wrap = document.createElement('div'); V.wrap.className = 'v3d';
  V.msg = document.createElement('div'); V.msg.className = 'v3d-msg'; V.msg.textContent = '3B görünüm yükleniyor…';
  V.root = document.createElement('div');
  V.band = document.createElement('div'); V.band.className = 'v3d-band'; V.band.hidden = true; V.band.setAttribute('role', 'alert');
  V.tag = document.createElement('span'); V.tag.className = 'rv-tag';
  V.note = document.createElement('div'); V.note.className = 'say';
  const head = document.createElement('h2'); head.append(document.createTextNode('Robot 3B '), V.tag);
  V.root.className = 'card'; V.root.id = 'p-view3d';
  V.root.append(head, V.band, V.wrap, V.note);
  try {
    const r = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.setClearColor(0x0a0e14, 1);
    V.renderer = r; V.canvas = r.domElement;
    V.canvas.setAttribute('role', 'img'); V.canvas.setAttribute('aria-label', 'Robotun 3B görünümü (salt okunur)');
    V.canvas.tabIndex = 0;
    V.wrap.append(V.canvas, V.msg);
  } catch (e) {
    V.glFailed = String(e && e.message || e);
    V.msg.textContent = `3B görünüm kullanılamıyor: WebGL açılamadı (${V.glFailed}). Diğer işlevler çalışır.`;
    V.wrap.append(V.msg);
    return;
  }
  V.scene = new THREE.Scene();
  V.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 30);
  V.camera.up.set(0, 0, 1);
  V.scene.add(new THREE.HemisphereLight(0xdde6ff, 0x30343c, 1.5));
  const sun = new THREE.DirectionalLight(0xffffff, 1.7); sun.position.set(1.2, -0.8, 2); V.scene.add(sun);
  const grid = new THREE.GridHelper(1.6, 16, 0x3b4452, 0x232a34); grid.rotation.x = Math.PI / 2; grid.position.set(0.3, 0, 0);
  V.scene.add(grid, new THREE.AxesHelper(0.15));
  V.robot = new THREE.Group(); V.scene.add(V.robot);
  const el = V.canvas;
  el.addEventListener('pointerdown', (ev) => { el.setPointerCapture(ev.pointerId); V.userMoved = true; V.drag = { x: ev.clientX, y: ev.clientY, yaw: V.yaw, pitch: V.pitch }; });
  el.addEventListener('pointermove', (ev) => {
    if (!V.drag) return;
    V.yaw = V.drag.yaw - (ev.clientX - V.drag.x) * 0.01;
    V.pitch = Math.max(-0.1, Math.min(1.5, V.drag.pitch + (ev.clientY - V.drag.y) * 0.01)); V.dirty = true; });
  for (const t of ['pointerup', 'pointercancel']) el.addEventListener(t, () => { V.drag = null; });
  el.addEventListener('wheel', (ev) => { ev.preventDefault(); V.userMoved = true; V.dist = Math.max(0.4, Math.min(5, V.dist * (ev.deltaY < 0 ? 0.9 : 1.1))); V.dirty = true; }, { passive: false });
  el.addEventListener('webglcontextlost', (ev) => { ev.preventDefault(); });          // telefonda sekme değişince olur
  el.addEventListener('webglcontextrestored', () => { V.dirty = true; kick(); });
  V.ro = new ResizeObserver(() => { V.dirty = true; }); V.ro.observe(V.wrap);
  document.addEventListener('visibilitychange', kick);
  setInterval(pollFrames, 500);
}

function resize() {
  const w = V.wrap.clientWidth, h = V.wrap.clientHeight;
  if (!w || !h) return false;
  const dpr = V.renderer.getPixelRatio();
  if (V.canvas.width !== Math.round(w * dpr) || V.canvas.height !== Math.round(h * dpr)) {
    V.renderer.setSize(w, h, false); V.camera.aspect = w / h; V.camera.updateProjectionMatrix();
  }
  return true;
}

/* ---------- model ---------- */
const mat = (c) => new THREE.MeshStandardMaterial({ color: new THREE.Color().setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace),
  roughness: 0.55, metalness: 0.15 });

function primitive(link) {
  const sh = link.shape; if (!sh) return null;
  let g;
  if (sh.type === 'cylinder') { g = new THREE.CylinderGeometry(sh.radius, sh.radius, sh.length, 28); g.rotateX(Math.PI / 2); }
  else if (sh.type === 'box') g = new THREE.BoxGeometry(sh.size[0], sh.size[1], sh.size[2]);
  else if (sh.type === 'sphere') g = new THREE.SphereGeometry(sh.radius, 20, 14);
  else return null;
  const m = new THREE.Mesh(g, mat(link.color || [0.7, 0.7, 0.7]));
  const o = link.origin || [0, 0, 0]; m.position.set(o[0], o[1], o[2]);
  return m;
}

function toGeometry(parsed) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(parsed.positions, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(parsed.normals, 3));
  return g;
}

async function loadMeshes(model, key) {
  let info = null;
  try { info = await api('/api/meshes'); } catch (e) { info = null; }
  if (key !== V.modelKey) return;
  V.meshInfo = info; let used = 0, failed = [];
  for (const link of model.links) {
    const m = info && info.meshes && info.meshes[link.name]; const node = V.nodes.get(link.name);
    if (!m || !node) continue;
    try {
      const res = await fetch(m.url, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const mesh = new THREE.Mesh(toGeometry(SL.parseStl(await res.arrayBuffer())), mat(link.color || [0.7, 0.7, 0.7]));
      if (key !== V.modelKey) return;
      if (node.userData.visual) node.remove(node.userData.visual);       // primitifin yerine geçer
      node.add(mesh); node.userData.visual = mesh; used += 1; V.dirty = true; frameCamera();
    } catch (e) { failed.push(`${link.name}: ${e.message}`); }
  }
  V.meshUsed = used; V.meshFailed = failed; updateNote();
}

function buildRobot(model) {
  V.robot.clear(); V.nodes.clear(); V.safeBox = null;
  const root = new THREE.Group(); root.name = model.root; V.robot.add(root); V.nodes.set(model.root, root);
  const byName = new Map(model.links.map((l) => [l.name, l]));
  const attach = (name) => {
    const node = V.nodes.get(name), link = byName.get(name);
    if (node && link && !node.userData.visual) { const p = primitive(link); if (p) { node.add(p); node.userData.visual = p; } }
  };
  attach(model.root);
  for (const j of SL.orderJoints(model)) {
    const g = new THREE.Group(); g.name = j.child; g.position.set(j.origin[0], j.origin[1], j.origin[2]);
    g.userData.axis = new THREE.Vector3(j.axis[0], j.axis[1], j.axis[2]).normalize(); g.userData.joint = j.name;
    V.nodes.get(j.parent).add(g); V.nodes.set(j.child, g); attach(j.child);
  }
  const sb = model.safe_box;
  if (sb) {
    const size = new THREE.Vector3(sb.x[1] - sb.x[0], sb.y[1] - sb.y[0], sb.z[1] - sb.z[0]);
    const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z)),
      new THREE.LineBasicMaterial({ color: 0xd29922 }));
    box.position.set((sb.x[0] + sb.x[1]) / 2, (sb.y[0] + sb.y[1]) / 2, (sb.z[0] + sb.z[1]) / 2);
    V.robot.add(box); V.safeBox = box;
  }
  const tcpNode = V.nodes.get(model.tcp.frame) || root;
  const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 14, 10), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  const off = model.tcp.offset || [0, 0, 0]; dot.position.set(off[0], off[1], off[2]); tcpNode.add(dot);
  V.reported = new THREE.Mesh(new THREE.TorusGeometry(0.02, 0.003, 8, 24), new THREE.MeshBasicMaterial({ color: 0x3fb950 }));
  V.reported.visible = false; V.robot.add(V.reported);
  V.dirty = true;
  frameCamera();
}

/* Kamerayı sıfır duruşun (+ güvenli kutunun) sınır küresine göre ayarla; kullanıcı kamerayı oynattıysa dokunma. */
function frameCamera(force) {
  if (V.userMoved && !force) return;
  V.robot.updateMatrixWorld(true);
  const root = V.model && V.nodes.get(V.model.root);
  if (!root) return;
  const box = new THREE.Box3().setFromObject(root);              // robot + güvenli kutu (bildirilen-TCP halkası dışarıda)
  if (V.safeBox) box.expandByObject(V.safeBox);
  if (box.isEmpty()) return;
  const sph = box.getBoundingSphere(new THREE.Sphere());
  V.center.copy(sph.center);
  V.dist = Math.max(0.5, (sph.radius / Math.sin(THREE.MathUtils.degToRad(V.camera.fov / 2))) * 1.05);
  V.camera.far = V.dist * 20; V.camera.updateProjectionMatrix(); V.dirty = true;
}

async function loadModel() {
  clearTimeout(V.retry); V.retry = null;
  try {
    const m = await api('/api/model');
    if (!m || !Array.isArray(m.joints) || !Array.isArray(m.links) || !m.joints.length || !m.safe_box || !m.tcp) {
      throw Object.assign(new Error('model eksik/beklenmedik biçimde'), { status: 0 });
    }
    if (m.view_model_version !== 1) throw Object.assign(new Error(`desteklenmeyen model sürümü: ${m.view_model_version}`), { status: 0 });
    const key = JSON.stringify([m.robot, m.joints, m.links, m.tcp, m.safe_box]);
    if (key !== V.modelKey) {
      V.modelKey = key; V.model = m; V.why = null; V.q = {}; V.target = {}; V.meshUsed = 0; V.meshFailed = []; V.overlay = null;
      buildRobot(m); applySnapTargets(true); loadMeshes(m, key);
    }
    V.tries = 0;
  } catch (e) {
    const body = e.body || {};
    V.why = (e.status === 501 ? 'bu robot arka ucu model vermiyor' : (body.error || body.message || e.message || 'bilinmeyen hata')) +
      (e.status ? ` (HTTP ${e.status})` : '');
    V.model = null; V.modelKey = '';
    if (V.robot) V.robot.clear();
    V.tries += 1; V.retry = setTimeout(loadModel, Math.min(30000, 3000 * V.tries));      // sessiz kalma: kutu nedeni gösterir
  }
  refreshUi();
}

function applySnapTargets(first) {
  const js = (V.snap && V.snap.robot && V.snap.robot.joints) || [];
  for (const j of js) if (typeof j.rad === 'number') V.target[j.name] = j.rad;
  if (first) for (const n of Object.keys(V.target)) V.q[n] = V.target[n];
  for (const n of Object.keys(V.target)) if (V.q[n] === undefined) V.q[n] = V.target[n];   // ilk durumda yumuşatma yok
}

/* ---------- aktif jog çerçevesi (jog sayfası) ---------- */
function activeFrameName() { return (window.JogPanel && JogPanel.frame) ? JogPanel.frame() : 'World'; }
async function pollFrames() {
  if (!V.attached || !V.model) return;
  const now = performance.now(), want = activeFrameName();
  if (V.framesFor === want && now - V.framesAt < 10000) return;
  V.framesAt = now; V.framesFor = want;
  try { V.frames = await api('/api/frames'); } catch (e) { V.frames = null; }
  rebuildOverlay();
}
function rebuildOverlay() {
  if (V.overlay) { V.overlay.removeFromParent(); V.overlay = null; }
  if (!V.model) return;
  const name = V.framesFor || 'World', g = new THREE.Group(), list = V.frames || {};
  const f = (list.frames || []).find((x) => x.name === name), s = (list.spheres || []).find((x) => x.name === name);
  let parent = V.robot;
  if (name === 'Tool') {                                  // takım çerçevesi TCP düğümünü izler
    parent = V.nodes.get(V.model.tcp.frame) || V.robot;
    const o = V.model.tcp.offset || [0, 0, 0]; g.position.set(o[0], o[1], o[2]); g.add(new THREE.AxesHelper(0.06));
  } else if (f && f.pose) {
    g.position.set(...f.pose.position);
    if (f.pose.quaternion_xyzw) g.quaternion.set(...f.pose.quaternion_xyzw);
    g.add(new THREE.AxesHelper(0.1));
  } else if (s) {
    const pts = [];
    for (let k = 0; k <= 48; k++) { const t = k / 48 * 2 * Math.PI; pts.push(new THREE.Vector3(s.center[0] + s.radius * Math.cos(t), s.center[1] + s.radius * Math.sin(t), s.center[2])); }
    g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x58a6ff })));
    const a = new THREE.AxesHelper(0.08); a.position.set(...s.center); g.add(a);
  }
  parent.add(g); V.overlay = g; V.dirty = true;
}

/* ---------- çizim ---------- */
function frame(now) {
  V.raf = 0;
  if (!V.attached || document.hidden || !V.renderer) return;
  const dt = Math.min(0.1, (now - (V.last || now)) / 1000); V.last = now;
  const stale = SL.staleReason(now, S.liveAt, V.stateAt, V.snap);
  if (!stale && V.model) {
    for (const n of Object.keys(V.target)) {
      const cur = V.q[n] === undefined ? V.target[n] : V.q[n], nxt = SL.approach(cur, V.target[n], dt);
      if (Math.abs(nxt - cur) > 1e-5) V.dirty = true; V.q[n] = Math.abs(V.target[n] - nxt) < 1e-5 ? V.target[n] : nxt;
    }
  }
  updateBand(stale);
  if (V.dirty && V.model && resize()) {
    V.dirty = false;
    for (const node of V.nodes.values()) {
      const j = node.userData.joint;
      if (j) node.quaternion.setFromAxisAngle(node.userData.axis, V.q[j] || 0);
    }
    const rep = V.snap && V.snap.robot && V.snap.robot.tcp && V.snap.robot.tcp.position;
    if (V.reported) { V.reported.visible = !!rep; if (rep) V.reported.position.set(rep[0], rep[1], rep[2]); }
    const cp = Math.cos(V.pitch);
    V.camera.position.set(V.center.x + V.dist * cp * Math.cos(V.yaw), V.center.y + V.dist * cp * Math.sin(V.yaw), V.center.z + V.dist * Math.sin(V.pitch));
    V.camera.lookAt(V.center);
    V.renderer.render(V.scene, V.camera);
  } else if (V.dirty && !V.model && V.renderer && resize()) { V.dirty = false; V.renderer.render(V.scene, V.camera); }
  kick();
}
function kick() { if (!V.raf && V.attached && !document.hidden) V.raf = requestAnimationFrame(frame); }

function updateBand(stale) {
  V.band.hidden = !stale;
  if (stale) V.band.textContent = `ACİL DUR: veri bayat, görünüm donmuş olabilir (${stale}). Robot hareket ediyorsa ACİL DUR düğmesini kullan.`;
  V.wrap.classList.toggle('stale', !!stale);
}
function updateNote() {
  const r = V.snap && V.snap.robot, [tag, txt] = SL.modeNote(r && r.mode);
  V.tag.textContent = tag; V.tag.classList.toggle('est', tag === 'KESTİRİM');
  const mesh = V.meshUsed ? `${V.meshUsed}/${V.model ? V.model.links.length : '?'} link CAD mesh'i` : 'primitif şekiller (mesh yok)';
  const bad = V.meshFailed && V.meshFailed.length ? ` Mesh hatası: ${V.meshFailed.join('; ')}.` : '';
  V.note.textContent = `${txt} Çizim: ${mesh}.${bad} Sürükle: döndür · tekerlek: yakınlaştır · turuncu kutu: güvenli kutu · beyaz nokta: model TCP · yeşil halka: robotun bildirdiği TCP. Salt okunur; ölçüm değildir.`;
}
function refreshUi() {
  if (V.glFailed) return;
  const reason = V.model ? SL.jointProblem(V.model, V.snap, V.why) : (V.why || 'model yükleniyor…');
  if (V.model && !reason) V.msg.hidden = true;
  else { V.msg.hidden = false; V.msg.textContent = `Görünüm kullanılamıyor: ${reason}`; }
  V.robot.visible = !reason;
  updateNote(); V.dirty = true; kick();
}

/* ---------- dışa açılan ---------- */
function attach(container) {
  if (!V.root) build();
  container.append(V.root); V.attached = container; V.dirty = true;
  if (!V.glFailed) { if (!V.model && !V.retry) loadModel(); kick(); }
}
function detach() { V.attached = null; if (V.root && V.root.parentNode) V.root.remove(); }
function onState(snap) {
  V.snap = snap; V.stateAt = performance.now();
  if (!V.root || V.glFailed) return;
  applySnapTargets(false); V.dirty = true; refreshUi();
}

window.Scene3D = { attach, detach, onState, _V: V };
