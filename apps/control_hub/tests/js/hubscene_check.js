/* HubScene iskeleti (scene3d.js'in sonundaki blok): imzalar var, hepsi 'not_implemented' döner, donmuş, abonelik iptal işlevi döner.
 * scene3d.js bir ES modülü ve Three.js ister; bu yüzden yalnız HubScene bloğu kesilip çalıştırılır.
 *   node hubscene_check.js <web dizini> */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(path.resolve(process.argv[2]), 'scene3d.js'), 'utf8');
const at = src.indexOf('/* ---------- HubScene');
assert.ok(at > 0, 'HubScene bloğu yok');
global.window = global;
new Function(src.slice(at))();                                  // bloğu çalıştır (window.HubScene tanımlanır)

const H = window.HubScene;
assert.strictEqual(H.implemented, false);
assert.deepStrictEqual([...H.layers], ['robot', 'workpiece', 'tool', 'environment', 'path', 'markers', 'safebox', 'grid']);
assert.deepStrictEqual([...H.kinds], ['mesh', 'box', 'sphere', 'frame', 'polyline', 'marker']);
assert.deepStrictEqual([...H.presets], ['iso', 'top', 'front', 'side']);
for (const n of ['addObject', 'removeObject', 'setPose', 'setVisible', 'enableGizmo', 'cameraPreset', 'focus', 'measure', 'setTrail']) {
  assert.strictEqual(typeof H[n], 'function', n);
  const r = H[n]('x', {});
  assert.deepStrictEqual([r.ok, r.error], [false, 'not_implemented'], n);
  assert.match(r.message, new RegExp(n));
}
for (const n of ['onPick', 'onGizmoChange']) {
  const off = H[n](() => {});
  assert.strictEqual(typeof off, 'function', n); off();                  // iptal işlevi her zaman çağrılabilir
}
assert.ok(Object.isFrozen(H));                                           // sayfa sahne API'sini kurcalayamaz
assert.throws(() => { 'use strict'; H.addObject = () => 1; });
console.log('HUBSCENE_OK');
