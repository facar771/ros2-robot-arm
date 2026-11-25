/* Yapay zekâ ajanı paneli (app.js'ten sonra yüklenir). Veri textContent ile yazılır (innerHTML yok).
 * Anahtar: yalnızca yerel bağlantıda (masaüstü/127.0.0.1) giriş kutusu görünür; sunucu anahtarı HİÇ geri
 * göndermez (yalnızca "ayarlı/hatırlanıyor" bayrakları). Gönderdikten sonra kutu temizlenir.
 * R9: kararlar (metinler, imzalar, gövdeler, ne zaman yenilenir) saf agent_logic.js'tedir; burası yalnız DOM çizer + fetch eder. */
'use strict';

window.AgentPanel = (() => {
  const L = window.AgentLogic;                     // sayfa kaydı agent_logic.js'i bundan ÖNCE yükler (pages/agent.js scripts)
  const A = { st: null, csrf: '', seq: 0, msgs: [], sig: '', mounted: false, tick: null, busy: false };
  const q = (id) => document.getElementById(id);

  async function aapi(path, method = 'GET', body) {
    const res = await fetch(path, L.requestOptions(method, body, A.csrf));
    let data = null;
    try { data = await res.json(); } catch (e) { /* gövdesiz */ }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  function mount(host) {
    if (A.mounted) return;
    A.host = host || null;
    aapi(L.PATH.status).then((st) => {
      if (!L.isEnabled(st)) return;             // ajan yok/kapalı: panel hiç çizilmez
      A.csrf = st.csrf; A.st = st; A.mounted = true;
      build(); refresh(true);
      A.tick = setInterval(countdown, L.COUNTDOWN_MS);
    }).catch(() => {});
  }

  function build() {
    const host = A.host || q('panels');
    const cfg = el('details', { class: 'ag-cfg', id: 'ag-cfg' }, [
      el('summary', { text: 'Anahtar ve ayarlar (yalnızca bu bilgisayar)' }),
      el('div', { class: 'ag-form', id: 'ag-form' })]);
    host.append(el('div', { class: 'card', id: 'p-agent' }, [
      el('h2', { text: 'Yapay zekâ ajanı' }),
      el('div', { class: 'say', id: 'ag-meta' }),
      el('ul', { class: 'ag-chat', id: 'ag-chat', 'aria-live': 'polite' }),
      el('div', { id: 'ag-appr' }),
      el('form', { class: 'ag-send', id: 'ag-send', autocomplete: 'off' }, [
        el('input', { id: 'ag-text', type: 'text', maxLength: L.LIMITS.text, autocomplete: 'off',
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
      field('Model', el('input', { id: 'ag-model', type: 'text', value: st.model, maxLength: L.LIMITS.model })),
      field('OpenAI uç adresi (https)', el('input', { id: 'ag-url', type: 'url', value: st.base_url })),
      field('Günlük istek sınırı', num('ag-lim', st.limits.daily_limit, ...L.LIMITS.daily)),
      field('Tur başına hareket', num('ag-mot', st.limits.per_turn_motions, ...L.LIMITS.motions)),
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

  function renderMeta() {
    const st = A.st; if (!st) return;
    q('ag-meta').textContent = L.metaText(st);
    const ks = q('ag-keystate'); if (ks) ks.textContent = L.keyText(st);
  }

  function renderChat() {
    const ul = q('ag-chat'); ul.replaceChildren();
    for (const m of A.msgs) {
      ul.append(el('li', { class: L.chatClass(m) }, [el('b', { text: L.roleLabel(m.role) }), el('span', { text: m.text })]));
    }
    ul.scrollTop = ul.scrollHeight;
  }

  function renderApprovals() {
    const box = q('ag-appr'); box.replaceChildren();
    const top = L.topApprovalId(A.st);
    if (L.isNewApproval(A.lastApproval, top)) {     // yeni onay: telefonda panel ekranın altında kalmasın
      q('p-agent').scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast('Ajan hareket için onay bekliyor');
    }
    A.lastApproval = top;
    for (const ap of A.st.approvals) {
      const left = el('span', { class: 'ag-left', 'data-left': String(ap.expires_in_s), text: L.leftLabel(ap.expires_in_s) });
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
      const v = L.nextLeft(n.dataset.left); n.dataset.left = String(v);
      n.textContent = L.leftLabel(v);
    }
  }

  async function refresh(force) {
    if (!A.mounted) return;
    try {
      const st = await aapi(L.PATH.status);
      A.csrf = st.csrf;
      const signature = L.formSignature(st);
      const keyTyped = !!(q('ag-key') && q('ag-key').value);
      const rebuild = L.shouldRebuildForm({ force, signature, previous: A.formSig, keyTyped });
      A.st = st; A.formSig = signature;
      if (rebuild) buildForm(st);
      const chat = await aapi(L.chatPath(force, A.seq));
      ({ msgs: A.msgs, seq: A.seq } = L.mergeChat(A.msgs, chat.messages, force, A.seq));
      renderMeta(); renderChat(); renderApprovals();
      q('ag-go').disabled = L.sendDisabled(st, !!(S.snap && S.snap.me.can_control));
      q('ag-stop').disabled = L.stopDisabled(st);
    } catch (e) { /* oturum süresi dolmuş olabilir: app.js yönetir */ }
  }

  function onState(s) {
    if (!A.mounted || !L.hasAgent(s)) return;
    const sig = L.stateSignature(s);
    if (sig !== A.sig) { A.sig = sig; refresh(false); }
  }

  async function onSend(ev) {
    ev.preventDefault();
    const inp = q('ag-text'); const text = L.messageText(inp.value); if (!text) return;
    try { await aapi(L.PATH.message, 'POST', L.messageBody(text)); inp.value = ''; refresh(false); }
    catch (e) { toast(e.message, true); }
  }
  async function decide(id, approve) {
    try { await aapi(L.approvalPath(id), 'POST', L.decisionBody(approve)); }
    catch (e) { toast(e.message, true); }
    refresh(false);
  }
  async function onCancel() {
    try { await aapi(L.PATH.cancel, 'POST', {}); } catch (e) { toast(e.message, true); }
    refresh(false);
  }
  async function onSave() {
    const v = (id) => q(id).value;
    try {
      await aapi(L.PATH.config, 'POST', L.configBody({ provider: v('ag-prov'), model: v('ag-model'), baseUrl: v('ag-url'),
        dailyLimit: v('ag-lim'), maxMotions: v('ag-mot'), dryRun: q('ag-dry').checked }));
      toast('Ajan ayarları kaydedildi');
    } catch (e) { toast(e.message, true); }
    refresh(true);
  }
  async function onSetKey() {
    const inp = q('ag-key'); const key = inp.value; inp.value = '';          // kutuyu hemen boşalt
    if (!key) return;
    try {
      const r = await aapi(L.PATH.key, 'POST', L.keyBody(q('ag-prov').value, key, q('ag-rem').checked));
      const note = L.keyNotice(r); toast(note.text, note.bad);
    } catch (e) { toast(e.message, true); }
    refresh(true);
  }
  async function onClearKey() {
    try { await aapi(L.PATH.keyClear, 'POST', L.keyClearBody(q('ag-prov').value)); }
    catch (e) { toast(e.message, true); }
    refresh(true);
  }

  return { mount, onState };
})();
