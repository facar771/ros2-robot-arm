/* Ajan panelinin SAF mantığı (R9): DOM yok, fetch yok, saat yok → node ile test edilir (tests/js/agent_logic_check.js).
 * agent.js yalnız çizer ve ağa konuşur; "ne gösterilecek / ne gönderilecek / ne zaman yeniden çizilecek" kararları buradadır.
 * İstek gövdelerindeki anahtar SIRASI sunucuya giden JSON'u belirler; değiştirme (davranış korunur). */
'use strict';

(() => {
  const PATH = {
    status: '/api/agent/status', chat: '/api/agent/chat', message: '/api/agent/message', cancel: '/api/agent/cancel',
    config: '/api/agent/config', key: '/api/agent/key', keyClear: '/api/agent/key/clear', approvals: '/api/agent/approvals/',
  };
  const LIMITS = { text: 2000, model: 100, daily: [1, 100000], motions: [0, 20] };   // alan sınırları (sunucu yine doğrular)
  const COUNTDOWN_MS = 1000;                       // onay süresi geri sayımı 1 sn'de bir
  const ROLE_LABEL = { user: 'Sen', assistant: 'Ajan', tool: 'Araç', system: 'Sistem' };

  /* Panel yalnız ajan etkinse çizilir (GET /api/agent/status → enabled). */
  const isEnabled = (st) => !!(st && st.enabled);

  /* fetch seçenekleri: yazan uçlar JSON gövde + CSRF belirteci ister (sunucu 403 csrf verir); GET gövdesizdir. */
  function requestOptions(method, body, csrf) {
    const opt = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') {
      opt.headers['Content-Type'] = 'application/json';
      opt.headers['X-CSRF-Token'] = csrf;
      opt.body = JSON.stringify(body || {});
    }
    return opt;
  }

  /* Anahtar durumu metni. Sunucu anahtarı HİÇ geri göndermez; yalnız "ayarlı/hatırlanıyor" bayrakları gelir. */
  function keyText(st) {
    return Object.entries(st.keys).map(([p, v]) =>
      `${p}: ${v.set ? (v.remembered ? 'ayarlı (hatırlanıyor)' : 'ayarlı (yalnızca bellekte)') : 'yok'}`).join(' · ');
  }

  /* Üst satır: sağlayıcı/model, günlük kullanım, tur sınırı; kuru çalıştırmada başa uyarı (hareket robota gitmez). */
  function metaText(st) {
    const bits = [`${st.provider}${st.model ? ' / ' + st.model : ''}`,
      `bugün ${st.limits.used_today}/${st.limits.daily_limit} istek`,
      `tur başına en çok ${st.limits.per_turn_motions} hareket`];
    if (st.dry_run) bits.unshift('KURU ÇALIŞTIRMA');
    return bits.join(' · ') + ' — ' + keyText(st);
  }

  const roleLabel = (role) => ROLE_LABEL[role] || role;
  /* Başarısız araç çağrısı kırmızı görünür (.bad); bilinmeyen rol sınıf olarak olduğu gibi geçer. */
  const chatClass = (m) => 'ag-m ' + m.role + (m.role === 'tool' && m.ok === false ? ' bad' : '');

  /* Sohbet: ilk yüklemede/zorla baştan (since=0), sonra yalnız yenileri. seq = görülen en büyük mesaj kimliği
   * (zorla yenilemede SIFIRLANMAZ; liste boşalır ama imleç ilerlemeye devam eder). Girdiyi değiştirmez. */
  const chatPath = (force, seq) => PATH.chat + '?since=' + (force ? 0 : seq);
  function mergeChat(prev, incoming, force, seq) {
    const msgs = force ? [] : prev.slice();
    let top = seq;
    for (const m of incoming) { msgs.push(m); top = Math.max(top, m.id); }
    return { msgs, seq: top };
  }

  /* Form yalnız ayar alanları değişince yeniden kurulur; kullanıcı anahtar yazıyorsa DOKUNULMAZ (yazdığı kaybolmasın). */
  const formSignature = (st) => JSON.stringify([st.can_configure, st.provider, st.model, st.base_url, st.dry_run,
    st.limits.daily_limit, st.limits.per_turn_motions]);
  const shouldRebuildForm = ({ force, signature, previous, keyTyped }) => (force || signature !== previous) && !keyTyped;

  /* WS durum mesajından yalnız ajanı ilgilendiren alanlar değişince yenile (gereksiz istek yok). */
  const hasAgent = (s) => !!(s && s.agent);
  const stateSignature = (s) => JSON.stringify([s.agent.chat_seq, s.agent.active, s.agent.pending_approvals, s.estop.latched]);

  /* Gönder: ajan çalışmıyorsa VE kullanıcı kontrol yetkisine sahipse açık. Durdur: yalnız ajan çalışırken. */
  const sendDisabled = (st, canControl) => !st.run || st.run.active || !canControl;
  const stopDisabled = (st) => !(st.run && st.run.active);

  /* Onay: en üstteki bekleyen onay yeni ise panel öne kaydırılır ve bildirim çıkar (telefonda panel altta kalmasın). */
  const topApprovalId = (st) => (st.approvals.length ? st.approvals[0].id : null);
  const isNewApproval = (prevId, topId) => !!topId && topId !== prevId;
  const approvalPath = (id) => PATH.approvals + encodeURIComponent(id);
  const decisionBody = (approve) => ({ approve });
  const leftLabel = (sec) => `${Math.ceil(sec)} sn`;
  const nextLeft = (value) => Math.max(0, parseFloat(value) - 1);

  /* Gövdeler (anahtar sırası sabit). Sayılar 10 tabanında okunur: "5abc" → 5, boş → NaN (sunucu reddeder). */
  const messageText = (raw) => String(raw).trim();
  const messageBody = (text) => ({ text });
  const configBody = (v) => ({ provider: v.provider, model: v.model, base_url: v.baseUrl,
    daily_limit: parseInt(v.dailyLimit, 10), max_motions: parseInt(v.maxMotions, 10), dry_run: v.dryRun });
  const keyBody = (provider, key, remember) => ({ provider, key, remember });
  const keyClearBody = (provider) => ({ provider, forget: true });
  /* Anahtar yanıtı uyarı taşırsa (ör. kalıcı depo yok) bildirim kırmızı olur. */
  const keyNotice = (r) => ({ text: r.warning || 'Anahtar ayarlandı', bad: !!r.warning });

  window.AgentLogic = { PATH, LIMITS, COUNTDOWN_MS, isEnabled, requestOptions, keyText, metaText, roleLabel, chatClass,
    chatPath, mergeChat, formSignature, shouldRebuildForm, hasAgent, stateSignature, sendDisabled, stopDisabled,
    topApprovalId, isNewApproval, approvalPath, decisionBody, leftLabel, nextLeft, messageText, messageBody,
    configBody, keyBody, keyClearBody, keyNotice };
  if (typeof module !== 'undefined') module.exports = window.AgentLogic;
})();
