/* Sayfa: Robot (ana sayfa). 3B görünüm + durum + robot bilgisi + olay günlüğü. Senaryo düğmeleri YOKTUR
 * (yeşile git / isimli pozlar kaldırıldı); hareket için Jog sayfası. Veri textContent ile yazılır. */
'use strict';

(() => {
  const R = { root: null, ui: null };
  const q = (id) => document.getElementById(id);

  function build(root) {
    const panels = (S.ui && S.ui.layout && S.ui.layout.panels) || ['status', 'robot', 'log'];
    const cards = {
      status: () => el('div', { class: 'card', id: 'p-status' }, [
        el('h2', { text: 'Durum' }), el('div', { class: 'status-big', id: 'st-big' }),
        el('div', { class: 'say', id: 'st-say' }), el('div', { class: 'say', id: 'st-last' }),
        el('div', { class: 'row' }, [el('button', { class: 'small', id: 'btn-cancel', type: 'button',
          text: 'Çalışan işi iptal et' })])]),
      robot: () => el('div', { class: 'card', id: 'p-robot' }, [
        el('h2', { text: 'Robot' }), el('dl', { class: 'kv', id: 'rb-kv' }), el('div', { class: 'joints', id: 'rb-joints' })]),
      log: () => el('div', { class: 'card', id: 'p-log' }, [el('h2', { text: 'Olay günlüğü' }), el('ul', { class: 'log', id: 'lg' })]),
    };
    const view = el('div', { id: 'robot-view' });
    const side = el('div', {}, panels.filter((p) => cards[p]).map((p) => cards[p]()));
    root.append(el('div', { class: 'page-grid two' }, [view, side]));
    const c = q('btn-cancel'); if (c) c.addEventListener('click', onCancel);
    return view;
  }

  async function onCancel() { try { await api('/api/scenarios/cancel', 'POST'); } catch (e) { toast(e.message, true); } }

  function renderStatus(s) {
    const run = s.runner, big = q('st-big');
    if (!big) return;
    let text = 'Hazır', cls = 'ok';
    if (s.estop.latched) { text = 'ACİL DUR'; cls = 'bad'; }
    else if (run.running) { text = `${run.label} (adım ${run.step}/${run.steps_total})`; cls = 'busy'; }
    big.textContent = text; big.className = 'status-big ' + cls;
    q('st-say').textContent = run.running ? run.say : '';
    const last = run.last;
    q('st-last').textContent = !run.running && last ? `Son: ${last.label}: ${last.message}` : '';
    const c = q('btn-cancel'); c.disabled = !run.running || !s.me.can_control; c.hidden = !run.running;
  }

  function renderRobot(s) {
    const kv = q('rb-kv');
    if (!kv || !s.robot) return;
    const r = s.robot;
    const STATE = { idle: 'hazır', busy: 'çalışıyor', estopped: 'ACİL DUR', error: 'hata (son komut)' };
    const rows = [['Arka uç', r.mode && r.mode !== r.name ? `${r.name} (${r.mode})` : r.name],
      ['Bağlantı', r.connected ? 'bağlı' : 'yok'],
      ['Robot durumu', STATE[r.state] || '—'],
      ['Hareket', r.moving === true ? 'hareket ediyor' : r.moving === false ? 'duruyor' : 'bilinmiyor'],
      ['Uç konum (m)', r.tool ? r.tool.map(fmt).join('  ') : '—']];
    if (s.bridge) {
      const b = s.bridge;
      rows.push(['ESP32 köprüsü', `${b.serial_connected ? 'seri bağlı' : 'seri YOK'}, ${b.client_connected ? 'istemci var' : 'istemci yok'} (${b.listen})`]);
    }
    if (r.current_job) rows.push(['Çalışan iş', `${r.current_job.id}: ${r.current_job.command}`]);
    if (r.last_error) rows.push(['Son hata', r.last_error.message]);
    if (r.safety && r.safety.zone) {
      const z = r.safety.zone.box;
      rows.push(['Güvenli kutu', ['x', 'y', 'z'].map((k) => `${k} ${z[k][0]}…${z[k][1]}`).join(', ')]);
    }
    kv.replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: String(v) })]));
    q('rb-joints').replaceChildren(...(r.joints || []).map((j) => el('div', { class: 'joint' }, [
      el('b', { text: j.deg == null ? '—' : `${j.deg.toFixed(1)}°` }), el('span', { text: j.name })])));
  }

  function renderLog(events) {
    const ul = q('lg'); if (!ul) return;
    ul.replaceChildren(...(events || S.events).slice(-40).reverse().map((e) =>
      el('li', { 'data-level': e.level }, [el('time', { text: hhmmss(e.ts) }), el('span', { text: e.message })])));
  }

  HubPages.register({
    id: 'robot', title: 'Robot', icon: '🤖', order: 10,
    mount(root) {
      R.root = root; R.view = build(root);
      renderLog(); HubPages.mountView(R.view);
    },
    onShow() { HubPages.mountView(R.view); },
    onState(s) { renderStatus(s); renderRobot(s); },
    onEvents(ev) { renderLog(ev); },
  });
})();
