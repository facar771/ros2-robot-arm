/* Parçalar sayfasının SAF mantığı (R12): DOM yok, fetch yok, saat yok → node ile test edilir (tests/js/parts_logic_check.js).
 * pages/parts.js yalnız DOM kurar, ağa konuşur ve sahneyi sürer; "hangi düğme kapalı / hangi uyarı görünür / hangi sahne nesnesi
 * çizilir / sunucuya hangi gövde gider" kararları ve METİNLER buradadır. Metinler ve nesne anahtar SIRASI ekranı ve sunucuya
 * giden gövdeleri belirler; değiştirme (davranış korunur).
 * GÜVENLİK: hareket KOMUTU bu dosyada YOKTUR. "Önce kontrol, sonra onay" akışı approach.js'tedir (checkMove/executeMove);
 * burada yalnız düğmelerin GÖSTERİM durumu hesaplanır, asıl denetim sunucudadır (arayüze güvenilmez).
 * Bağımlılık: scene_logic.js'in saf sayı/metin yardımcıları (window.SceneLogic, tembel); sayfa ikisini de `scripts` ile yükler. */
'use strict';

(() => {
  const SL = () => window.SceneLogic;
  const PICK_LAYERS = ['workpiece', 'environment'];                       // takım robota bağlıdır: hedef olamaz
  const DEFAULT_PICK = ['workpiece', 'tool', 'environment', 'markers'];    // başka sayfalar için eski hâl
  const STEP_EXT = /\.(step|stp|iges|igs)$/i, MAX_BYTES = 50 * 1024 * 1024, B64_CHUNK = 0x8000, DELETE_ARM_MS = 4000;
  const DEFAULT_D_MM = 50, DEFAULT_SPEED_PCT = 10;                         // KARAR GEREKLİ: varsayılanlar (PR'da)
  const TINT = { pending: 0xf0883e, ok: 0x3fb950, bad: 0xda3633, tool: 0xd29922 };
  const AP = { point: 'parts:ap-point', line: 'parts:ap-line', frame: 'parts:ap-frame', ghost: 'parts:ap-ghost', tgt: 'parts:ap-tgt' };
  const PLAN_IDS = [AP.frame, AP.line, AP.ghost, AP.tgt];                  // plan düşünce silinen yardımcılar (nokta kalır)
  const PART_SCENE_ID = /^parts:[0-9a-f]{32}$/;                           // yalnız parça nesneleri; ap-* yardımcıları buraya girmez
  const POSE_KEYS = [['x', 'x (mm)'], ['y', 'y (mm)'], ['z', 'z (mm)'], ['r', 'roll (°)'], ['p', 'pitch (°)'], ['w', 'yaw (°)']];
  const KIND_OPTS = [['workpiece', 'İş parçası'], ['tool', 'Takım'], ['environment', 'Ortam']];
  const UNIT_OPTS = [['mm', 'mm'], ['in', 'inç'], ['m', 'm']];
  const CAMERA_OPTS = [['iso', 'İzometrik'], ['top', 'Üst'], ['front', 'Ön'], ['side', 'Yan']];
  const GIZMO_OPTS = [['translate', 'Taşı'], ['rotate', 'Döndür'], ['off', 'Gizmo kapat']];
  const FILE_ACCEPT = '.stl,.obj,.step,.stp,.iges,.igs';
  const ORIENT_TEXT = { normal: 'Takım yüzeye dik baksın (−normal)', down: 'Hep aşağı bak', keep: 'Mevcut yönelimi koru' };
  /* Hareket yönelimi (UI_CONTRACT §9-B `orientation`): varsayılan SİM'de 'free', gerçek robotta 'fixed' (scene_logic.js). */
  const MODE_TEXT = [['free', 'Serbest (robot yönelimi seçer)'], ['fixed', 'Sabit (yönelimi aşağıdan seç)']];
  const MODE_HINT = {
    free: ['Serbest: yalnız KONUM hedeflenir, yönelimi robot seçer; alet yüzeye dik BAKMAYABİLİR. Dik/aşağı bakış gerekiyorsa Sabit seç.', ''],
    freeReal: ['Serbest yönelim GERÇEK ya da bilinmeyen robotta: alet beklenmedik açıyla gelebilir. Hedefi 3B’de iyi kontrol et.', 'warn'],
    fixed: ['Sabit: alet yönelimi seçtiğin gibi tutulur; bilek tekilliği yanındaki hedefler reddedilebilir (o zaman Serbest dene).', ''],
  };
  const ASSUMPTION = 'Not: 3B’deki parça konumunun gerçek parçayla uyuştuğu VARSAYILIR; bu bir ölçüm değildir (tahmini).';
  const NOTICE = 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)';   // sunucu bildirmezse yedek metin (docs/THIRD_PARTY.md)
  const TXT = {
    noPoint: 'Henüz nokta seçilmedi.', delIdle: 'Sil', delArmed: 'Emin misin? Sil', checking: 'Kontrol ediliyor…',
    tcpPlaced: 'Parça, robotun BİLDİRDİĞİ TCP konumuna konuldu (tahmini; ölçüm değil).',
    modeChanged: 'Mod değişti: yönelim varsayılanı uygulandı, yeniden kontrol et', settingChanged: 'Ayar değişti: yeniden kontrol et',
    staleNotSent: 'Robot verisi bayat: hareket GÖNDERİLMEDİ, yeniden kontrol et',
    toolNote: 'Takım ofseti yalnız KAYDEDİLİR ve 3B’de çizilir; robota uygulanmaz.',
    howTo: '3B’de parça yüzeyine dokun: nokta ve yüzey normali alınır. Önce kontrol edilir, sonra onayınla hareket eder.',
    stepOn: 'STEP/IGES: birim dosyadan okunur, parça mm olarak kaydedilir. Dönüşüm en çok 20 sn sürer.',
    stepOff: 'STEP için dönüştürücü kurulu değil, STL’e çevirip yükle.',
  };

  /* ---------- robot durumu (S.snap): yalnız OKUR ---------- */
  const finite3 = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === 'number' && Number.isFinite(x));
  const estopped = (snap) => !!(snap && snap.estop && snap.estop.latched);
  const canControl = (snap) => !!(snap && snap.me && snap.me.can_control);
  const robotStale = (snap) => !!(snap && snap.robot && snap.robot.stale);
  function reportedTcp(snap) {                                             // robotun BİLDİRDİĞİ TCP (§9-A: pos_m, rpy_deg W-P-R)
    const t = snap && snap.robot && snap.robot.tcp;
    const pos = t && (t.pos_m || t.position), rpy = t && (t.rpy_deg || t.wpr_deg);
    return finite3(pos) && finite3(rpy) ? { pos_m: pos.slice(), rpy_deg: rpy.slice() } : null;
  }
  function binaryString(buf) {                                             // parça parça: büyük dosyada yığın taşmasın (btoa'yı sayfa uygular)
    const u = new Uint8Array(buf); let s = '';
    for (let i = 0; i < u.length; i += B64_CHUNK) s += String.fromCharCode.apply(null, u.subarray(i, i + B64_CHUNK));
    return s;
  }

  /* ---------- sahne eşlemesi ve liste ---------- */
  const sceneId = (p) => `parts:${p.id}`;
  function staleIds(ids, parts) {                                          // sahnede olup listede olmayan PARÇA nesneleri (ap-* yardımcılarına dokunulmaz)
    const want = new Set(parts.map(sceneId));
    return ids.filter((id) => PART_SCENE_ID.test(id) && !want.has(id));
  }
  const needsRebuild = (cur, spec) => !!cur && (cur.layer !== spec.layer || cur.attach !== spec.attach);   // tür değişti: katman/bağlama yeniden kurulur
  const rowSub = (p) => `${SL().KIND_LABEL[p.kind]} · ${SL().sizeText(p.size_m)} · ${p.triangles} üçgen`;
  const countText = (n, limits) => `${n} / ${(limits && limits.max_parts) || '?'} parça`;
  const listState = (r) => ({ parts: r.parts || [], limits: r.limits || null, step: r.step || null, problems: (r.problems || []).join(' · ') });
  const saveSay = (note) => [note || 'Kaydedildi', note ? 'warn' : 'ok'];                   // not: "Kaydedildi" onu ezmesin

  /* ---------- yükleme (STL/OBJ + S2b: STEP/IGES) ---------- */
  const stepOn = (info) => !!(info && info.available);                    // sunucu bildirir: dönüştürücü kurulu mu
  const isStep = (name) => STEP_EXT.test(name || '');
  function fileChoice(file, info) {                                        // dönüştürücü yoksa STEP/IGES yüklenmez; varsa birim DOSYADAN okunur (mm)
    const step = !!file && isStep(file.name), on = stepOn(info);
    const say = !step ? ['', ''] : on ? [TXT.stepOn, ''] : [(info && info.message) || TXT.stepOff, 'warn'];
    return { goDisabled: step && !on, unitDisabled: step && on, say };
  }
  function uploadNote(info) {                                              // başlık, sınır notu ve LGPL bildirimi (dönüştürücü varsa)
    const on = stepOn(info), n = 'En çok 50 MB, 500 000 üçgen. ';
    return { title: on ? 'Parça ekle (STL / OBJ / STEP / IGES)' : 'Parça ekle (STL / OBJ)',
      note: n + (on ? 'STEP/IGES alt süreçte dönüştürülür (en çok 20 sn).' : 'STEP/IGES için dönüştürücü kurulu değil: CAD’den STL’e çevirip yükle.'),
      licHidden: !on, lic: on ? `${(info && info.notice) || NOTICE}. Lisans ve kaynak: docs/THIRD_PARTY.md` : '' };
  }
  function uploadPlan(file) {                                              // büyük dosya sunucuya GİTMEZ (sunucu da sınırlar)
    if (!file) return { error: 'Önce bir dosya seç' };
    if (file.size > MAX_BYTES) return { error: `Dosya ${MAX_BYTES / 1048576} MB sınırını aşıyor` };
    return { busy: isStep(file.name) ? 'Dönüştürülüyor (en çok 20 sn)…' : 'Yükleniyor…' };
  }
  function uploadBody(file, b64, kind, unit, name) {                       // anahtar sırası sunucuya giden gövdeyi belirler
    const body = { filename: file.name, data_b64: b64, kind, unit };
    if (name.trim()) body.name = name.trim();
    return body;
  }
  const uploadDone = (r) => { const w = r.warnings || []; return w.length ? [w.join(' · '), 'warn'] : ['Yüklendi', 'ok']; };

  /* ---------- seçili parça: yerleştirme ---------- */
  const poseKey = (p) => (p.kind === 'tool' ? 'tool_offset' : 'pose');   // takımda ofset, diğerlerinde dünya konumu
  const gimbalText = (rpy) => (SL().nearGimbal(rpy) ? 'Pitch ±90° yakınında: roll ve yaw birleşir (kilitlenme).' : '');
  function selectedView(p) {
    const tool = p.kind === 'tool';
    return { title: tool ? 'Takım ofseti (flanşa göre)' : 'Yerleşim (dünya çerçevesi)', toolNoteHidden: !tool, tcpHidden: tool,
      size: `Boyut (ölçekli): ${SL().sizeText(p.size_m)} · ${p.triangles} üçgen`, pose: p[poseKey(p)] };
  }
  function readPatch(p, fields, scaleText, unit) {                         // sayı alanları → kayıt yaması; geçersizse sunucuya GİTMEZ
    const pr = SL().readPoseFields(fields), sc = SL().readScale(scaleText);
    if (!pr.ok || !sc.ok) return { ok: false, message: (pr.ok ? sc : pr).message };
    return { ok: true, rpy: pr.pose.rpy_deg, patch: { [poseKey(p)]: pr.pose, scale: sc.scale, unit } };
  }
  function tcpBlock(snap) {                                                // '' = konulabilir
    const stale = robotStale(snap);
    if (!reportedTcp(snap) || stale) return stale ? 'Robot verisi bayat: TCP konumu alınamadı' : 'Robotun TCP konumu bilinmiyor';
    return '';
  }

  /* ---------- dokun-git / yaklaş: düğme durumu ve sahne nesneleri ---------- */
  function blockReason(snap, hasScene, hasPick) {                          // '' = hareket kurulabilir (yalnız gösterim; asıl denetim sunucuda)
    return estopped(snap) ? 'ACİL DUR aktif: hareket yok' : !canControl(snap) ? 'Bu oturum salt-okunur: hareket yok'
      : robotStale(snap) ? 'Robot verisi bayat: hareket yok' : !hasScene ? '3B görünüm yok: dokunarak hedef seçilemez'
      : !hasPick ? 'Önce 3B’de parçanın yüzeyine dokun' : '';
  }
  function approachButtons(snap, st) {                                     // st: { hasScene, hasPick, busy, hasPlan }
    const why = blockReason(snap, st.hasScene, st.hasPick), off = !!why || st.busy;
    return { nearOff: off, touchOff: off, okHidden: !st.hasPlan, okOff: st.busy || estopped(snap) || !canControl(snap) || robotStale(snap),
      why: why && !st.hasPlan ? why : '', whyCls: estopped(snap) ? 'bad' : '' };
  }
  const validPick = (res) => !!res && finite3(res.point_m) && finite3(res.normal);   // §9-C: yalnız isabette çağrılır
  const pickedPartId = (res) => (PART_SCENE_ID.test(res.objectId || '') ? res.objectId.slice('parts:'.length) : null);
  const pointSpec = (pick) => ({ id: AP.point, kind: 'marker', layer: 'markers', geometry: { radius_m: 0.006 },
    pose: { pos_m: pick.point_m, rpy_deg: [0, 0, 0] }, color: TINT.pending, opacity: 1 });
  function pointInfo(pick) {
    const mm = (x) => Math.round(x * 10000) / 10, n = pick.normal.map((x) => Math.round(x * 100) / 100).join(' ');
    return `Nokta (mm): ${pick.point_m.map(mm).join(' · ')}  ·  yüzey normali: ${n}`;
  }
  const visibleTool = (parts) => parts.find((p) => p.kind === 'tool' && p.visible);
  /* Hedef için sahne işleri, SIRAYLA: ['put', tanım] | ['drop', kimlik]. Yönelim yoksa (free) çerçeve/gölge takım çizilmez, yalnız hedef
   * noktası; yoksa çerçeve + (görünür takım ve TCP ofseti varsa) gölge takım. `offOf` tembeldir: yalnız gerektiğinde sahneye sorulur. */
  function targetOps(pick, t, tool, offOf) {
    const ops = [['put', { id: AP.line, kind: 'polyline', layer: 'path', geometry: { points_m: [pick.point_m, t.pos_m] }, color: TINT.pending }]];
    if (t.orientation === 'free') {
      ops.push(['drop', AP.frame], ['drop', AP.ghost], ['put', { id: AP.tgt, kind: 'marker', layer: 'path', geometry: { radius_m: 0.008 },
        pose: { pos_m: t.pos_m, rpy_deg: [0, 0, 0] }, color: TINT.pending, opacity: 0.9 }]);
      return ops;
    }
    ops.push(['drop', AP.tgt], ['put', { id: AP.frame, kind: 'frame', layer: 'path', geometry: { size_m: 0.05 }, pose: t }]);
    const off = tool && offOf();
    if (tool && off) {                                                     // gölge takım: bu hedefte takım nerede olur
      ops.push(['put', { id: AP.ghost, kind: 'mesh', layer: 'path', geometry: { url: tool.url, scale: tool.unit_m * tool.scale },
        pose: SL().ghostToolPose(t, off, tool.tool_offset), color: TINT.tool, opacity: 0.4 }]);
    } else ops.push(['drop', AP.ghost]);
    return ops;
  }

  /* Yönelim kipi: SİM'de varsayılan Serbest, gerçek/tezgâhta Sabit. Mod DEĞİŞİNCE (sim↔real) varsayılan yeniden uygulanır ve plan düşer;
   * kullanıcı kipi elle değiştirdiyse aynı modda durum yayınları onu ezmez. Mod bilinmiyorsa (robot bildirimi yok) dokunulmaz. */
  function modeView(free, modeKey) {
    const [text, cls] = MODE_HINT[free ? (modeKey === 'sim' ? 'free' : 'freeReal') : 'fixed'];
    return { orientHidden: free, text, cls };
  }
  function modeSync(key, modeKey, current) {                               // null = değişiklik yok
    if (key === 'unknown' || key === modeKey) return null;
    const want = SL().defaultMoveOrientation(key);
    return { modeKey: key, want, set: current !== want };
  }
  const gimbalTarget = (t) => (t.gimbal ? 'Hedef yönelimi pitch ±90° yakınında (kilitlenme): roll/yaw birleşir.' : '');
  const checkSay = (res, t) => (res.ok
    ? [`Kontrol geçti${t.orientation === 'free' ? ' (yönelimi robot seçer)' : ''}. Onaylarsan robot hareket eder.`, 'ok']
    : [`${res.message} — hareket GÖNDERİLMEDİ`, 'bad']);
  const moveSay = (res) => (res.ok ? [`Hareket komutu gönderildi${res.job_id ? ` (iş ${res.job_id})` : ''}.`, 'ok'] : [res.message, 'bad']);

  window.PartsLogic = { PICK_LAYERS, DEFAULT_PICK, DELETE_ARM_MS, DEFAULT_D_MM, DEFAULT_SPEED_PCT, TINT, AP, PLAN_IDS, POSE_KEYS,
    KIND_OPTS, UNIT_OPTS, CAMERA_OPTS, GIZMO_OPTS, FILE_ACCEPT, ORIENT_TEXT, MODE_TEXT, ASSUMPTION, TXT,
    estopped, robotStale, reportedTcp, binaryString, sceneId, staleIds, needsRebuild, rowSub, countText, listState, saveSay,
    fileChoice, uploadNote, uploadPlan, uploadBody, uploadDone, poseKey, gimbalText, selectedView, readPatch, tcpBlock,
    blockReason, approachButtons, validPick, pickedPartId, pointSpec, pointInfo, visibleTool, targetOps, modeView, modeSync,
    gimbalTarget, checkSay, moveSay };
  if (typeof module !== 'undefined') module.exports = window.PartsLogic;
})();
