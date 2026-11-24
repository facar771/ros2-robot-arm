/* HubScene (scene_hub.js, UI_CONTRACT §9-C) GERÇEK three ile, WebGL/DOM olmadan:
 *  · sözleşme: imzalar, sabitler, dondurulmuş yüz, sonuç nesneleri ({ok}/{ok:false,error,message}), sayfa önekli kimlik,
 *    WebGL yokken `implemented:false` + her işlev not_implemented (abonelik: iptal işlevi);
 *  · nesneler/katmanlar, onPick (yalnız isabette; nokta + normal), ölçüm, TCP izi/pozu, flanşa bağlı takım,
 *    gizmo olayları phase start/move/end (sahte TransformControls), kamera önayarları.
 *   node hubscene_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
const { pathToFileURL } = require('url');
const WEB = path.resolve(process.argv[2]);
global.window = global;
require(path.join(WEB, 'scene_logic.js'));
require(path.join(WEB, 'scene_hub.js'));
const SL = SceneLogic;
const near = (a, b, tol = 1e-6) => assert.ok(Math.abs(a - b) <= tol, `${a} ≉ ${b}`);
const nearV = (a, b, tol = 1e-6) => b.forEach((x, i) => near(a[i], x, tol));
const ok = (r, what) => assert.strictEqual(r.ok, true, `${what}: ${JSON.stringify(r)}`);
const bad = (r, error, rx) => { assert.strictEqual(r.ok, false, JSON.stringify(r)); assert.strictEqual(r.error, error, JSON.stringify(r)); if (rx) assert.match(r.message, rx); };

function stlCube(sizeRaw, flip = false) {                                  // ikili STL: [0,size]^3 küp (12 üçgen)
  const c = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]].map((v) => v.map((x) => x * sizeRaw));
  const quads = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
  const tris = quads.flatMap(([a, b, d, e]) => [[c[a], c[b], c[d]], [c[a], c[d], c[e]]]);
  const buf = new ArrayBuffer(84 + tris.length * 50), dv = new DataView(buf);
  dv.setUint32(80, tris.length, true);
  tris.forEach((t, i) => {
    const o = 84 + i * 50, [p, q, r] = flip ? [t[0], t[2], t[1]] : t;
    const u = q.map((x, k) => x - p[k]), v = r.map((x, k) => x - p[k]);
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]], l = Math.hypot(...n) || 1;
    n.forEach((x, k) => dv.setFloat32(o + 4 * k, x / l, true));
    [p, q, r].flat().forEach((x, k) => dv.setFloat32(o + 12 + 4 * k, x, true));
  });
  return buf;
}

(async () => {
  const THREE = await import(pathToFileURL(path.join(WEB, 'vendor', 'three', 'three.module.js')).href);
  const mk = (gizmoFails = false) => {
    const V = { scene: new THREE.Scene(), camera: new THREE.PerspectiveCamera(45, 800 / 600, 0.01, 30), robot: new THREE.Group(), nodes: new Map(),
      model: null, dirty: false, center: new THREE.Vector3(), yaw: -0.65, pitch: 0.45, dist: 1.5, userMoved: false, glFailed: null, hubNote: '',
      grid: new THREE.Object3D(), safeBox: new THREE.Object3D(), canvas: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }) } };
    V.scene.add(V.robot, V.grid, V.safeBox);
    V.camera.up.set(0, 0, 1); V.camera.position.set(0.3, -0.3, 1); V.camera.lookAt(0.3, 0, 0.1); V.camera.updateMatrixWorld(true);   // tam dikey bakış `up` ile paralel olurdu
    const fl = new THREE.Group(); fl.position.set(0.3, 0, 0.2); V.robot.add(fl); V.nodes.set('flange', fl);
    V.model = { tcp: { frame: 'flange', offset: [0, 0, 0.05], wpr: [0, Math.PI / 2, 0] } };
    const framed = [], calls = [], c = { refreshed: 0 };
    class FakeTC extends THREE.EventDispatcher {
      constructor(cam, el) { super(); this.helper = new THREE.Object3D(); calls.push(['new', !!cam, !!el]); }
      getHelper() { return this.helper; }
      setMode(m) { calls.push(['mode', m]); } setSpace(s) { calls.push(['space', s]); }
      setTranslationSnap(v) { calls.push(['tsnap', v]); } setRotationSnap(v) { calls.push(['rsnap', v]); }
      attach(o) { this.obj = o; calls.push(['attach', o.name]); } detach() { this.obj = null; calls.push(['detach']); }
    }
    const hub = SceneHub.create({ THREE, V, SL, frame: (f) => framed.push(f), refresh: () => { c.refreshed += 1; },
      loadGizmo: async () => { if (gizmoFails) throw new Error('indirme yok'); return { TransformControls: FakeTC }; } });
    hub._attach();                                                          // scene3d.js build() sahneyi kurunca çağırır
    return { V, hub, framed, calls, c };
  };
  const cube = (id, extra = {}) => ({ id, kind: 'box', layer: 'environment', geometry: { size_m: [0.1, 0.1, 0.1] }, pose: { pos_m: [0.3, 0, 0.05], rpy_deg: [0, 0, 0] }, ...extra });

  /* --- sözleşme: imzalar, sabitler, dondurulmuş yüz --- */
  let { V, hub, c } = mk();
  assert.strictEqual(hub.implemented, true);
  assert.deepStrictEqual([...hub.layers], ['robot', 'workpiece', 'tool', 'environment', 'path', 'markers', 'safebox', 'grid']);
  assert.deepStrictEqual([...hub.kinds], ['mesh', 'box', 'sphere', 'frame', 'polyline', 'marker']);
  assert.deepStrictEqual([...hub.presets], ['iso', 'top', 'front', 'side']);
  for (const n of ['addObject', 'removeObject', 'setPose', 'setVisible', 'enableGizmo', 'cameraPreset', 'focus', 'measure', 'setTrail']) assert.strictEqual(typeof hub[n], 'function', n);
  for (const n of ['onPick', 'onGizmoChange']) { const off = hub[n](() => {}); assert.strictEqual(typeof off, 'function', n); off(); }
  assert.ok(Object.isFrozen(hub));                                          // sayfa sahne API'sini kurcalayamaz
  assert.throws(() => { 'use strict'; hub.addObject = () => 1; });

  /* --- WebGL yok: implemented:false, her işlev not_implemented, abonelik iptal işlevi döner --- */
  const dead = mk(); dead.V.glFailed = 'WebGL açılamadı';
  assert.strictEqual(dead.hub.implemented, false);
  for (const n of ['addObject', 'removeObject', 'setPose', 'setVisible', 'enableGizmo', 'cameraPreset', 'focus', 'measure', 'setTrail']) {
    const r = dead.hub[n]('x:y', {}); bad(r, 'not_implemented', new RegExp(n));
  }
  for (const n of ['onPick', 'onGizmoChange']) { const off = dead.hub[n](() => assert.fail('çağrılmamalı')); assert.strictEqual(typeof off, 'function'); off(); }
  assert.strictEqual(dead.hub.list().length, 0);

  /* --- nesneler, katmanlar, hata sözleşmesi --- */
  let r = hub.addObject(cube('t:kutu', { color: 0x58a6ff })); ok(r, 'addObject'); assert.strictEqual(r.id, 't:kutu');
  assert.deepStrictEqual(hub.list(), ['t:kutu']); assert.strictEqual(hub.info('t:kutu').layer, 'environment');
  bad(hub.addObject(cube('t:kutu')), 'exists', /zaten var/);
  bad(hub.addObject(cube('kutu')), 'bad_request', /<sayfa>/);              // kimlik sayfa önekiyle başlamalı (§9-C)
  bad(hub.addObject({ id: 't:x', kind: 'cone' }), 'bad_request', /tür/);
  bad(hub.addObject(null), 'bad_request');
  assert.strictEqual(hub.info('t:yok'), null);
  for (const [kind, geometry] of [['sphere', { radius_m: 0.02 }], ['frame', { size_m: 0.1 }], ['marker', { radius_m: 0.01, number: 3 }], ['polyline', { points_m: [[0, 0, 0], [0.1, 0, 0], [0.1, 0.1, 0]] }]]) {
    ok(hub.addObject({ id: `t:o-${kind}`, kind, geometry }), kind);
  }
  assert.strictEqual(hub.list().length, 5);
  ok(hub.addObject({ id: 't:kapali', kind: 'polyline', geometry: { points_m: [[0, 0, 0], [0.1, 0, 0], [0.1, 0.1, 0]], closed: true } }));
  assert.strictEqual(V.scene.getObjectByName('t:kapali').children[0].geometry.getAttribute('position').count, 4);   // kapalı: ilk nokta sona eklenir
  assert.strictEqual(V.scene.getObjectByName('t:o-polyline').children[0].geometry.getAttribute('position').count, 3);
  ok(hub.removeObject('t:kapali'));
  ok(hub.setPose('t:kutu', { pos_m: [0.31, 0.02, 0.05], rpy_deg: [0, 0, 90] }));
  const g = V.scene.getObjectByName('t:kutu'); nearV(g.position.toArray(), [0.31, 0.02, 0.05]);
  nearV(new THREE.Vector3(1, 0, 0).applyQuaternion(g.quaternion).toArray(), [0, 1, 0]);                  // yaw 90°: x -> y
  bad(hub.setPose('t:kutu', { pos_m: [0, 0, 99], rpy_deg: [0, 0, 0] }), 'bad_request', /konum/);
  nearV(g.position.toArray(), [0.31, 0.02, 0.05]);                                                         // reddedilen poz uygulanmadı
  bad(hub.setPose('t:yok', { pos_m: [0, 0, 0], rpy_deg: [0, 0, 0] }), 'not_found');
  ok(hub.setVisible('t:kutu', false)); assert.strictEqual(g.visible, false); ok(hub.setVisible('t:kutu', true)); assert.strictEqual(g.visible, true);
  hub.setVisible('environment', false); assert.strictEqual(g.visible, false); assert.strictEqual(hub.info('t:kutu').visible, true);
  hub.setVisible('environment', true); assert.strictEqual(g.visible, true);
  hub.setVisible('grid', false); assert.strictEqual(V.grid.visible, false); hub.setVisible('safebox', false); assert.strictEqual(V.safeBox.visible, false);
  const before = c.refreshed; hub.setVisible('robot', false); assert.strictEqual(hub.layerVisible('robot'), false); hub.setVisible('robot', true);
  assert.ok(V.msg === undefined && c.refreshed === before);                                                // görünüm kurulmadan refreshUi çağrılmaz
  bad(hub.setVisible('yok', true), 'not_found');
  ok(hub.updateObject('t:kutu', { color: '#ff0000', opacity: 0.4 }));
  assert.strictEqual(g.children[0].material.color.getHex(), 0xff0000); near(g.children[0].material.opacity, 0.4); assert.ok(g.children[0].material.transparent);
  bad(hub.updateObject('t:kutu', { opacity: 3 }), 'bad_request', /saydamlık/);
  ok(hub.updateObject('t:o-polyline', { points_m: [[0, 0, 0], [0.2, 0, 0]] }));
  assert.strictEqual(V.scene.getObjectByName('t:o-polyline').children[0].geometry.getAttribute('position').count, 2);
  ok(hub.select('t:kutu')); near(g.children[0].material.emissiveIntensity, 0.55); hub.select(null); near(g.children[0].material.emissiveIntensity, 0);
  ok(hub.removeObject('t:o-sphere')); assert.ok(!hub.list().includes('t:o-sphere')); assert.strictEqual(V.scene.getObjectByName('t:o-sphere'), undefined);
  bad(hub.removeObject('t:o-sphere'), 'not_found', /böyle bir nesne/);

  /* --- sahne kurulmadan eklenen nesneler _attach ile sahneye girer --- */
  const late = mk(); late.V.scene.remove(late.hub._state.root);
  ok(late.hub.addObject(cube('t:erken'))); assert.strictEqual(late.V.scene.getObjectByName('t:erken'), undefined);
  late.hub._attach(); assert.ok(late.V.scene.getObjectByName('t:erken'));

  /* --- mesh (ArrayBuffer ile; üretimde url) ve ölçek --- */
  ok(hub.addObject({ id: 't:parca', kind: 'mesh', layer: 'workpiece', geometry: { data: stlCube(40), scale: 0.001 }, pose: { pos_m: [0.4, 0, 0], rpy_deg: [0, 0, 0] } }));
  ok(await hub.ready('t:parca')); assert.ok(hub.info('t:parca').loaded);
  const mesh = V.scene.getObjectByName('t:parca').children[0]; near(mesh.scale.x, 0.001);
  new THREE.Box3().setFromObject(V.scene.getObjectByName('t:parca')).getSize(new THREE.Vector3()).toArray().forEach((x) => near(x, 0.04, 1e-6));   // 40 mm
  hub.updateObject('t:parca', { scale: 0.0254 }); near(mesh.scale.x, 0.0254);                              // birim değişti (inç)
  bad(hub.addObject({ id: 't:harici', kind: 'mesh', geometry: { url: 'https://x/y.stl' } }), 'bad_request', /aynı kaynak/);   // CSP: yalnız aynı kaynak
  hub.addObject({ id: 't:bozuk', kind: 'mesh', geometry: { data: new ArrayBuffer(10) } });
  bad(await hub.ready('t:bozuk'), 'load_failed', /kısa/); assert.match(hub.info('t:bozuk').error, /kısa/); assert.ok(!hub.info('t:bozuk').loaded);
  global.fetch = async () => ({ ok: false, status: 404 });                                                 // indirme addObject içinde başlar: önce kur
  hub.addObject({ id: 't:agda', kind: 'mesh', geometry: { url: '/api/yok/mesh' } });
  bad(await hub.ready('t:agda'), 'load_failed', /404/);
  bad(await hub.ready('t:yok'), 'not_found');
  for (const id of ['t:parca', 't:bozuk', 't:agda']) hub.removeObject(id);

  /* --- tıklama: ışın-mesh kesişimi; onPick YALNIZ isabette --- */
  ({ V, hub, c } = mk());
  hub.addObject(cube('t:kutu'));
  let hit = hub._pickAt(400, 300);
  assert.strictEqual(hit.objectId, 't:kutu'); assert.strictEqual(hit.layer, 'environment');
  nearV(hit.point_m, [0.3, 0, 0.1], 1e-6); nearV(hit.normal, [0, 0, 1], 1e-6);                             // üst yüzey: nokta + normal
  assert.strictEqual(hub._pickAt(5, 5), null);
  hub.setVisible('t:kutu', false); assert.strictEqual(hub._pickAt(400, 300), null);                         // gizli nesne seçilmez
  hub.setVisible('t:kutu', true); hub.setPickLayers(['workpiece']); assert.strictEqual(hub._pickAt(400, 300), null); hub.setPickLayers(['environment']);
  hub.addObject({ id: 't:ters', kind: 'mesh', layer: 'workpiece', geometry: { data: stlCube(0.2, true) }, pose: { pos_m: [0.2, -0.1, 0.05], rpy_deg: [0, 0, 0] } });
  await hub.ready('t:ters'); hub.setPickLayers(['workpiece']);                                              // sarma yönü ters mesh
  hit = hub._pickAt(400, 300); assert.strictEqual(hit.objectId, 't:ters'); nearV(hit.normal, [0, 0, 1], 1e-6);   // normal yine KAMERAYA bakar
  hub.removeObject('t:ters'); hub.setPickLayers(['environment']);
  const got = [], off = hub.onPick((p) => got.push(p));
  hub._tap(400, 300); assert.strictEqual(got.length, 1);
  assert.deepStrictEqual(Object.keys(got[0]).sort(), ['layer', 'normal', 'objectId', 'point_m']); assert.strictEqual(got[0].objectId, 't:kutu');
  hub._tap(5, 5); assert.strictEqual(got.length, 1);                                                        // isabetsiz tıklama aboneyi ÇAĞIRMAZ
  const thrower = hub.onPick(() => { throw new Error('sayfa hatası'); }); const log = console.error; console.error = () => {};
  hub._tap(400, 300); console.error = log; assert.strictEqual(got.length, 2); thrower();                    // bir abonenin hatası diğerini/çizimi bozmaz
  off(); hub._tap(400, 300); assert.strictEqual(got.length, 2);                                            // abonelik iptali

  /* --- ölçüm --- */
  const meas = []; hub.onMeasure((m) => meas.push(m)); V.msg = {}; ok(hub.measure(true));
  const pa = hub._pickAt(400, 300), pb = hub._pickAt(420, 310);
  hub._tap(400, 300); assert.strictEqual(meas.length, 0); hub._tap(420, 310);
  assert.strictEqual(meas.length, 1); near(meas[0].distance_m, SL.distance(pa.point_m, pb.point_m), 1e-9); assert.ok(meas[0].distance_m > 0.001);
  assert.match(V.hubNote, /Ölçüm: [\d.]+ mm .*gerçek ölçüm değildir/); assert.ok(c.refreshed > 0);        // not görünür; ölçüm sanılmasın
  const after = []; hub.onPick((p) => after.push(p)); hub._tap(400, 300); assert.strictEqual(after.length, 0);   // ölçüm açıkken onPick çağrılmaz
  ok(hub.measure(false)); assert.strictEqual(V.hubNote, ''); hub._tap(400, 300); assert.strictEqual(after.length, 1);
  delete V.msg;

  /* --- TCP, iz --- */
  const tcp = hub.tcpPose(); nearV(tcp.pos_m, [0.3, 0, 0.25], 1e-9); nearV(tcp.rpy_deg, [0, 90, 0], 1e-6);       // flanş (0.3,0,0.2) + ofset z 0.05, P = 90°
  nearV(hub.tcpOffset().rpy_deg, [0, 90, 0], 1e-9);
  ok(hub.setTrail(true)); hub._frame(); hub._frame();                                                     // aynı yerde: tek nokta
  assert.strictEqual(hub._state.trail.pts.length, 1);
  V.nodes.get('flange').position.set(0.4, 0, 0.2); hub._frame();                                           // 10 cm gitti
  assert.strictEqual(hub._state.trail.pts.length, 2); assert.strictEqual(hub._state.trail.line.geometry.getAttribute('position').count, 2);
  hub.setTrail(false); assert.strictEqual(hub._state.trail, null); hub._frame();
  V.nodes.get('flange').position.set(0.3, 0, 0.2);

  /* --- flanşa bağlı takım --- */
  ok(hub.addObject({ id: 't:alet', kind: 'box', layer: 'tool', attach: 'flange', geometry: { size_m: [0.02, 0.02, 0.06] }, pose: { pos_m: [0, 0, 0.03], rpy_deg: [0, 0, 0] } }));
  assert.strictEqual(V.scene.getObjectByName('t:alet').parent, V.nodes.get('flange'));
  V.scene.updateMatrixWorld(true);
  nearV(new THREE.Vector3().setFromMatrixPosition(V.scene.getObjectByName('t:alet').matrixWorld).toArray(), [0.3, 0, 0.23], 1e-9);   // robot hareket edince takım gelir
  const flange2 = new THREE.Group(); V.robot.clear(); V.robot.add(flange2); V.nodes.set('flange', flange2);        // model yeniden kuruldu
  hub._onModel(); assert.strictEqual(V.scene.getObjectByName('t:alet').parent, flange2);
  V.model = null; V.robot.clear(); V.nodes.clear(); ok(hub.addObject({ id: 't:alet2', kind: 'box', attach: 'flange', geometry: { size_m: [0.02, 0.02, 0.02] } }));
  assert.strictEqual(V.scene.getObjectByName('t:alet2'), undefined);                                       // model yokken sahnede değil (bekler)
  assert.strictEqual(hub.tcpPose(), null);

  /* --- gizmo: sahte TransformControls, phase start/move/end --- */
  const m2 = mk(); hub = m2.hub; V = m2.V;
  hub.addObject(cube('t:kutu'));
  bad(hub.enableGizmo('t:kutu', { mode: 'scale' }), 'bad_request', /translate\/rotate/);
  bad(hub.enableGizmo('t:kutu', { space: 'x' }), 'bad_request', /world\/local/);
  bad(hub.enableGizmo('t:yok'), 'not_found');
  ok(hub.enableGizmo('t:kutu')); await hub._state.tc;                                                       // varsayılan: taşı, dünya, 1 mm / 1°
  assert.deepStrictEqual(m2.calls.filter((x) => x[0] !== 'new'), [['mode', 'translate'], ['space', 'world'], ['tsnap', 0.001], ['rsnap', Math.PI / 180], ['attach', 't:kutu']]);
  ok(hub.enableGizmo('t:kutu', { mode: 'rotate', space: 'local', snap: false })); await hub._state.tc;       // ikinci kez: aynı denetleyici, yakalama kapalı
  assert.strictEqual(m2.calls.filter((x) => x[0] === 'new').length, 1);
  assert.deepStrictEqual(m2.calls.slice(-5), [['mode', 'rotate'], ['space', 'local'], ['tsnap', null], ['rsnap', null], ['attach', 't:kutu']]);
  const ev = []; hub.onGizmoChange((e) => ev.push(e));
  const tc = hub._state.gizmo, obj = V.scene.getObjectByName('t:kutu');
  tc.dispatchEvent({ type: 'dragging-changed', value: true }); assert.ok(hub.isGizmoDragging());
  tc.dispatchEvent({ type: 'mouseDown' });
  obj.position.set(0.35, 0.01, 0.05); obj.quaternion.setFromEuler(new THREE.Euler(0, 0, Math.PI / 2)); tc.dispatchEvent({ type: 'objectChange' });
  tc.dispatchEvent({ type: 'mouseUp' }); tc.dispatchEvent({ type: 'dragging-changed', value: false }); assert.ok(!hub.isGizmoDragging());
  assert.deepStrictEqual(ev.map((e) => e.phase), ['start', 'move', 'end']); assert.ok(ev.every((e) => e.id === 't:kutu' && !('final' in e)));
  nearV(ev[1].pose.pos_m, [0.35, 0.01, 0.05], 1e-6); nearV(ev[1].pose.rpy_deg, [0, 0, 90], 1e-5); nearV(ev[2].pose.pos_m, [0.35, 0.01, 0.05], 1e-6);
  nearV(hub.info('t:kutu').pose.pos_m, [0.35, 0.01, 0.05], 1e-6);                                            // spec poz'u gizmoyla güncel
  const taps = []; hub.onPick((p) => taps.push(p)); hub._tap(400, 300); assert.strictEqual(taps.length, 0);   // gizmo tutulduysa tıklama seçim sayılmaz
  hub._tap(400, 300); assert.strictEqual(taps.length, 1);                                                    // bayrak tek tıklamalıktır
  ok(hub.disableGizmo()); assert.strictEqual(hub._state.gizmoId, null); assert.strictEqual(m2.calls.at(-1)[0], 'detach');
  ok(hub.enableGizmo('t:kutu')); await hub._state.tc; ok(hub.removeObject('t:kutu')); assert.strictEqual(hub._state.gizmoId, null);   // silinen nesnenin gizmosu kalkar
  hub.addObject(cube('t:b')); const n0 = m2.calls.filter((x) => x[0] === 'attach').length;
  ok(hub.enableGizmo('t:b')); ok(hub.removeObject('t:b')); await hub._state.tc;                              // yüklenirken silindi: bağlanmaz
  assert.strictEqual(m2.calls.filter((x) => x[0] === 'attach').length, n0);
  const m4 = mk(true); m4.hub.addObject(cube('t:kutu')); ok(m4.hub.enableGizmo('t:kutu'));                    // gizmo dosyası inmedi: görünür not, çökme yok
  await m4.hub._state.tc.catch(() => {}); await new Promise((res) => setTimeout(res, 0)); assert.match(m4.V.hubNote, /Gizmo yüklenemedi: indirme yok/);

  /* --- kamera --- */
  const m3 = mk(); hub = m3.hub; V = m3.V;
  ok(hub.cameraPreset('top')); assert.ok(V.pitch > 1.4 && V.userMoved); hub.cameraPreset('front'); near(V.yaw, -Math.PI / 2); near(V.pitch, 0);
  hub.cameraPreset('side'); near(V.yaw, 0); hub.cameraPreset('iso'); near(V.yaw, -0.65);
  bad(hub.cameraPreset('altta'), 'bad_request', /önayarı/);
  ok(hub.focus('robot')); assert.deepStrictEqual(m3.framed, [true]);
  hub.addObject({ id: 't:k', kind: 'box', geometry: { size_m: [0.1, 0.1, 0.1] }, pose: { pos_m: [0.5, 0.2, 0.05], rpy_deg: [0, 0, 0] } });
  ok(hub.focus('t:k')); nearV(V.center.toArray(), [0.5, 0.2, 0.05], 1e-6); assert.ok(V.dist >= 0.15 && V.dist < 1);
  bad(hub.focus('t:yok'), 'not_found');
  const noView = mk(); noView.V.scene = null; noView.V.camera = null;                                       // görünüm henüz açılmadı: işlev çökmez
  bad(noView.hub.focus('robot'), 'not_ready'); bad(noView.hub.measure(true), 'not_ready'); ok(noView.hub.cameraPreset('top'));
  console.log('HUBSCENE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
