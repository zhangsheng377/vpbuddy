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

// 2026-06-28: ASR 30s batch 延迟显示 — 录音开始累加, 收到 segment 重置
// 因为 funasr 是 batch 模式, 用户说话后最长等 30s 才出字, 必须有视觉反馈
const LATENCY_WINDOW_S = 30; // 服务端 30s 切片
let latencyTimer = null;
let latencyStartMs = 0;
function startLatencyTicker() {
  if (latencyTimer) clearInterval(latencyTimer);
  latencyStartMs = Date.now();
  const el = document.getElementById("latency");
  if (!el) return;
  el.textContent = `已等 0.0s / ${LATENCY_WINDOW_S}s`;
  latencyTimer = setInterval(() => {
    const elapsed = (Date.now() - latencyStartMs) / 1000;
    el.textContent = `已等 ${elapsed.toFixed(1)}s / ${LATENCY_WINDOW_S}s`;
  }, 500);
}
function stopLatencyTicker(resetText = true) {
  if (latencyTimer) { clearInterval(latencyTimer); latencyTimer = null; }
  if (resetText) {
    const el = document.getElementById("latency");
    if (el) el.textContent = "延迟 -";
  }
}

const i18n = {
  zh: {
    idle: "未连接", capturing: "采集中...", stopped: "已停止", noResult: "无结果",
    sseConnected: "SSE 已连接", sseHeartbeat: "SSE 心跳正常"
  },
  en: {
    idle: "Disconnected", capturing: "Capturing...", stopped: "Stopped", noResult: "No results",
    sseConnected: "SSE connected", sseHeartbeat: "SSE heartbeat ok"
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
    return localStorage.getItem("vpbuddy-gpu-url") || "http://gpu.zhangshengdong.com:8765";
  }
}

// === 实时转写流 ===
// 2026-06-27: 开始/停止 合并成一个 toggle 按钮 (#btn-rec, data-state="idle"|"recording")
document.getElementById("btn-rec").addEventListener("click", async () => {
  const btn = document.getElementById("btn-rec");
  const dot = document.getElementById("rec-dot");
  const status = document.getElementById("rec-status");
  if (btn.dataset.state === "idle") {
    // 开始录音
    btn.disabled = true;
    try {
      const e = document.getElementById("audio-device").value || null;
      currentMeetingId = await invoke("start_capture", {
        autoUpload: document.getElementById("auto-upload").checked,
        audioDevice: e,
      });
      recording = true;
      startLatencyTicker();
      btn.dataset.state = "recording";
      btn.textContent = "停止录音";
      dot.className = "dot live";
      status.textContent = t("capturing");
      btn.disabled = false;
      // 2026-06-27: 不再调 refreshDocs — 内容由 SSE doc-status 自动推流
      await refreshChatHistory();
    } catch (e) {
      status.textContent = "❌ " + e;
      btn.disabled = false;
    }
  } else {
    // 停止录音
    btn.disabled = true;
    try {
      await invoke("stop_capture");
      recording = false;
      stopLatencyTicker();
      btn.dataset.state = "idle";
      btn.textContent = "开始录音";
      dot.className = "dot";
      status.textContent = t("stopped");
    } catch (e) {
      status.textContent = "❌ " + e;
    } finally {
      btn.disabled = false;
    }
  }
});

// === 监听 Tauri 后端事件 ===
// 2026-06-27 加强: 时间戳 + 说话人分色块 + 自动滚动到顶 + 新增提示动画
listen("transcript-segment", (e) => {
  const seg = e.payload;
  // 2026-06-28: 收到新段, 重置延迟计时器 (新 chunk 转写完了)
  if (latencyTimer) {
    latencyStartMs = Date.now();
    document.getElementById("latency").textContent = `已等 0.0s / ${LATENCY_WINDOW_S}s (刚出字)`;
  }
  segCount += 1;
  const item = document.createElement("div");
  item.className = "stream-item";
  // 格式化时间 MM:SS.mmm
  const startSec = seg.start_sec || 0;
  const mm = Math.floor(startSec / 60).toString().padStart(2, "0");
  const ss = (startSec % 60).toFixed(1).padStart(4, "0");
  const timeStr = `${mm}:${ss}`;
  const spkId = String(seg.speaker_id || "?");
  // 说话人彩色块 — 末两位做 hash 映射色板
  const colorIdx = parseInt(spkId.slice(-2), 10) % 8;
  item.innerHTML =
    `<span class="time">${timeStr}</span>` +
    `<span class="spk spk-${colorIdx}">${escapeHtml(spkId)}</span>` +
    ` <span class="text">${escapeHtml(seg.text || "")}</span>`;
  const list = document.getElementById("stream-list");
  // 新增项插入顶部
  list.insertBefore(item, list.firstChild);
  // 触发动画 — 先加高亮, 0.8s 后移除
  item.classList.add("stream-item-fresh");
  setTimeout(() => item.classList.remove("stream-item-fresh"), 800);
  document.getElementById("seg-count").textContent = `${segCount} 段`;
  // 显示"最近一段"提示
  const lastBadge = document.getElementById("last-seg");
  if (lastBadge) lastBadge.textContent = `最新: ${(seg.text || "").slice(0, 30)}`;
});

listen("capture-stats", (e) => {
  byteCount = e.payload.bytes;
  upCount = e.payload.uploads;
  document.getElementById("byte-count").textContent = `${(byteCount/1024).toFixed(0)} KB`;
  document.getElementById("up-count").textContent = `${upCount} 上传`;
});

// 2026-06-27: 实时波形图 — Rust 每 0.5s emit audio-level (RMS 0.0-1.0)
// 画 60 帧历史, 绿柱按 RMS 高度, 平线 = 静音
const wfCanvas = document.getElementById("waveform");
const wfCtx = wfCanvas?.getContext("2d");
const wfHistory = [];  // 最近 60 帧 RMS
const WF_MAX = 60;
function drawWaveform() {
  if (!wfCtx || !wfCanvas) return;
  // 高 DPI 处理
  const dpr = window.devicePixelRatio || 1;
  const w = wfCanvas.clientWidth;
  const h = wfCanvas.clientHeight;
  if (wfCanvas.width !== w * dpr || wfCanvas.height !== h * dpr) {
    wfCanvas.width = w * dpr;
    wfCanvas.height = h * dpr;
  }
  wfCtx.scale(dpr, dpr);
  wfCtx.clearRect(0, 0, w, h);
  // 中线
  wfCtx.strokeStyle = "rgba(255,255,255,0.06)";
  wfCtx.beginPath();
  wfCtx.moveTo(0, h / 2);
  wfCtx.lineTo(w, h / 2);
  wfCtx.stroke();
  // 60 个柱子
  const barW = w / WF_MAX;
  for (let i = 0; i < wfHistory.length; i++) {
    const rms = wfHistory[i];
    const barH = Math.max(2, rms * h * 0.95);
    const x = i * barW;
    const y = (h - barH) / 2;
    // 按 RMS 大小着色: <0.01 灰, <0.1 黄, ≥0.1 绿
    if (rms < 0.01) wfCtx.fillStyle = "rgba(120,130,150,0.4)";
    else if (rms < 0.1) wfCtx.fillStyle = "#f59e0b";
    else wfCtx.fillStyle = "#10b981";
    wfCtx.fillRect(x + 1, y, barW - 2, barH);
  }
}
listen("audio-level", (e) => {
  const rms = e.payload?.rms || 0;
  wfHistory.push(rms);
  if (wfHistory.length > WF_MAX) wfHistory.shift();
  drawWaveform();
});
// 录音停止时清空波形 (兜底: 用户停止录音 3s 后波形归零)
setInterval(() => {
  if (!recording && wfHistory.some(v => v > 0)) {
    wfHistory.length = 0;
    drawWaveform();
  }
}, 3000);

// 2026-06-27: doc-status SSE 事件直接写入对应 doc-block, 不再走单 viewer
// 2026-06-27 v2: demo 走独立的 panel-demo iframe (不再 inline 替换 pre)
listen("doc-status", (e) => {
  const { meeting_id, kind, state, status, count, content, doc_size, is_demo } = e.payload;
  if (meeting_id) currentMeetingId = meeting_id;
  const docState = state || status || "queued";
  const block = document.querySelector(`.doc-block[data-kind="${kind}"]`);
  if (block) {
    block.className = `doc-block ${docState}${kind === "demo" ? " doc-block-stub" : ""}`;
    const countEl = block.querySelector(".doc-count");
    const stateEl = block.querySelector(".doc-state");
    if (countEl) {
      countEl.textContent = kind === "demo" ? "-" :
        (doc_size ? `${Math.ceil(doc_size / 1024)}KB` : (count || 0));
    }
    if (stateEl) {
      stateEl.textContent =
        kind === "demo" ? "已抽到独立页签" :
        docState === "stored" ? "✓ 已生成" :
        docState === "queued" || docState === "triggered" ? "生成中…" :
        docState === "failed" ? "✗ 失败" :
        "待生成";
    }
  }
  if (kind && content) {
    docsByKind[kind] = { kind, status: docState, content, is_demo };
    // 2026-06-27: demo 写到独立 panel-demo 的 iframe (全屏)
    if (kind === "demo" && is_demo) {
      const frame = document.getElementById("demo-iframe");
      if (frame) frame.srcdoc = content;
      return;
    }
    // 其他 5 类写到自己 doc-block 的 body
    const body = block?.querySelector(".doc-body");
    if (body) body.textContent = content;
  }
});

listen("connection-status", (e) => {
  const p = e.payload || {};
  if (p.sse === "connected") document.getElementById("conn-status").textContent = t("sseConnected");
  if (p.sse === "heartbeat") document.getElementById("conn-status").textContent = t("sseHeartbeat");
  if (p.upload === "failed") document.getElementById("conn-status").textContent = "上传失败，已重试";
});

// 2026-06-28 ADR-0018: GPU 端 6 docs 全 stored 后推 meeting-complete
// 客户端: 标记会议状态 + 把按钮文字改成 "会议完成, 开始新会议"
// 注意: 不需要 refreshDocs — SSE doc-status 之前已逐个推过来, 6 块已是最新
listen("meeting-complete", (e) => {
  const p = e.payload || {};
  const btn = document.getElementById("btn-rec");
  if (btn) {
    btn.dataset.state = "idle";
    btn.textContent = "✅ 会议完成 (开始新会议)";
    btn.disabled = false;
  }
  const status = document.getElementById("rec-status");
  if (status) status.textContent = "🎉 6 文档已全部生成";
  console.log("meeting-complete:", p);
});

listen("metrics-update", (e) => {
  const p = e.payload || {};
  const latency = p.end_to_end_ms || p.processing_ms;
  document.getElementById("latency").textContent = latency ? `延迟 ${latency}ms` : "延迟 -";
});

listen("chat-message", (e) => {
  renderChatMessage(e.payload);
});

// 2026-06-26: GPU 服务器连接指示灯 (绿=在线, 红=离线, 黄=检测中)
listen("gpu-connection", (e) => {
  const p = e.payload || {};
  const pill = document.getElementById("gpu-pill");
  const status = document.getElementById("gpu-status");
  if (!pill || !status) return;
  pill.classList.remove("online", "offline", "checking");
  const url = p.url || "";
  let label;
  if (p.status === "online") {
    pill.classList.add("online");
    label = "已连接";
  } else if (p.status === "offline") {
    pill.classList.add("offline");
    label = "未连接";
  } else {
    pill.classList.add("checking");
    label = "检测中";
  }
  // 显示地址最后一截 (host:port) + detail
  try {
    const u = new URL(url);
    label += ` ${u.host}`;
  } catch (_) {}
  status.textContent = label;
  pill.title = `GPU: ${url}\n${p.detail || ""}`;
});

// 实时结构化事实更新 (REQ/GOAL/FEAT/RISK/QUE)
listen("state-update", (e) => {
  const stats = e.payload;
  // 2026-06-27: 5 类结构化事实降级到 stream 顶部 pill, 不再有 fact-list
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
});

// 2026-06-27: 6 文档改并列展示, 删除 click 切换 + refreshDocs + renderDoc 单 viewer
// 内容全部由 SSE "doc-status" 推流自动写入对应 .doc-block

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
        selected_doc_kind: document.querySelector(".doc-block.stored")?.dataset.kind || null,
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
    // 2026-06-27: 0 设备要醒目提示 (常见于: Win 隐私设置禁麦克风 / 没插麦)
    if (devices.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "⚠️ 未检测到任何输入设备";
      select.appendChild(opt);
      const recStatus = document.getElementById("rec-status");
      if (recStatus) recStatus.textContent = "⚠️ 无输入设备 — 检查 Windows 麦克风隐私设置";
      return;
    }
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.is_default ? `${d.name}（默认）` : d.name;
      select.appendChild(opt);
    }
  } catch (e) {
    console.warn("获取音频设备失败", e);
    // 2026-06-27: invoke 失败也提示 (cpal 初始化错误)
    const recStatus = document.getElementById("rec-status");
    if (recStatus) recStatus.textContent = "❌ 音频设备枚举失败: " + e;
  }
}

document.getElementById("ui-lang").value = lang;
document.getElementById("ui-lang").addEventListener("change", (e) => {
  lang = e.target.value;
  localStorage.setItem("vpbuddy-lang", lang);
  document.getElementById("rec-status").textContent = recording ? t("capturing") : t("idle");
});

// GPU URL 保存按钮 (2026-06-27)
document.getElementById("btn-save-url").addEventListener("click", async () => {
  const url = document.getElementById("gpu-url").value.trim();
  if (!url) return;
  try {
    // 2026-06-27 修: 之前是 a("set_gpu_url") → ReferenceError: a is not defined
    // 'a' 是 typo, 应该是 invoke。set_gpu_url 命令由 Rust 注册, 这里正确调用。
    await invoke("set_gpu_url", { url });
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

// 2026-06-27: 设置页显示客户端日志路径 + 复制按钮
(async () => {
  const el = document.getElementById("log-path");
  if (!el) return;
  try {
    const p = await invoke("get_log_path_cmd");
    el.textContent = p;
    el.title = p;
  } catch (e) {
    el.textContent = "(获取失败: " + e + ")";
  }
})();
document.getElementById("btn-open-log-dir")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-open-log-dir");
  if (!btn) return;
  try {
    // 2026-06-27: 用 tauri-plugin-opener 的 reveal_item_in_dir 打开文件管理器
    // (Win: 资源管理器高亮该文件; mac: Finder 高亮; Linux: 文件管理器打开父目录)
    await invoke("open_log_dir_cmd");
    btn.textContent = "✓ 已打开";
    setTimeout(() => { btn.textContent = "打开目录"; }, 2000);
  } catch (e) {
    btn.textContent = "❌ " + e;
  }
});
