/* Parametreler sayfasının SAF mantığı (P4): sayı ayrıştırma, değişiklik kümesi, hata eşleme, kaydetme kapısı,
 * güvenli kutu önizleme geometrisi. DOM yok; node'da testlenir (tests/js/params_logic_check.js).
 * Doğrulamanın HAKEMİ sunucudur (profile_rules + robot_core); burası yalnız yazım hatasını yakalar ve sunucuya
 * yalnız DEĞİŞEN alanları gönderir. */
'use strict';

window.ParamsLogic = (() => {
  const CORE = ['min_deg', 'max_deg', 'vel_dps', 'acc_dps2', 'home_deg'];          // eksen tablosunun ana sütunları
  const CORE_LABEL = { min_deg: 'En küçük °', max_deg: 'En büyük °', vel_dps: 'Hız °/s', acc_dps2: 'İvme °/s²', home_deg: 'Home °' };

  /** Metin -> {value} (boş = null: "boşalt") ya da {error}. Virgül ondalık kabul edilir; 1e3 / 0x10 / 12abc reddedilir. */
  function parse(text, kind) {
    const t = String(text === undefined || text === null ? '' : text).trim().replace(',', '.');
    if (t === '') return { value: null };
    if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return { error: 'sayı olmalı' };
    const v = Number(t);
    if (!Number.isFinite(v)) return { error: 'sayı olmalı' };
    if (kind === 'int' && !Number.isInteger(v)) return { error: 'tam sayı olmalı' };
    if (kind === 'sign' && v !== 1 && v !== -1) return { error: '+1 ya da -1 olmalı' };
    return { value: v };
  }

  /** Gösterim duyarlılığı içinde eşit mi (sunucudaki same_value ile AYNI kural). */
  function same(f, a, b) {
    if (a === null || a === undefined || b === null || b === undefined) return (a ?? null) === (b ?? null);
    if (f.kind !== 'float') return Math.trunc(a) === Math.trunc(b);
    return Math.abs(a - b) < 0.5 * 10 ** -f.decimals;
  }

  /** Gösterilecek metin: yuvarlak, gereksiz sıfırsız. */
  function fmt(v, decimals) {
    if (v === null || v === undefined || !Number.isFinite(v)) return '';
    return String(Number(v.toFixed(decimals)));
  }

  /** Değişen alanlar: {anahtar: sayı | null}. `cur`: anahtar -> sayı|null; metni bozuk alanlar `bad` kümesinde, gönderilmez. */
  function changed(fields, cur, bad) {
    const out = {};
    for (const f of fields) {
      if (bad && bad.has(f.key) || !(f.key in cur)) continue;
      if (!same(f, f.value, cur[f.key])) out[f.key] = cur[f.key];
    }
    return out;
  }

  /** Sunucu sorunları -> {alan: [{severity, message}]}; sorunlar bir alana (ya da satır/gruba) bağlanır. */
  function byField(issues) {
    const m = {};
    for (const i of issues || []) (m[i.field] = m[i.field] || []).push({ severity: i.severity, message: i.message });
    return m;
  }

  /** Bir alan anahtarı için HATA var mı (uyarı değil)? Satır/grup anahtarları öneklerle eşlenir (örn. 'safety'). */
  const hasError = (map, key) => (map[key] || []).some((i) => i.severity === 'error');

  /** "Taslak kaydet" kapısı: değişiklik var, hiç hata yok, metin hatası yok, yetki var, meşgul değil. */
  function saveGate({ res, edits, badCount, canControl, busy, pending }) {
    if (!canControl) return { ok: false, why: 'Kilitli/izleyici kipi: kaydedilemez' };
    if (busy) return { ok: false, why: 'Kaydediliyor…' };
    if (pending) return { ok: false, why: 'Doğrulanıyor…' };
    if (badCount > 0) return { ok: false, why: 'Sayı olmayan alan var' };
    if (!Object.keys(edits).length) return { ok: false, why: 'Değişiklik yok' };
    if (!res) return { ok: false, why: 'Doğrulama sonucu yok' };
    if (!res.ok) return { ok: false, why: `${(res.issues || []).filter((i) => i.severity === 'error').length} hata var: düzeltmeden kaydedilmez` };
    return { ok: true, why: 'Taslak veri klasörüne yazılır; robota UYGULANMAZ' };
  }

  /** Güvenli kutu önizlemesi: `box` mm {x:[lo,hi], y:[..], z:[..]}, `tcp` mm [x,y,z] | null, görünüm 'top' (XY) | 'side' (XZ).
   *  Çıktı: piksel dikdörtgen + nokta; ölçek iki görünümde ortak değil, her görünüm kendi sınırına oturur. */
  function project(box, tcp, view, w, h, pad) {
    const [a, b] = view === 'top' ? ['x', 'y'] : ['x', 'z'];
    const pts = [box[a], box[b]];
    if (tcp) pts[0] = [Math.min(pts[0][0], tcp[0]), Math.max(pts[0][1], tcp[0])];
    if (tcp) pts[1] = [Math.min(pts[1][0], tcp[view === 'top' ? 1 : 2]), Math.max(pts[1][1], tcp[view === 'top' ? 1 : 2])];
    const sx = (w - 2 * pad) / Math.max(1e-6, pts[0][1] - pts[0][0]);
    const sy = (h - 2 * pad) / Math.max(1e-6, pts[1][1] - pts[1][0]);
    const s = Math.min(sx, sy);
    const X = (v) => pad + (v - pts[0][0]) * s;
    const Y = (v) => h - pad - (v - pts[1][0]) * s;                                 // yukarı = artı
    const rect = { x: X(box[a][0]), y: Y(box[b][1]), w: (box[a][1] - box[a][0]) * s, h: (box[b][1] - box[b][0]) * s };
    const dot = tcp ? { x: X(tcp[0]), y: Y(tcp[view === 'top' ? 1 : 2]) } : null;
    return { rect, dot, scale: s };
  }

  /** Kutu değerleri (anahtar -> mm) -> {x:[lo,hi], ...}; eksik/NaN varsa null. */
  function boxOf(values) {
    const box = {};
    for (const ax of ['x', 'y', 'z']) {
      const lo = values[`safety.${ax}_min_mm`], hi = values[`safety.${ax}_max_mm`];
      if (![lo, hi].every((v) => typeof v === 'number' && Number.isFinite(v)) || hi <= lo) return null;
      box[ax] = [lo, hi];
    }
    return box;
  }

  /** Yakalanan canlı ham değeri (pot) alana yazılabilir mi? Yalnız geçerli bölgedeki, taze örnek. */
  function capturable(row) {
    return !!(row && row.zone === 'ok' && row.fresh !== 'stale' && Number.isInteger(row.raw));
  }

  return { CORE, CORE_LABEL, parse, same, fmt, changed, byField, hasError, saveGate, project, boxOf, capturable };
})();
