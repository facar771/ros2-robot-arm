/* Dokun-git hedeflerini scene_logic.js'ten (SceneLogic.approachTarget) hesaplayıp JSON basar: pytest bunları GERÇEK SİM
 * arka ucuna (/api/move/check + /api/move) gönderir. Böylece JS hedef matematiği gerçek kinematikle sınanır.
 *   node approach_targets.js <web dizini> '<[{point, normal, d, opts}, …]>' */
'use strict';
const path = require('path');
global.window = global;
require(path.join(path.resolve(process.argv[2]), 'scene_logic.js'));
const cases = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(cases.map((c) => SceneLogic.approachTarget(c.point, c.normal, c.d, c.opts || {}))));
