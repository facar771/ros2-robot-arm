/* HubScene (UI_CONTRACT §9-C, sahibi S2): 3B sahnede nesne ekle/sil/poz/görünürlük, katmanlar, tıklama (onPick), gizmo,
 * kamera önayarları, ölçüm ve TCP izi. scene3d.js bunu `import '/scene_hub.js'` ile yükler ve SceneHub.create'i THREE +
 * dahili durumla çağırır. Bu dosyada `import`/`export` YOK (node --check sınıfı; THREE dışarıdan gelir, node testlerinde
 * gerçek three verilir). Saf hesaplar scene_logic.js'te (SceneLogic); burada yalnız THREE.
 * Dünya çerçevesi Z yukarı, birim metre; poz = {pos_m, rpy_deg} (W-P-R, robot_core.se3). SALT GÖRSEL: robota komut yolu
 * YOKTUR (hareket akışı yalnız approach.js'tedir: önce kontrol, sonra onay); sahne ne ölçümdür ne güvenlik işlevi.
 * Hata sözleşmesi: işlevler fırlatmaz; { ok:true, ... } ya da { ok:false, error, message } döner. WebGL yoksa
 * `implemented === false` ve her işlev { ok:false, error:'not_implemented' } döner (abonelikler boş iptal işlevi). */
'use strict';

window.SceneHub = (() => {
  const SELECT_EMISSIVE = 0x1f6feb, MEASURE_COLOR = 0xf0883e, TRAIL_COLOR = 0x3fb950, LABEL_PX = 128;
  const PRESETS = { iso: [-0.65, 0.45], top: [-0.65, 1.5], front: [-Math.PI / 2, 0.0], side: [0.0, 0.0] };   // [yaw, pitch]
  const MIN_FOCUS_DIST = 0.15, MAX_TRAIL = 2000, TRAIL_STEP_M = 0.002;
  const fail = (error, message) => ({ ok: false, error, message });
  const E = (code, message) => Object.assign(new Error(message), { code });

  function create({ THREE, V, SL, frame, refresh, loadGizmo }) {
    const H = { objs: new Map(), layers: Object.fromEntries(SL.LAYERS.map((l) => [l, true])), pickCbs: new Set(), gizmoCbs: new Set(),
      measureCbs: new Set(), pickLayers: new Set(['workpiece', 'tool', 'environment', 'markers']), measure: null, trail: null,
      gizmo: null, helper: null, gizmoId: null, gizmoWant: null, tc: null, gizmoTouched: false, dragging: false, selected: null };
    H.root = new THREE.Group(); H.root.name = 'hub-objects';        // sahneye scene3d.js build() sırasında eklenir (_attach)
    const ray = new THREE.Raycaster(), dirty = () => { V.dirty = true; };
    const need = (id) => { const r = H.objs.get(id); if (!r) throw E('not_found', `böyle bir nesne yok: ${id}`); return r; };
    const needView = () => { if (!V.scene || !V.camera) throw E('not_ready', '3B görünüm henüz açılmadı'); };
    const flange = () => (V.model && V.nodes.get(V.model.tcp.frame)) || null;
    const redraw = () => { if (V.msg) refresh(); else dirty(); };      // robot katmanı/not: görünüm kurulmadan çağrılabilir
    const say = (text) => { V.hubNote = text || ''; redraw(); };
    const safe = (cb, arg) => { try { cb(arg); } catch (e) { console.error('HubScene abonesi hata verdi:', e); } };

    /* ---------- görsel kurma ---------- */
    function material(s) {
      return new THREE.MeshStandardMaterial({ color: s.color, roughness: 0.55, metalness: 0.15, side: THREE.DoubleSide,
        transparent: s.opacity < 1, opacity: s.opacity, depthWrite: s.opacity >= 1 });
    }
    function labelSprite(text) {                       // numaralı işaret: sayı bir tuvale çizilir (HTML üretilmez)
      if (typeof document === 'undefined' || !text) return null;
      const c = document.createElement('canvas'); c.width = c.height = LABEL_PX;
      const g = c.getContext('2d'); if (!g) return null;
      g.fillStyle = 'rgba(13,17,23,.85)'; g.beginPath(); g.arc(LABEL_PX / 2, LABEL_PX / 2, LABEL_PX / 2 - 4, 0, 2 * Math.PI); g.fill();
      g.fillStyle = '#e6edf3'; g.font = `bold ${LABEL_PX * 0.5}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(text, LABEL_PX / 2, LABEL_PX / 2 + 4);
      const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
      sp.scale.setScalar(0.035); sp.position.set(0, 0, 0.03); sp.renderOrder = 10; return sp;
    }
    const meshOf = (geo, s) => { const m = new THREE.Mesh(geo, material(s)); m.userData.hubId = s.id; return m; };
    function bufferOf(parsed) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(parsed.positions, 3));
      g.setAttribute('normal', new THREE.BufferAttribute(parsed.normals, 3));
      return g;
    }
    async function loadMesh(rec) {
      const s = rec.spec, gm = s.geometry;
      let buf = gm.data;
      if (!buf) {
        const res = await fetch(gm.url, { credentials: 'same-origin' });
        if (!res.ok) throw new Error(`mesh indirilemedi (HTTP ${res.status})`);
        buf = await res.arrayBuffer();
      }
      if (H.objs.get(s.id) !== rec) return;                                    // bu arada silindi
      const m = meshOf(bufferOf(SL.parseStl(buf)), s);
      m.scale.setScalar(gm.scale); rec.visual = m; rec.group.add(m); applyLook(rec); dirty();
    }
    function build(rec) {
      const s = rec.spec, g = s.geometry;
      if (s.kind === 'mesh') { rec.ready = loadMesh(rec).catch((e) => { rec.error = String(e && e.message || e); throw e; }); rec.ready.catch(() => {}); return; }
      let v;
      if (s.kind === 'box') v = meshOf(new THREE.BoxGeometry(...g.size_m), s);
      else if (s.kind === 'sphere') v = meshOf(new THREE.SphereGeometry(g.radius_m, 24, 16), s);
      else if (s.kind === 'marker') {
        v = meshOf(new THREE.SphereGeometry(g.radius_m, 16, 12), s);
        const sp = labelSprite(g.number == null ? s.label : String(g.number)); if (sp) v.add(sp);
      } else if (s.kind === 'frame') v = new THREE.AxesHelper(g.size_m);
      else {
        const pts = g.points_m.map((p) => new THREE.Vector3(...p)); if (g.closed) pts.push(pts[0].clone());
        v = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
          new THREE.LineBasicMaterial({ color: s.color, transparent: s.opacity < 1, opacity: s.opacity }));
      }
      rec.visual = v; rec.group.add(v);
    }
    /* setFromPoints var olan özniteliği YENİDEN BOYUTLANDIRMAZ (nokta sayısı değişirse eski boyutta kalır): geometri yenilenir. */
    function setLinePoints(line, pts, closed) {
      const old = line.geometry, v = pts.map((p) => new THREE.Vector3(...p)); if (closed) v.push(v[0].clone());
      line.geometry = new THREE.BufferGeometry().setFromPoints(v); if (old) old.dispose();
    }
    function applyPose(group, pose) {
      group.position.set(...pose.pos_m);
      const R = SL.rpyToMatrix(pose.rpy_deg);                                    // Matrix4.set satır öncelikli
      group.quaternion.setFromRotationMatrix(new THREE.Matrix4().set(R[0], R[1], R[2], 0, R[3], R[4], R[5], 0, R[6], R[7], R[8], 0, 0, 0, 0, 1));
      group.updateMatrix();
    }
    const poseOfGroup = (group) => {                                             // yerel poz (üst çerçeveye göre)
      const e = new THREE.Matrix4().makeRotationFromQuaternion(group.quaternion).elements;
      return { pos_m: group.position.toArray(), rpy_deg: SL.matrixToRpy([e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]]) };
    };
    function applyLook(rec) {                                                    // saydamlık, renk, seçim vurgusu
      const s = rec.spec, sel = H.selected === s.id;
      rec.group.traverse((o) => {
        const mt = o.material; if (!mt || !mt.color || o.isSprite) return;
        mt.color.setHex(s.color); mt.opacity = s.opacity; mt.transparent = s.opacity < 1; mt.depthWrite = s.opacity >= 1;
        if (mt.emissive) { mt.emissive.setHex(sel ? SELECT_EMISSIVE : 0x000000); mt.emissiveIntensity = sel ? 0.55 : 0; }
      });
    }
    function applyVisibility() {
      for (const rec of H.objs.values()) rec.group.visible = rec.visible && H.layers[rec.spec.layer];
      if (V.grid) V.grid.visible = H.layers.grid;
      if (V.safeBox) V.safeBox.visible = H.layers.safebox;
      redraw();                                                                  // robot katmanı refreshUi'de uygulanır
    }
    function mount(rec) {                                                        // flanşa bağlı nesne robot modeliyle yeniden doğar
      const parent = rec.spec.attach === 'flange' ? flange() : H.root;
      if (rec.group.parent !== parent) { if (rec.group.parent) rec.group.removeFromParent(); if (parent) parent.add(rec.group); }
    }

    /* ---------- nesneler ---------- */
    function addObject(spec) {
      const s = SL.validateSpec(spec);
      if (H.objs.has(s.id)) throw E('exists', `nesne zaten var: ${s.id}`);
      const rec = { spec: s, group: new THREE.Group(), visual: null, visible: true, error: null, ready: Promise.resolve() };
      rec.group.name = s.id; applyPose(rec.group, s.pose); H.objs.set(s.id, rec);
      build(rec); mount(rec); applyVisibility(); return { ok: true, id: s.id };
    }
    function removeObject(id) {
      const rec = need(id);
      if (H.gizmoId === id || (H.gizmoWant && H.gizmoWant.id === id)) disableGizmo();
      if (H.selected === id) H.selected = null;
      rec.group.removeFromParent();
      rec.group.traverse((o) => { if (o.geometry) o.geometry.dispose(); const mt = o.material; if (mt) { if (mt.map) mt.map.dispose(); mt.dispose(); } });
      H.objs.delete(id); dirty();
    }
    function setPose(id, pose) {
      const rec = need(id), s = SL.validateSpec({ ...rec.spec, pose });                // yeniden doğrulama: geçersiz poz kabul edilmez
      rec.spec.pose = s.pose; applyPose(rec.group, s.pose); dirty();
    }
    function setVisible(idOrLayer, on) {
      if (SL.LAYERS.includes(idOrLayer)) H.layers[idOrLayer] = !!on; else need(idOrLayer).visible = !!on;
      applyVisibility();
    }
    function updateObject(id, patch) {                                           // EK (§9-C dışı): renk/saydamlık/ölçek/nokta güncelle
      const rec = need(id), s = rec.spec;
      if (patch.color !== undefined || patch.opacity !== undefined) {
        const v = SL.validateSpec({ ...s, color: patch.color === undefined ? s.color : patch.color, opacity: patch.opacity === undefined ? s.opacity : patch.opacity });
        s.color = v.color; s.opacity = v.opacity; applyLook(rec);
      }
      if (patch.scale !== undefined && s.kind === 'mesh') {
        s.geometry.scale = SL.validateSpec({ ...s, geometry: { ...s.geometry, scale: patch.scale } }).geometry.scale;
        if (rec.visual) rec.visual.scale.setScalar(s.geometry.scale);
      }
      if (patch.points_m !== undefined && s.kind === 'polyline') {
        const v = SL.validateSpec({ ...s, geometry: { points_m: patch.points_m, closed: s.geometry.closed } });
        s.geometry.points_m = v.geometry.points_m; setLinePoints(rec.visual, v.geometry.points_m, s.geometry.closed);
      }
      dirty();
    }
    function select(id) { const old = H.selected && H.objs.get(H.selected); H.selected = id; if (old) applyLook(old); if (id) applyLook(need(id)); dirty(); }
    const info = (id) => { const r = H.objs.get(id); return r ? { id, kind: r.spec.kind, layer: r.spec.layer, pose: r.spec.pose, attach: r.spec.attach, visible: r.visible, loaded: !!r.visual, error: r.error } : null; };

    /* ---------- TCP ve izi ---------- */
    function tcpOffset() { const t = V.model && V.model.tcp; return t ? { pos_m: (t.offset || [0, 0, 0]).slice(), rpy_deg: (t.wpr || [0, 0, 0]).map((a) => a * SL.R2D) } : null; }
    function tcpPose() {                                                         // flanş dünya pozu · TCP ofseti
      const node = flange(), off = tcpOffset(); if (!node || !off) return null;
      V.robot.updateMatrixWorld(true);
      const e = node.matrixWorld.elements;
      const fl = { R: [e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]], p: [e[12], e[13], e[14]] };
      return SL.poseJson(SL.poseMul(fl, SL.poseOf(off.pos_m, off.rpy_deg)));
    }
    function setTrail(on) {
      if (!on) { if (H.trail) { H.trail.line.removeFromParent(); H.trail.line.geometry.dispose(); H.trail = null; dirty(); } return; }
      if (H.trail) return;
      const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: TRAIL_COLOR }));
      line.frustumCulled = false; H.root.add(line); H.trail = { line, pts: [] };
    }
    function frameTick() {                                                       // her çizimde (scene3d.js frame; bayatken çağrılmaz)
      if (!H.trail) return;
      const p = tcpPose();
      if (p && SL.trailPush(H.trail.pts, p.pos_m, TRAIL_STEP_M, MAX_TRAIL)) { setLinePoints(H.trail.line, H.trail.pts); dirty(); }
    }
    const onModel = () => { for (const r of H.objs.values()) mount(r); applyVisibility(); };
    function attachToScene() {                                                   // scene3d.js build(): sahne artık var
      V.scene.add(H.root); if (H.helper) V.scene.add(H.helper); applyVisibility();
    }

    /* ---------- seçim / ölçüm ---------- */
    function pickAt(clientX, clientY) {                                          // isabet yoksa null
      const rc = V.canvas.getBoundingClientRect();
      if (!rc.width || !rc.height) return null;
      ray.setFromCamera(new THREE.Vector2(((clientX - rc.left) / rc.width) * 2 - 1, -(((clientY - rc.top) / rc.height) * 2 - 1)), V.camera);
      V.scene.updateMatrixWorld(true);
      const targets = [];
      for (const r of H.objs.values()) if (r.visual && r.group.parent && r.group.visible && H.pickLayers.has(r.spec.layer)) targets.push(r.visual);
      for (const hit of ray.intersectObjects(targets, true)) {
        let o = hit.object; while (o && !o.userData.hubId) o = o.parent;
        if (!o || !hit.face) continue;
        const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld).toArray(), d = ray.ray.direction.toArray();
        return { objectId: o.userData.hubId, layer: H.objs.get(o.userData.hubId).spec.layer, point_m: hit.point.toArray(), normal: SL.facingNormal(n, d) };
      }
      return null;
    }
    function clearMeasureViz() { const m = H.measure; for (const c of [...m.viz.children]) { c.geometry.dispose(); c.material.dispose(); m.viz.remove(c); } m.pts = []; }
    function measureTap(res) {
      const m = H.measure; if (!res) return;
      if (m.pts.length >= 2) clearMeasureViz();
      m.pts.push(res.point_m);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.004, 12, 8), new THREE.MeshBasicMaterial({ color: MEASURE_COLOR, depthTest: false }));
      dot.position.set(...res.point_m); dot.renderOrder = 11; m.viz.add(dot);
      if (m.pts.length === 2) {
        const ln = new THREE.Line(new THREE.BufferGeometry().setFromPoints(m.pts.map((p) => new THREE.Vector3(...p))), new THREE.LineBasicMaterial({ color: MEASURE_COLOR, depthTest: false }));
        ln.renderOrder = 11; m.viz.add(ln);
        const out = { a_m: m.pts[0], b_m: m.pts[1], distance_m: SL.distance(m.pts[0], m.pts[1]) };
        say(`Ölçüm: ${(out.distance_m * 1000).toFixed(1)} mm (modeller arası mesafe; gerçek ölçüm değildir).`);
        for (const cb of [...H.measureCbs]) safe(cb, out);
      }
      dirty();
    }
    function measure(on) {
      needView();
      if (on && !H.measure) { H.measure = { pts: [], viz: new THREE.Group() }; H.root.add(H.measure.viz); }
      if (!on && H.measure) { clearMeasureViz(); H.measure.viz.removeFromParent(); H.measure = null; say(''); }
      return { ok: true, on: !!H.measure };
    }
    /* scene3d.js bir TIKLAMA (sürükleme değil) algılayınca çağırır. İsabetsiz tıklama hiçbir aboneyi çağırmaz. */
    function tap(clientX, clientY) {
      if (H.gizmoTouched) { H.gizmoTouched = false; return; }                    // gizmo tutamacına dokunuş seçim sayılmaz
      const res = pickAt(clientX, clientY);
      if (H.measure) { measureTap(res); return; }
      if (res) for (const cb of [...H.pickCbs]) safe(cb, { objectId: res.objectId, point_m: res.point_m, normal: res.normal, layer: res.layer });
    }

    /* ---------- gizmo ---------- */
    function emitGizmo(phase) {
      const rec = H.gizmoId && H.objs.get(H.gizmoId); if (!rec) return;
      const pose = poseOfGroup(rec.group); rec.spec.pose = pose;
      for (const cb of [...H.gizmoCbs]) safe(cb, { id: H.gizmoId, pose, phase });
    }
    function applyGizmo(TC, want) {
      const rec = H.objs.get(want.id);
      if (H.gizmoWant !== want || !rec) return;                                  // yüklenirken kapatıldı/silindi
      if (!H.gizmo) {
        H.gizmo = new TC.TransformControls(V.camera, V.canvas); H.helper = H.gizmo.getHelper(); V.scene.add(H.helper);
        H.gizmo.addEventListener('change', dirty);
        H.gizmo.addEventListener('mouseDown', () => { H.gizmoTouched = true; emitGizmo('start'); });
        H.gizmo.addEventListener('objectChange', () => emitGizmo('move'));
        H.gizmo.addEventListener('mouseUp', () => emitGizmo('end'));
        H.gizmo.addEventListener('dragging-changed', (e) => { H.dragging = !!e.value; });
      }
      H.gizmo.setMode(want.mode); H.gizmo.setSpace(want.space);
      H.gizmo.setTranslationSnap(want.snap.pos_m || null); H.gizmo.setRotationSnap(want.snap.rot_deg ? want.snap.rot_deg * SL.D2R : null);
      H.gizmo.attach(rec.group); H.gizmoId = want.id; dirty();
    }
    /* TransformControls ilk istekte indirilir (kullanılmazsa hiç); eşzamanlı döner, eklenme ms sürer, hata görünür not olur. */
    function enableGizmo(id, opts = {}) {
      needView(); need(id);
      const snap = SL.snapSettings(opts.snap), mode = opts.mode || 'translate', space = opts.space || 'world';
      if (!['translate', 'rotate'].includes(mode)) throw E('bad_request', 'gizmo kipi translate/rotate olmalı');
      if (!['world', 'local'].includes(space)) throw E('bad_request', 'gizmo uzayı world/local olmalı');
      const want = { id, mode, space, snap }; H.gizmoWant = want;
      H.tc = H.tc || loadGizmo();
      H.tc.then((TC) => applyGizmo(TC, want), (e) => { H.tc = null; H.gizmoWant = null; say(`Gizmo yüklenemedi: ${e && e.message || e}`); });
    }
    function disableGizmo() { H.gizmoWant = null; if (H.gizmo) H.gizmo.detach(); H.gizmoId = null; H.dragging = false; H.gizmoTouched = false; dirty(); }

    /* ---------- kamera ---------- */
    function cameraPreset(name) {
      const p = PRESETS[name]; if (!p) throw E('bad_request', `kamera önayarı ${Object.keys(PRESETS).join('/')} olmalı`);
      V.yaw = p[0]; V.pitch = p[1]; V.userMoved = true; dirty();
    }
    function focus(target) {
      needView();
      if (target === 'robot') { frame(true); return; }
      const rec = need(target); V.scene.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(rec.group); if (box.isEmpty()) return;
      const sph = box.getBoundingSphere(new THREE.Sphere());
      V.center.copy(sph.center); V.userMoved = true;
      V.dist = Math.max(MIN_FOCUS_DIST, (sph.radius / Math.sin(THREE.MathUtils.degToRad(V.camera.fov / 2))) * 1.3); dirty();
    }

    /* ---------- dışa açılan yüz: dondurulmuş, hata fırlatmaz ---------- */
    const guard = (name, fn) => (...a) => {
      if (V.glFailed) return fail('not_implemented', `HubScene.${name}: WebGL açılamadı, 3B sahne yok`);
      try { const r = fn(...a); return r === undefined ? { ok: true } : r; } catch (e) { return fail(e.code || 'bad_request', String(e && e.message || e)); }
    };
    const sub = (set) => (cb) => {
      if (V.glFailed || typeof cb !== 'function') return () => {};
      set.add(cb); return () => set.delete(cb);
    };
    const ready = (id) => {                                                      // mesh yüklenince { ok } (asla reddetmez)
      const rec = H.objs.get(id); if (!rec) return Promise.resolve(fail('not_found', `böyle bir nesne yok: ${id}`));
      return rec.ready.then(() => ({ ok: true }), (e) => fail('load_failed', String(e && e.message || e)));
    };
    return Object.freeze({
      get implemented() { return !V.glFailed; },
      layers: SL.LAYERS, kinds: SL.KINDS, presets: Object.freeze(Object.keys(PRESETS)),
      addObject: guard('addObject', addObject), removeObject: guard('removeObject', removeObject), setPose: guard('setPose', setPose),
      setVisible: guard('setVisible', setVisible), onPick: sub(H.pickCbs), enableGizmo: guard('enableGizmo', enableGizmo),
      onGizmoChange: sub(H.gizmoCbs), cameraPreset: guard('cameraPreset', cameraPreset), focus: guard('focus', focus),
      measure: guard('measure', measure), setTrail: guard('setTrail', (on) => { setTrail(on); }),
      /* EK (§9-C dışı, geriye uyumlu): */
      updateObject: guard('updateObject', updateObject), select: guard('select', select), disableGizmo: guard('disableGizmo', disableGizmo),
      onMeasure: sub(H.measureCbs), ready, info, list: () => [...H.objs.keys()], tcpPose, tcpOffset,
      setPickLayers(layers) { H.pickLayers = new Set(layers); }, layerVisible: (l) => H.layers[l] !== false, isGizmoDragging: () => H.dragging,
      /* scene3d.js'e özel kancalar */
      _attach: attachToScene, _tap: tap, _pickAt: pickAt, _frame: frameTick, _onModel: onModel, _state: H });
  }
  return { create };
})();
