/* Sayfa: Yapay zekâ ajanı. Menüde yalnız ajan etkinse görünür (GET /api/agent/status enabled). */
'use strict';

(() => {
  HubPages.register({
    id: 'agent', title: 'Ajan', icon: '💬', order: 30,
    scripts: ['/agent_logic.js', '/agent.js'],            // mantık ÖNCE: agent.js yüklenirken AgentLogic hazır olmalı
    async available() {
      try { const r = await fetch('/api/agent/status', { credentials: 'same-origin' }); return r.ok && (await r.json()).enabled === true; }
      catch (e) { return false; }
    },
    mount(root) { AgentPanel.mount(root); },
    onState(s) { AgentPanel.onState(s); },
  });
})();
