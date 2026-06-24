// VPBuddy Desktop Client — Tauri 前端
// 设计: 不依赖浏览器, 直接调 Tauri Rust 后端 (invoke)
//       后端持续抓系统音频 + 推到 GPU server + 通过 SSE 回流 UI

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

// === 状态 ===
let recording = false;
let segCount = 0;
let byteCount = 0;
let upCount = 0;
let currentMeetingId = null;
let docsByKind = {};

const i18n = {
  zh: {
    idle: "未连接", capturing: "采集中...", stopped: "已停止", noResult: "无结果",
    docHint: "选择文档查看内容", sseConnected: "SSE 已连接", sseHeartbeat: "SSE 心跳正常"
  },
  en: {
    idle: "Disconnected", capturing: "Capturing...", stopped: "Stopped", noResult: "No results",
    docHint: "Select a document to view", sseConnected: "SSE connected", sseHeartbeat: "SSE heartbeat ok"
  }
};
let lang = localStorage.getItem("vpbuddy-lang") || "zh";

// === UI 切换 ===
document.querySelectorAll(".bottom-nav button").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".bottom-nav button").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    document.getElementById("panel-" + b.dataset.panel).classList.add("active");
  });
});

// === 录音控制 ===
document.getElementById("btn-start").addEventListener("click", async () => {
  try {
    const device = document.getElementById("audio-device").value || null;
    currentMeetingId = await invoke("start_capture", {
      autoUpload: document.getElementById("auto-upload").checked,
      audioDevice: device
    });
    recording = true;
    document.getElementById("btn-start").disabled = true;
    document.getElementById("btn-stop").disabled = false;
    document.getElementById("rec-dot").className = "dot live";
    document.getElementById("rec-status").textContent = t("capturing");
    await refreshDocs();
  } catch (e) {
    document.getElementById("rec-status").textContent = "❌ " + e;
  }
});

document.getElementById("btn-stop").addEventListener("click", async () => {
  try {
    await invoke("stop_capture");
    recording = false;
    document.getElementById("btn-start").disabled = false;
    document.getElementById("btn-stop").disabled = true;
    document.getElementById("rec-dot").className = "dot";
    document.getElementById("rec-status").textContent = t("stopped");
  } catch (e) {
    document.getElementById("rec-status").textContent = "❌ " + e;
  }
});

// === 监听 Tauri 后端事件 ===
listen("transcript-segment", (e) => {
  const seg = e.payload;
  segCount += 1;
  const item = document.createElement("div");
  item.className = "stream-item";
  item.innerHTML = `<span class="time">${seg.start_sec.toFixed(1)}s</span>` +
    `<span class="spk spk-${String(seg.speaker_id).slice(-2)}">${seg.speaker_id}</span>` +
    ` <span class="text">${escapeHtml(seg.text)}</span>`;
  const list = document.getElementById("stream-list");
  list.insertBefore(item, list.firstChild);
  document.getElementById("seg-count").textContent = `${segCount} 段`;
});

listen("capture-stats", (e) => {
  byteCount = e.payload.bytes;
  upCount = e.payload.uploads;
  document.getElementById("byte-count").textContent = `${(byteCount/1024).toFixed(0)} KB`;
  document.getElementById("up-count").textContent = `${upCount} 上传`;
});

listen("doc-status", (e) => {
  const { meeting_id, kind, state, status, count, content, updated_at, doc_size, is_demo } = e.payload;
  if (meeting_id) currentMeetingId = meeting_id;
  const docState = state || status || "queued";
  const card = document.querySelector(`.doc-card[data-kind="${kind}"]`);
  if (card) {
    card.className = `doc-card ${docState}`;
    card.querySelector(".doc-count").textContent = doc_size ? `${Math.ceil(doc_size / 1024)}KB` : (count || 0);
    card.querySelector(".doc-state").textContent = docState === "stored" ? "✓ 已生成" : docState === "queued" || docState === "triggered" ? "生成中..." : "✗";
  }
  if (kind && content) {
    docsByKind[kind] = { kind, status: docState, content, updated_at, is_demo };
    if (kind === "demo") renderDoc(kind);
  }
});

listen("connection-status", (e) => {
  const p = e.payload || {};
  if (p.sse === "connected") document.getElementById("conn-status").textContent = t("sseConnected");
  if (p.sse === "heartbeat") document.getElementById("conn-status").textContent = t("sseHeartbeat");
  if (p.upload === "failed") document.getElementById("conn-status").textContent = "上传失败，已重试";
});

listen("metrics-update", (e) => {
  const p = e.payload || {};
  const latency = p.end_to_end_ms || p.processing_ms;
  document.getElementById("latency").textContent = latency ? `延迟 ${latency}ms` : "延迟 -";
});

// 实时结构化事实更新 (REQ/GOAL/FEAT/RISK/QUE)
listen("state-update", (e) => {
  const stats = e.payload;
  // 更新统计数字
  const reqEl = document.getElementById("fact-req");
  const goalEl = document.getElementById("fact-goal");
  const featEl = document.getElementById("fact-feat");
  const riskEl = document.getElementById("fact-risk");
  const queEl = document.getElementById("fact-que");
  if (reqEl) reqEl.textContent = stats.requirements || 0;
  if (goalEl) goalEl.textContent = stats.goals || 0;
  if (featEl) featEl.textContent = stats.features || 0;
  if (riskEl) riskEl.textContent = stats.risks || 0;
  if (queEl) queEl.textContent = stats.questions || 0;

  // 如果有 items 详情，更新列表
  const listEl = document.getElementById("fact-list");
  if (listEl && stats.items && stats.items.length > 0) {
    listEl.innerHTML = stats.items.map(item => `
      <div class="fact-item-card">
        <span class="fact-tag fact-tag-${item.type}">${item.type.toUpperCase()}</span>
        <span class="fact-text">${escapeHtml(item.text)}</span>
      </div>
    `).join("");
  }
});

// === 文档展示 / Demo 预览 ===
document.querySelectorAll(".doc-card").forEach(card => {
  card.addEventListener("click", () => renderDoc(card.dataset.kind));
});

document.getElementById("btn-refresh-docs").addEventListener("click", refreshDocs);

async function refreshDocs() {
  if (!currentMeetingId) {
    try { currentMeetingId = await invoke("get_current_meeting"); } catch (_) {}
  }
  if (!currentMeetingId) return;
  const result = await invoke("get_meeting_docs", { meetingId: currentMeetingId });
  docsByKind = {};
  for (const doc of (result.docs || [])) {
    docsByKind[doc.kind] = doc;
    const card = document.querySelector(`.doc-card[data-kind="${doc.kind}"]`);
    if (card) {
      card.className = `doc-card ${doc.status}`;
      card.querySelector(".doc-count").textContent = doc.doc_size ? `${Math.ceil(doc.doc_size / 1024)}KB` : 0;
      card.querySelector(".doc-state").textContent = doc.status === "stored" ? "✓ 已生成" : "待生成";
    }
  }
}

function renderDoc(kind) {
  const doc = docsByKind[kind];
  document.getElementById("doc-title").textContent = doc?.label || kind || t("docHint");
  const pre = document.getElementById("doc-content");
  const frame = document.getElementById("demo-frame");
  if (!doc || !doc.content) {
    pre.style.display = "block";
    frame.style.display = "none";
    pre.textContent = "暂未生成";
    return;
  }
  if (kind === "demo") {
    pre.style.display = "none";
    frame.style.display = "block";
    frame.srcdoc = doc.content;
  } else {
    frame.style.display = "none";
    pre.style.display = "block";
    pre.textContent = doc.content;
  }
}

listen("error", (e) => {
  document.getElementById("rec-status").textContent = "❌ " + e.payload;
  document.getElementById("rec-dot").className = "dot err";
});

// === KB 检索 ===
document.getElementById("kb-btn").addEventListener("click", async () => {
  const q = document.getElementById("kb-q").value.trim();
  if (!q) return;
  const results = await invoke("kb_search", { query: q, topK: 5 });
  const html = results.map((r, i) => `
    <div class="kb-result">
      <div class="head">
        <span class="badge">${i+1}</span>
        <span>${r.meeting_id}/${r.doc_kind}</span>
        <span>dist=${r.distance.toFixed(3)}</span>
      </div>
      <div>${escapeHtml(r.snippet).slice(0, 200)}</div>
    </div>
  `).join("");
  document.getElementById("kb-results").innerHTML = html || `<div style='color:var(--text2);padding:20px;'>${t("noResult")}</div>`;
});

async function initAudioDevices() {
  try {
    const select = document.getElementById("audio-device");
    const devices = await invoke("list_audio_devices");
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.is_default ? `${d.name}（默认）` : d.name;
      select.appendChild(opt);
    }
  } catch (e) {
    console.warn("获取音频设备失败", e);
  }
}

document.getElementById("ui-lang").value = lang;
document.getElementById("ui-lang").addEventListener("change", (e) => {
  lang = e.target.value;
  localStorage.setItem("vpbuddy-lang", lang);
  document.getElementById("rec-status").textContent = recording ? t("capturing") : t("idle");
});

function t(key) {
  return (i18n[lang] || i18n.zh)[key] || key;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

initAudioDevices();
