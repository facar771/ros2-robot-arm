/* Yapay zekâ ajanı paneli (app.js'ten sonra yüklenir). Veri textContent ile yazılır (innerHTML yok).
 * Anahtar: yalnızca yerel bağlantıda (masaüstü/127.0.0.1) giriş kutusu görünür; sunucu anahtarı HİÇ geri
 * göndermez (yalnızca "ayarlı/hatırlanıyor" bayrakları). Gönderdikten sonra kutu temizlenir. */
'use strict';

window.AgentPanel = (() => {
  const A = { st: null, csrf: '', seq: 0, msgs: [], sig: '', mounted: false, tick: null, busy: false };
  const q = (id) => document.getElementById(id);

  async function aapi(path, method = 'GET', body) {
    const opt = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') {
      opt.headers['Content-Type'] = 'application/json';
      opt.headers['X-CSRF-Token'] = A.csrf;
      opt.body = JSON.stringify(body || {});
    }
    const res = await fetch(path, opt);
    let data = null;
    try { data = await res.json(); } catch (e) { /* gövdesiz */ }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  function mount() {
    if (A.mounted) return;
    aapi('/api/agent/status').then((st) => {
      if (!st.enabled) return;                  // ajan yok/kapalı: panel hiç çizilmez
      A.csrf = st.csrf; A.st = st; A.mounted = true;
      build(); refresh(true);
      A.tick = setInterval(countdown, 1000);
    }).catch(() => {});
  }

  function build() {
    const host = q('panels');
    const cfg = el('details', { class: 'ag-cfg', id: 'ag-cfg' }, [
      el('summary', { text: 'Anahtar ve ayarlar (yalnızca bu bilgisayar)' }),
      el('div', { class: 'ag-form', id: 'ag-form' })]);
    host.append(el('div', { class: 'card', id: 'p-agent' }, [
      el('h2', { text: 'Yapay zekâ ajanı' }),
      el('div', { class: 'say', id: 'ag-meta' }),
      el('ul', { class: 'ag-chat', id: 'ag-chat', 'aria-live': 'polite' }),
      el('div', { id: 'ag-appr' }),
      el('form', { class: 'ag-send', id: 'ag-send', autocomplete: 'off' }, [
        el('input', { id: 'ag-text', type: 'text', maxLength: 2000, autocomplete: 'off',
                      placeholder: 'Örn. yeşil nesnenin üstüne git', 'aria-label': 'Ajana mesaj' }),
        el('button', { class: 'small primary', id: 'ag-go', type: 'submit', text: 'Gönder' }),
        el('button', { class: 'small', id: 'ag-stop', type: 'button', text: 'Ajanı durdur' })]),
      cfg]));
    q('ag-send').addEventListener('submit', onSend);
    q('ag-stop').addEventListener('click', onCancel);
  }

  function field(label, node) { return el('label', { class: 'ag-f' }, [el('span', { text: label }), node]); }

  function buildForm(st) {
    const form = q('ag-form'); form.replaceChildren();
    if (!st.can_configure) {
      form.append(el('p', { class: 'muted', text: 'Anahtar ve ayarlar yalnızca masaüstü pencereden ya da bu ' +
        'bilgisayarda http://127.0.0.1 üzerinden girilir; telefondan girilemez.' }));
      return;
    }
    const sel = el('select', { id: 'ag-prov' }, st.providers.map((p) => el('option', { value: p, text: p })));
    sel.value = st.provider;
    const num = (id, v, min, max) => el('input', { id, type: 'number', value: String(v), min, max });
    form.append(
      field('Sağlayıcı', sel),
      field('Model', el('input', { id: 'ag-model', type: 'text', value: st.model, maxLength: 100 })),
      field('OpenAI uç adresi (https)', el('input', { id: 'ag-url', type: 'url', value: st.base_url })),
      field('Günlük istek sınırı', num('ag-lim', st.limits.daily_limit, 1, 100000)),
      field('Tur başına hareket', num('ag-mot', st.limits.per_turn_motions, 0, 20)),
      el('label', { class: 'ag-f row' }, [el('input', { id: 'ag-dry', type: 'checkbox', checked: st.dry_run }),
        el('span', { text: 'Kuru çalıştırma (hareket robota gitmez)' })]),
      el('button', { class: 'small', id: 'ag-save', type: 'button', text: 'Ayarları kaydet' }),
      field('API anahtarı', el('input', { id: 'ag-key', type: 'password', autocomplete: 'off',
                                          spellcheck: false, placeholder: 'sk-… (yalnızca bellekte tutulur)' })),
      el('label', { class: 'ag-f row' }, [el('input', { id: 'ag-rem', type: 'checkbox' }),
        el('span', { text: 'Beni hatırla (Windows Credential Manager; ayar dosyasına yazılmaz)' })]),
      el('div', { class: 'row' }, [
        el('button', { class: 'small', id: 'ag-setkey', type: 'button', text: 'Anahtarı ayarla' }),
        el('button', { class: 'small', id: 'ag-clrkey', type: 'button', text: 'Anahtarı sil' })]),
      el('div', { class: 'say', id: 'ag-keystate' }));
    q('ag-save').addEventListener('click', onSave);
    q('ag-setkey').addEventListener('click', onSetKey);
    q('ag-clrkey').addEventListener('click', onClearKey);
  }

  function keyText(st) {
    return Object.entries(st.keys).map(([p, v]) =>
      `${p}: ${v.set ? (v.remembered ? 'ayarlı (hatırlanıyor)' : 'ayarlı (yalnızca bellekte)') : 'yok'}`).join(' · ');
  }

  function renderMeta() {
    const st = A.st; if (!st) return;
    const bits = [`${st.provider}${st.model ? ' / ' + st.model : ''}`,
      `bugün ${st.limits.used_today}/${st.limits.daily_limit} istek`,
      `tur başına en çok ${st.limits.per_turn_motions} hareket`];
    if (st.dry_run) bits.unshift('KURU ÇALIŞTIRMA');
    q('ag-meta').textContent = bits.join(' · ') + ' — ' + keyText(st);
    const ks = q('ag-keystate'); if (ks) ks.textContent = keyText(st);
  }

  function renderChat() {
    const ul = q('ag-chat'); ul.replaceChildren();
    for (const m of A.msgs) {
      const who = { user: 'Sen', assistant: 'Ajan', tool: 'Araç', system: 'Sistem' }[m.role] || m.role;
      ul.append(el('li', { class: 'ag-m ' + m.role + (m.role === 'tool' && m.ok === false ? ' bad' : '') }, [
        el('b', { text: who }), el('span', { text: m.text })]));
    }
    ul.scrollTop = ul.scrollHeight;
  }

  function renderApprovals() {
    const box = q('ag-appr'); box.replaceChildren();
    const top = A.st.approvals.length ? A.st.approvals[0].id : null;
    if (top && top !== A.lastApproval) {            // yeni onay: telefonda panel ekranın altında kalmasın
      q('p-agent').scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast('Ajan hareket için onay bekliyor');
    }
    A.lastApproval = top;
    for (const ap of A.st.approvals) {
      const left = el('span', { class: 'ag-left', 'data-left': String(ap.expires_in_s), text: `${Math.ceil(ap.expires_in_s)} sn` });
      const ok = el('button', { class: 'small primary', type: 'button', text: 'Onayla' });
      const no = el('button', { class: 'small', type: 'button', text: 'Reddet' });
      ok.addEventListener('click', () => decide(ap.id, true));
      no.addEventListener('click', () => decide(ap.id, false));
      box.append(el('div', { class: 'ag-approval' }, [
        el('b', { text: 'Robot hareket edecek — onay?' }), el('div', { text: ap.description }),
        el('div', { class: 'muted' }, ['Süre dolarsa REDDEDİLİR · ', left]), el('div', { class: 'row' }, [ok, no])]));
    }
  }

  function countdown() {
    for (const n of document.querySelectorAll('.ag-left')) {
      const v = Math.max(0, parseFloat(n.dataset.left) - 1); n.dataset.left = String(v);
      n.textContent = `${Math.ceil(v)} sn`;
    }
  }

  async function refresh(force) {
    if (!A.mounted) return;
    try {
      const st = await aapi('/api/agent/status');
      A.csrf = st.csrf;
      const formSig = JSON.stringify([st.can_configure, st.provider, st.model, st.base_url, st.dry_run,
                                      st.limits.daily_limit, st.limits.per_turn_motions]);
      const rebuild = force || formSig !== A.formSig;
      A.st = st; A.formSig = formSig;
      if (rebuild && !(q('ag-key') && q('ag-key').value)) buildForm(st);
      const chat = await aapi('/api/agent/chat?since=' + (force ? 0 : A.seq));
      if (force) A.msgs = [];
      for (const m of chat.messages) { A.msgs.push(m); A.seq = Math.max(A.seq, m.id); }
      renderMeta(); renderChat(); renderApprovals();
      q('ag-go').disabled = !st.run || st.run.active || !(S.snap && S.snap.me.can_control);
      q('ag-stop').disabled = !st.run.active;
    } catch (e) { /* oturum süresi dolmuş olabilir: app.js yönetir */ }
  }

  function onState(s) {
    if (!A.mounted || !s.agent) return;
    const sig = JSON.stringify([s.agent.chat_seq, s.agent.active, s.agent.pending_approvals, s.estop.latched]);
    if (sig !== A.sig) { A.sig = sig; refresh(false); }
  }

  async function onSend(ev) {
    ev.preventDefault();
    const inp = q('ag-text'); const text = inp.value.trim(); if (!text) return;
    try { await aapi('/api/agent/message', 'POST', { text }); inp.value = ''; refresh(false); }
    catch (e) { toast(e.message, true); }
  }
  async function decide(id, approve) {
    try { await aapi('/api/agent/approvals/' + encodeURIComponent(id), 'POST', { approve }); }
    catch (e) { toast(e.message, true); }
    refresh(false);
  }
  async function onCancel() {
    try { await aapi('/api/agent/cancel', 'POST', {}); } catch (e) { toast(e.message, true); }
    refresh(false);
  }
  async function onSave() {
    const v = (id) => q(id).value;
    try {
      await aapi('/api/agent/config', 'POST', { provider: v('ag-prov'), model: v('ag-model'),
        base_url: v('ag-url'), daily_limit: parseInt(v('ag-lim'), 10), max_motions: parseInt(v('ag-mot'), 10),
        dry_run: q('ag-dry').checked });
      toast('Ajan ayarları kaydedildi');
    } catch (e) { toast(e.message, true); }
    refresh(true);
  }
  async function onSetKey() {
    const inp = q('ag-key'); const key = inp.value; inp.value = '';          // kutuyu hemen boşalt
    if (!key) return;
    try {
      const r = await aapi('/api/agent/key', 'POST', { provider: q('ag-prov').value, key, remember: q('ag-rem').checked });
      toast(r.warning || 'Anahtar ayarlandı', !!r.warning);
    } catch (e) { toast(e.message, true); }
    refresh(true);
  }
  async function onClearKey() {
    try { await aapi('/api/agent/key/clear', 'POST', { provider: q('ag-prov').value, forget: true }); }
    catch (e) { toast(e.message, true); }
    refresh(true);
  }

  return { mount, onState };
})();
