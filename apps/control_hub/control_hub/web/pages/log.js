/* Sayfa: Günlük (P4). Hub olay günlüğünün son 500 kaydı: seviye/kaynak süzgeci, arama, yeni olaylar ÜSTTE, CSV indir.
 * SIZINTI KURALI: ekrana yalnız sunucunun MASKELEDİĞİ satırlar (GET /api/log) yazılır; PIN/anahtar/yol hiç görünmez.
 * S.events (WebSocket, ham) ekranda GÖSTERİLMEZ; yalnız "yeni olay var, yenile" tetikleyicisidir (onEvents).
 * Veri textContent ile yazılır (innerHTML yok). Saf mantık window.LogLogic'te (node testi: tests/js/log_logic_check.js). */
'use strict';

(() => {
  const LEVELS = ['info', 'warning', 'error', 'critical'];
  const LEVEL_LABEL = { info: 'Bilgi', warning: 'Uyarı', error: 'Hata', critical: 'Kritik' };
  const FLOOR_LABEL = { info: 'Hepsi', warning: 'Uyarı ve üstü', error: 'Hata ve üstü', critical: 'Yalnız kritik' };
  const MAX_ROWS = 500;
  const MAX_Q = 200;

  /* ---------- saf mantık ---------- */
  const LogLogic = {
    LEVELS, LEVEL_LABEL, FLOOR_LABEL, MAX_ROWS, MAX_Q,
    /* Süzgeç -> sorgu dizesi. Varsayılanlar (en düşük seviye "info", boş kaynak/arama) gönderilmez. */
    query(f) {
      const p = new URLSearchParams();
      if (f.level && f.level !== 'info' && LEVELS.includes(f.level)) p.set('level', f.level);
      if (f.source) p.set('source', String(f.source).slice(0, 64));
      const q = String(f.q || '').trim().slice(0, MAX_Q);
      if (q) p.set('q', q);
      p.set('limit', String(MAX_ROWS));
      return p.toString();
    },
    url(f, csv = false) { return `${csv ? '/api/log.csv' : '/api/log'}?${LogLogic.query(f)}`; },
    /* WebSocket olay listesindeki en büyük sıra no (değişince günlük yenilenir). */
    latestSeq(events) { return (events || []).reduce((m, e) => Math.max(m, (e && e.seq) | 0), 0); },
    levelOf(level) { return LEVELS.includes(level) ? level : 'info'; },
    summary(res) {
      if (!res) return '';
      const cap = res.capacity || MAX_ROWS;
      const tail = res.total >= cap ? ` · bellekte son ${cap} olay tutulur` : '';
      return `${res.shown} / ${res.total} olay gösteriliyor, en yeni üstte${tail}`;
    },
    /* Sunucudan gelen satırı çizilecek hücre metinlerine çevirir (hepsi düz metin). */
    cells(r) {
      return { time: String(r.time || ''), level: LogLogic.levelOf(r.level), source: String(r.source || ''),
               message: String(r.message || ''), detail: String(r.detail || '') };
    },
  };
  window.LogLogic = LogLogic;

  /* ---------- arayüz ---------- */
  const L = { f: { level: 'info', source: '', q: '' }, ui: {}, seq: 0, seen: -1, dirty: true, visible: false, mounted: false,
              timer: null, sources: [] };

  function field(text, control) {
    return el('label', { class: 'lg-field' }, [el('span', { class: 'muted', text }), control]);
  }

  function option(value, text) { return el('option', { value, text }); }

  function build(root) {
    const u = L.ui;
    u.level = el('select', { class: 'lg-in', 'data-role': 'level' }, LEVELS.map((v) => option(v, FLOOR_LABEL[v])));
    u.source = el('select', { class: 'lg-in', 'data-role': 'source' }, [option('', 'Tümü')]);
    u.q = el('input', { type: 'search', class: 'lg-in', maxLength: MAX_Q, autocomplete: 'off', spellcheck: false,
                        placeholder: 'mesaj, kaynak, ayrıntı…', 'data-role': 'q' });
    u.q.setAttribute('aria-label', 'Günlükte ara');
    u.refresh = el('button', { type: 'button', class: 'lg-btn', text: 'Yenile', 'data-role': 'refresh' });
    u.csv = el('a', { class: 'lg-btn', text: 'CSV indir', 'data-role': 'csv', href: LogLogic.url(L.f, true), rel: 'noopener' });
    u.count = el('div', { class: 'lg-count muted' });
    u.count.setAttribute('role', 'status');
    u.count.setAttribute('aria-live', 'polite');
    u.err = el('div', { class: 'lg-err', hidden: true });
    u.err.setAttribute('role', 'alert');
    u.body = el('tbody', {});
    u.empty = el('div', { class: 'lg-empty muted', hidden: true, text: 'Eşleşen olay yok.' });
    const head = ['Zaman', 'Seviye', 'Kaynak', 'Mesaj', 'Ayrıntı'].map((h) => el('th', { text: h }));

    u.level.addEventListener('change', () => { L.f.level = u.level.value; changed(0); });
    u.source.addEventListener('change', () => { L.f.source = u.source.value; changed(0); });
    u.q.addEventListener('input', () => { L.f.q = u.q.value; changed(250); });
    u.refresh.addEventListener('click', () => refresh());

    root.append(
      el('section', { class: 'card lg-card' }, [
        el('h2', { text: 'Günlük' }),
        el('p', { class: 'lg-note', text: 'Hub olay günlüğü. Gösterilen metinde PIN, anahtar ve dosya yolları gizlenir ([GİZLİ], [yol]).' }),
        el('div', { class: 'lg-bar' }, [field('En düşük seviye', u.level), field('Kaynak', u.source), field('Ara', u.q), u.refresh, u.csv]),
        u.count, u.err,
        el('div', { class: 'lg-wrap' }, [el('table', { class: 'lg-tbl' }, [el('thead', {}, [el('tr', {}, head)]), u.body])]),
        u.empty,
      ]));
  }

  function rowEl(r) {
    const c = LogLogic.cells(r);
    const names = ['Zaman', 'Seviye', 'Kaynak', 'Mesaj', 'Ayrıntı'];
    const tds = [
      el('td', { class: 'lg-time', text: c.time }),
      el('td', {}, [el('span', { class: `lg-lvl ${c.level}`, text: LEVEL_LABEL[c.level] })]),
      el('td', { class: 'lg-src', text: c.source }),
      el('td', { class: 'lg-msg', text: c.message }),
      el('td', { class: 'lg-det', text: c.detail }),
    ];
    tds.forEach((td, i) => td.setAttribute('data-label', names[i]));
    return el('tr', { class: `lg-row ${c.level}` }, tds);
  }

  function fillSources(list) {
    const have = L.sources.join('|'), now = (list || []).join('|');
    if (have === now) return;
    L.sources = (list || []).slice();
    const keep = L.f.source;
    L.ui.source.replaceChildren(option('', 'Tümü'), ...L.sources.map((s) => option(s, s)));
    L.ui.source.value = L.sources.includes(keep) ? keep : '';
    L.f.source = L.ui.source.value;
  }

  function render(res) {
    const u = L.ui;
    fillSources(res.sources);
    u.csv.href = LogLogic.url(L.f, true);
    u.body.replaceChildren(...res.events.map(rowEl));
    u.empty.hidden = res.events.length > 0;
    u.count.textContent = LogLogic.summary(res);
    u.err.hidden = true;
  }

  function fail(e) {
    L.ui.err.hidden = false;
    L.ui.err.textContent = `Günlük okunamadı: ${(e && e.message) || 'bilinmeyen hata'}`;
  }

  async function refresh() {
    if (!L.mounted) return;
    clearTimeout(L.timer);
    const mine = ++L.seq;
    L.dirty = false;
    L.ui.csv.href = LogLogic.url(L.f, true);
    try {
      const res = await api(LogLogic.url(L.f));
      if (mine === L.seq) render(res);                               // eski/yarışan yanıt yazılmaz
    } catch (e) {
      if (mine === L.seq) { L.dirty = true; fail(e); }
    }
  }

  function changed(ms) {
    clearTimeout(L.timer);
    L.ui.csv.href = LogLogic.url(L.f, true);
    L.timer = setTimeout(refresh, ms);
  }

  HubPages.register({
    id: 'log', title: 'Günlük', icon: '📜', order: 60,
    styles: ['/pages/log.css'],
    mount(root) { build(root); L.mounted = true; return refresh(); },
    onShow() { L.visible = true; if (L.dirty) refresh(); },
    onHide() { L.visible = false; clearTimeout(L.timer); },
    /* Ham olaylar ekrana yazılmaz; yalnız "yeni olay var" işareti. Görünürken yenile, değilse işaretle. */
    onEvents(events) {
      const s = LogLogic.latestSeq(events);
      if (s === L.seen) return;
      L.seen = s;
      if (!L.mounted) return;
      if (L.visible) changed(400); else L.dirty = true;
    },
  });
})();
