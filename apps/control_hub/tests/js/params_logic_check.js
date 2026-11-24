/* P4 Parametreler saf mantığı (params_form.js): ayrıştırma, değişiklik kümesi, hata eşleme, kaydetme kapısı, önizleme.
 * pytest çağırır (tests/test_params_page.py).  node params_logic_check.js <web dizini> */
'use strict';
const assert = require('assert');
const path = require('path');
global.window = global;
require(path.join(path.resolve(process.argv[2]), 'params_form.js'));
const L = ParamsLogic;

/* parse */
for (const [t, kind, want] of [['12', 'float', 12], ['-0,5', 'float', -0.5], ['  3.25 ', 'float', 3.25], ['.5', 'float', 0.5],
  ['+7', 'float', 7], ['', 'float', null], ['  ', 'float', null], ['1', 'sign', 1], ['-1', 'sign', -1], ['16', 'int', 16]]) {
  assert.deepStrictEqual(L.parse(t, kind), { value: want }, `parse(${JSON.stringify(t)})`);
}
for (const [t, kind] of [['abc', 'float'], ['12abc', 'float'], ['1e3', 'float'], ['0x10', 'float'], ['1.2.3', 'float'], ['--1', 'float'],
  ['Infinity', 'float'], ['NaN', 'float'], ['1.5', 'int'], ['0', 'sign'], ['2', 'sign'], ['1,5,5', 'float']]) {
  assert.ok(L.parse(t, kind).error, `parse(${JSON.stringify(t)}, ${kind}) hata olmalı`);
}

/* same: gösterim duyarlılığı */
const f3 = { key: 'a', kind: 'float', decimals: 3, value: 2.8647889 };
assert.ok(L.same(f3, 2.8647889, 2.865));                     // gösterilen yuvarlak değer = değişiklik değil
assert.ok(!L.same(f3, 2.8647889, 2.866));
assert.ok(L.same({ kind: 'int', decimals: 0 }, 16, 16) && !L.same({ kind: 'int', decimals: 0 }, 16, 17));
assert.ok(L.same(f3, null, null) && !L.same(f3, null, 1) && !L.same(f3, 1, null));
assert.strictEqual(L.fmt(2.8647889, 3), '2.865'); assert.strictEqual(L.fmt(90, 3), '90'); assert.strictEqual(L.fmt(null, 3), '');
assert.strictEqual(L.fmt(NaN, 3), '');

/* changed: yalnız değişenler, bozuk metinli alan gönderilmez */
const fields = [{ key: 'a', kind: 'float', decimals: 3, value: 1 }, { key: 'b', kind: 'float', decimals: 3, value: 2 },
  { key: 'c', kind: 'int', decimals: 0, value: 16 }, { key: 'p', kind: 'int', decimals: 0, value: null }];
assert.deepStrictEqual(L.changed(fields, { a: 1, b: 2.0004, c: 16, p: null }, new Set()), {});
assert.deepStrictEqual(L.changed(fields, { a: 5, b: 2, c: 32, p: 7 }, new Set()), { a: 5, c: 32, p: 7 });
assert.deepStrictEqual(L.changed(fields, { a: 5, b: 9 }, new Set(['b'])), { a: 5 });
assert.deepStrictEqual(L.changed(fields, { p: null, a: null }, new Set()), { a: null });   // boşaltma gönderilir (sunucu reddeder)

/* hata eşleme */
const m = L.byField([{ field: 'x', severity: 'error', message: 'e1' }, { field: 'x', severity: 'warning', message: 'w' }, { field: 'y', severity: 'warning', message: 'w2' }]);
assert.strictEqual(m.x.length, 2); assert.ok(L.hasError(m, 'x')); assert.ok(!L.hasError(m, 'y')); assert.ok(!L.hasError(m, 'z'));
assert.deepStrictEqual(L.byField(null), {});

/* kaydetme kapısı */
const ok = { ok: true, issues: [{ severity: 'warning' }] }, base = { res: ok, edits: { a: 1 }, badCount: 0, canControl: true, busy: false, pending: false };
assert.ok(L.saveGate(base).ok);
assert.ok(!L.saveGate({ ...base, canControl: false }).ok);               // kilitli/izleyici
assert.ok(!L.saveGate({ ...base, busy: true }).ok);
assert.ok(!L.saveGate({ ...base, pending: true }).ok);                   // doğrulama sürerken kaydetme yok
assert.ok(!L.saveGate({ ...base, badCount: 1 }).ok);
assert.ok(!L.saveGate({ ...base, edits: {} }).ok);                       // değişiklik yok
assert.ok(!L.saveGate({ ...base, res: null }).ok);
const gateBad = L.saveGate({ ...base, res: { ok: false, issues: [{ severity: 'error' }, { severity: 'warning' }, { severity: 'error' }] } });
assert.ok(!gateBad.ok && gateBad.why.startsWith('2 hata'));              // yalnız HATA sayılır, uyarı değil

/* güvenli kutu + önizleme */
const vals = { 'safety.x_min_mm': 60, 'safety.x_max_mm': 220, 'safety.y_min_mm': -100, 'safety.y_max_mm': 100, 'safety.z_min_mm': 20, 'safety.z_max_mm': 220 };
const box = L.boxOf(vals);
assert.deepStrictEqual(box, { x: [60, 220], y: [-100, 100], z: [20, 220] });
assert.strictEqual(L.boxOf({ ...vals, 'safety.x_max_mm': 60 }), null);   // min >= max: çizilmez
assert.strictEqual(L.boxOf({ ...vals, 'safety.z_min_mm': NaN }), null);
assert.strictEqual(L.boxOf({}), null);
const pr = L.project(box, [140, 0, 120], 'top', 240, 170, 18);
assert.ok(pr.rect.w > 0 && pr.rect.h > 0 && pr.rect.x >= 18 - 1e-9 && pr.rect.y >= 18 - 1e-9);
assert.ok(pr.dot.x > pr.rect.x && pr.dot.x < pr.rect.x + pr.rect.w && pr.dot.y > pr.rect.y && pr.dot.y < pr.rect.y + pr.rect.h, 'içerideki nokta kutunun içinde');
const out = L.project(box, [700, 0, 900], 'side', 240, 170, 18);
assert.ok(out.dot.x > out.rect.x + out.rect.w && out.dot.y < out.rect.y, 'dışarıdaki nokta kutunun dışında (sağ-üst)');
assert.ok(out.dot.x <= 240 && out.dot.y >= 0, 'nokta tuvalin içinde kalır');
assert.strictEqual(L.project(box, null, 'top', 240, 170, 18).dot, null);

/* yakalama: yalnız geçerli bölgede ve taze örnek */
assert.ok(L.capturable({ zone: 'ok', fresh: 'fresh', raw: 500 }));
assert.ok(L.capturable({ zone: 'ok', fresh: 'unknown', raw: 500 }));
assert.ok(!L.capturable({ zone: 'low', fresh: 'fresh', raw: 5 }));
assert.ok(!L.capturable({ zone: 'high', fresh: 'fresh', raw: 1020 }));
assert.ok(!L.capturable({ zone: 'ok', fresh: 'stale', raw: 500 }));
assert.ok(!L.capturable(null) && !L.capturable(undefined));
console.log('PARAMS_LOGIC_JS_OK');
