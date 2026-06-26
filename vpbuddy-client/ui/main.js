// VPBuddy Desktop Client — Tauri 前端
// 设计: 不依赖浏览器, 直接调 Tauri Rust 后端 (invoke)
//       后端持续抓系统音频 + 推到 GPU server + 通过 SSE 回流 UI
//
// 2026-06-26: Tauri 2.6.3 稳定版去掉 window.__TAURI__,
// 必须用 ESM import 从 @tauri-apps/api 导入.
// Vite 构建时把 node_modules 里的包打进 bundle.

import { invoke as _invoke, convertFileSrc } from '@tauri-apps/api/core';
import { listen as _listen } from '@tauri-apps/api/event';

// 2026-06-26: Tauri 2.6.3 的 import 在 Vite 构建后正常工作.
// 如果因为是直接加载 index.html (非 Vite 构建) 失败,
// 尝试回退到 window.__TAURI__.
let invoke = typeof _invoke === 'function' ? _invoke : undefined;
let listen = typeof _listen === 'function' ? _listen : undefined;

if (!invoke && window.__TAURI__ && window.__TAURI__.core) {
  invoke = window.__TAURI__.core.invoke;
  listen = window.__TAURI__.event.listen;
  console.warn("Tauri API: 使用 window.__TAURI__ 回退模式");
} else if (!invoke) {
  console.warn("Tauri API 不可用 — 模拟模式 (UI 调试)");
  invoke = () => Promise.reject(new Error("Tauri 未连接"));
  listen = () => Promise.reject(new Error("Tauri 未连接"));
}

// === 状态 ===
let recording = false;
let segCount = 0;
let byteCount = 0;
let upCount = 0;
let currentMeetingId = null;
let docsByKind = {};
const renderedChatIds = new Set();

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

// GPU URL 获取 (2026-06-26: 用 fetch 替代 invoke)
async function getGpuUrl() {
  try {
    return await invoke("get_gpu_url");
  } catch (_) {
    return localStorage.getItem("vpbuddy-gpu-url") || "http://192.168.10.63:8765";
  }
}

// === 实时转写流 ===
document.getElementById("btn-start").addEventListener("click", async () => {
  try {
    const e = document.getElementById("audio-device").value || null;
    currentMeetingId = await invoke("start_capture", {
      autoUpload: document.getElementById("auto-upload").checked,
      audioDevice: e,
    });
    recording = true;
    document.getElementById("btn-start").disabled = true;
    document.getElementById("btn-stop").disabled = false;
    document.getElementById("rec-dot").className = "dot live";
    document.getElementById("rec-status").textContent = t("capturing");
    await refreshDocs();
    await refreshChatHistory();
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

listen("chat-message", (e) => {
  renderChatMessage(e.payload);
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
  if (!currentMeetingId) return;
  // 2026-06-26: 走 Tauri invoke (Rust reqwest), 不再 webview fetch
  // 原因: webview fetch 跨域受限 + POST application/json 触发 OPTIONS 预检 → Failed to fetch
  try {
    const result = await invoke("fetch_meeting_docs", { meetingId: currentMeetingId });
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
  } catch (e) {
    console.warn("refreshDocs fetch failed:", e);
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

// === VP Chat ===
document.getElementById("chat-send").addEventListener("click", sendChat);
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    sendChat();
  }
});

async function sendChat() {
  if (!currentMeetingId) {
    document.getElementById("chat-status").textContent = "请先开始会议";
    return;
  }
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  document.getElementById("chat-status").textContent = "Hermes 正在思考...";
  try {
    // 2026-06-26: 走 invoke (Rust reqwest), 不再 webview fetch
    const result = await invoke("post_meeting_chat", {
      meetingId: currentMeetingId,
      message,
      context: {
        active_panel: document.querySelector(".bottom-nav button.active")?.dataset.panel || "chat",
        selected_doc_kind: document.querySelector(".doc-card.stored")?.dataset.kind || null,
      },
    });
    if (result.user_message) renderChatMessage(result.user_message);
    if (result.assistant_message) renderChatMessage(result.assistant_message);
    document.getElementById("chat-status").textContent = "Hermes 已回复";
  } catch (e) {
    document.getElementById("chat-status").textContent = "Chat 失败：" + e;
  }
}

async function refreshChatHistory() {
  if (!currentMeetingId) return;
  try {
    // 2026-06-26: 走 invoke (Rust reqwest), 不再 webview fetch
    const result = await invoke("fetch_meeting_chat_history", { meetingId: currentMeetingId });
    for (const msg of (result.messages || [])) renderChatMessage(msg);
  } catch (e) {
    console.warn("读取 Chat 历史失败", e);
  }
}

function renderChatMessage(msg) {
  if (!msg || !msg.id || renderedChatIds.has(msg.id)) return;
  renderedChatIds.add(msg.id);
  const list = document.getElementById("chat-list");
  const empty = list.querySelector(".chat-empty");
  if (empty) empty.remove();
  const item = document.createElement("div");
  item.className = `chat-msg ${msg.role || "assistant"} ${msg.status || "ok"}`;
  const role = msg.role === "user" ? "VP" : (msg.source === "hermes" ? "Hermes" : "VPBuddy");
  item.innerHTML = `
    <div class="chat-meta"><span>${role}</span><span>${escapeHtml(msg.created_at || "")}</span></div>
    <div class="chat-content">${escapeHtml(msg.content || "")}</div>
  `;
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;
}

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

// GPU URL 保存按钮 (2026-06-26)
document.getElementById("btn-save-url").addEventListener("click", async () => {
  const url = document.getElementById("gpu-url").value.trim();
  if (!url) return;
  try {
    await a("set_gpu_url", { url });
    document.getElementById("btn-save-url").textContent = "✓ 已保存";
    setTimeout(() => { document.getElementById("btn-save-url").textContent = "保存"; }, 2000);
  } catch (e) {
    document.getElementById("btn-save-url").textContent = "❌ " + e;
  }
});

function t(key) {
  return (i18n[lang] || i18n.zh)[key] || key;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

initAudioDevices();
