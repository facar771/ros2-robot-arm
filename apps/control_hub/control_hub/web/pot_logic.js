/* Pot (potansiyometre) mantığı: SAF, DOM yok (node'da testlenir). Tezgâh pot paneli (bench.js) ve Parametreler
 * sayfasının pot kalibrasyonu (pages/params.js) AYNI kuralları kullanır.
 *
 * Çerçeve (firmware/POT_TELEMETRI.md): ham 10 bit ADC (0..1023); ham <= 8 ya da >= 1015 GEÇERSİZ (kablo/uç);
 * 0,5 sn'den eski örnek BAYAT; tüm kanallar birlikte ~15 Hz.
 *
 * Hub'da henüz pot uç noktası YOK (PR'da İSTEK: Robot_3 / Robot_10). Beklenen gövde (snap.pot YA DA GET /api/pot):
 *   { ok: true, hz: 15, channels: [ { ch: 1, raw: 512, valid: true, age_ms: 40 }, ... ] }
 * Bozuk/eksik gövde panelin çökmesine değil "veri yok" yazmasına yol açar. */
'use strict';

window.PotLogic = (() => {
  const INVALID_LOW = 8, INVALID_HIGH = 1015, ADC_MAX = 1023, STALE_MS = 500, STREAM_HZ = 15;
  const isInt = (v) => typeof v === 'number' && Number.isInteger(v);

  /** Ham değerin bölgesi: 'low' (<=8), 'high' (>=1015), 'ok'; sayı değilse/aralık dışıysa 'bad'. */
  function zone(raw) {
    if (!isInt(raw) || raw < 0 || raw > ADC_MAX) return 'bad';
    if (raw <= INVALID_LOW) return 'low';
    if (raw >= INVALID_HIGH) return 'high';
    return 'ok';
  }

  const ZONE_TEXT = {
    low: `ham ${INVALID_LOW} ve altı: GEÇERSİZ (kablo/uç)`,
    high: `ham ${INVALID_HIGH} ve üstü: GEÇERSİZ (kablo/uç)`,
    bad: 'ham değer anlamsız (0–1023 dışı)',
  };

  /** Kalibrasyon {raw_min, raw_max, angle_at_min_deg, angle_at_max_deg} ile doğrusal açı; eksik/bozuksa null.
   *  Uç dışı kırpılmaz: kullanıcı olduğu gibi görür (kalibrasyon uçlarının dışına çıkmış olabilir). */
  function angle(raw, cal) {
    if (!cal || !isInt(raw)) return null;
    const { raw_min: a, raw_max: b, angle_at_min_deg: p, angle_at_max_deg: q } = cal;
    if (![a, b, p, q].every((v) => typeof v === 'number' && Number.isFinite(v)) || b <= a) return null;
    return p + ((raw - a) / (b - a)) * (q - p);
  }

  /** Örnek tazeliği: 'fresh' | 'stale' (> 0,5 sn) | 'unknown' (yaş yok). */
  function freshness(ageMs) {
    if (typeof ageMs !== 'number' || !Number.isFinite(ageMs) || ageMs < 0) return 'unknown';
    return ageMs > STALE_MS ? 'stale' : 'fresh';
  }

  /** Sunucu gövdesi -> {ok, hz, rows:[{ch, raw, ageMs, zone, fresh, valid}]} (kanala göre sıralı). Bozuk gövde: ok:false. */
  function normalize(body) {
    if (!body || typeof body !== 'object' || !Array.isArray(body.channels)) {
      return { ok: false, hz: null, rows: [], reason: 'pot verisi yok' };
    }
    const rows = [];
    for (const c of body.channels) {
      if (!c || !isInt(c.ch) || c.ch < 1 || c.ch > 255) continue;               // anlamsız kanal numarası
      const z = zone(c.raw);
      rows.push({ ch: c.ch, raw: z === 'bad' ? null : c.raw, ageMs: typeof c.age_ms === 'number' ? c.age_ms : null,
                  zone: z, fresh: freshness(c.age_ms), valid: c.valid !== false && z === 'ok' });
    }
    rows.sort((x, y) => x.ch - y.ch);
    const hz = typeof body.hz === 'number' && Number.isFinite(body.hz) ? body.hz : null;
    return { ok: body.ok !== false, hz, rows };
  }

  /** Bir satırın kullanıcıya gösterilecek durumu: {level:'ok'|'warn'|'bad', text}. */
  function status(row) {
    if (!row) return { level: 'bad', text: 'veri yok' };
    if (row.zone !== 'ok') return { level: 'bad', text: ZONE_TEXT[row.zone] };
    if (row.fresh === 'stale') return { level: 'warn', text: `bayat (${Math.round(row.ageMs)} ms)` };
    if (row.valid === false) return { level: 'bad', text: 'sunucu: geçersiz örnek' };
    return { level: 'ok', text: row.fresh === 'fresh' ? `taze (${Math.round(row.ageMs)} ms)` : 'taze (yaş bilinmiyor)' };
  }

  return { INVALID_LOW, INVALID_HIGH, ADC_MAX, STALE_MS, STREAM_HZ, zone, angle, freshness, normalize, status };
})();
