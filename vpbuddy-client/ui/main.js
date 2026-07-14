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

// ============================================================================
// ADR-0047: Auth state & token management
// ============================================================================
let authToken = null;
let authEmail = null;

function getAuthToken() {
  if (!authToken) { authToken = localStorage.getItem("vpbuddy-token"); }
  return authToken;
}
function setAuthToken(token, email) {
  authToken = token; authEmail = email;
  localStorage.setItem("vpbuddy-token", token);
  localStorage.setItem("vpbuddy-email", email);
}
function clearAuthToken() {
  authToken = null; authEmail = null;
  localStorage.removeItem("vpbuddy-token");
  localStorage.removeItem("vpbuddy-email");
}
function fetchWithAuth(url, opts = {}) {
  const token = getAuthToken();
  if (!token) return fetch(url, opts);
  opts.headers = opts.headers || {};
  opts.headers["Authorization"] = "Bearer " + token;
  return fetch(url, opts);
}

// ============================================================================
// Auth UI: login / register overlay
// ============================================================================
async function checkAuthAndInit() {
  const overlay = document.getElementById("auth-overlay");
  if (!overlay) return;

  // Bind auth form events
  document.getElementById("auth-login-btn")?.addEventListener("click", handleLogin);
  document.getElementById("auth-register-btn")?.addEventListener("click", handleRegister);
  document.getElementById("auth-show-login")?.addEventListener("click", showLoginForm);
  document.getElementById("auth-show-register")?.addEventListener("click", showRegisterForm);

  const token = getAuthToken();
  if (!token) { overlay.style.display = "flex"; return; }

  try {
    const gpu = await getGpuUrl();
    const resp = await fetch(gpu + "/api/auth/me", { headers: { Authorization: "Bearer " + token } });
    if (resp.ok) {
      const data = await resp.json();
      authEmail = data.email;
      overlay.style.display = "none";
    } else { clearAuthToken(); overlay.style.display = "flex"; return; }
  } catch (e) { clearAuthToken(); overlay.style.display = "flex"; return; }

  // Authenticated — run init
  initAfterAuth();
}

function initAfterAuth() {
  initAudioDevices();
  (async () => {
    const el = document.getElementById("log-path");
    if (!el) return;
    try { const p = await invoke("get_log_path_cmd"); el.textContent = p; el.title = p; }
    catch(e) { el.textContent = "(获取失败: " + e + ")"; }
  })();
  loadMeetings();
}

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById("auth-email").value.trim();
  const password = document.getElementById("auth-password").value;
  const errEl = document.getElementById("auth-error");
  errEl.textContent = "";
  if (!email || !password) { errEl.textContent = "请填写邮箱和密码"; return; }
  try {
    const gpu = await getGpuUrl();
    const resp = await fetch(gpu + "/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (resp.ok) {
      setAuthToken(data.token, data.email);
      document.getElementById("auth-overlay").style.display = "none";
      initAfterAuth();
    } else { errEl.textContent = data.error || "登录失败"; }
  } catch(e) { errEl.textContent = "网络错误"; }
}

async function handleRegister(e) {
  e.preventDefault();
  const email = document.getElementById("auth-reg-email").value.trim();
  const password = document.getElementById("auth-reg-password").value;
  const errEl = document.getElementById("auth-error"); errEl.textContent = "";
  if (!email || !password) { errEl.textContent = "请填写邮箱和密码"; return; }
  if (password.length < 6) { errEl.textContent = "密码至少 6 位"; return; }
  try {
    const gpu = await getGpuUrl();
    const resp = await fetch(gpu + "/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (resp.ok) {
      setAuthToken(data.token, data.email);
      document.getElementById("auth-overlay").style.display = "none";
      initAfterAuth();
    } else { errEl.textContent = data.error || "注册失败"; }
  } catch(e) { errEl.textContent = "网络错误"; }
}

function showLoginForm() {
  document.getElementById("auth-login-form").style.display = "block";
  document.getElementById("auth-register-form").style.display = "none";
  document.getElementById("auth-error").textContent = "";
}
function showRegisterForm() {
  document.getElementById("auth-login-form").style.display = "none";
  document.getElementById("auth-register-form").style.display = "block";
  document.getElementById("auth-error").textContent = "";
}

// === 状态 ===
let recording = false;
let segCount = 0;
let byteCount = 0;
let upCount = 0;
let currentMeetingId = null;
let docsByKind = {};
const renderedChatIds = new Set();

let latencyTimer = null;
let latencyStartMs = 0;
function startLatencyTicker() {
  if (latencyTimer) clearInterval(latencyTimer);
  latencyStartMs = Date.now();
  const el = document.getElementById("latency");
  if (!el) return;
  el.textContent = `已采集 0.0s`;
  latencyTimer = setInterval(() => {
    const elapsed = (Date.now() - latencyStartMs) / 1000;
    el.textContent = `已采集 ${elapsed.toFixed(1)}s`;
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
    idle: "录音就绪", capturing: "采集中...", stopped: "已停止", noResult: "无结果",
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
// 2026-06-28: 优先级 — localStorage (用户运行时改) > invoke get_gpu_url (Rust 读 yaml) > yaml 默认值
async function getGpuUrl() {
  // localStorage 优先: 用户在设置页改过, 重启后保持 (Rust 启动不读 localStorage, 仅写回 yaml)
  const ls = localStorage.getItem("vpbuddy-gpu-url");
  if (ls) return ls;
  try {
    return await invoke("get_gpu_url");
  } catch (_) {
    // 2026-07-03 ADR-0039: 内置默认值 = 公网 GPU server (47.100.182.3:28765)
    // 之前是 http://gpu.zhangshengdong.com:8765 (LAN IPv6-only 域名, V 家网解析失败, ADR-0036)
    return "http://47.100.182.3:28765";
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
    // 2026-07-01 ADR-0022: 必须先选/输入会议, 客户端校验 (服务端二次校验)
    const mid = resolveMeetingId();
    if (!mid) {
      status.textContent = "❌ 请先选择或输入会议";
      return;
    }
    btn.disabled = true;
    try {
      const e = document.getElementById("audio-device").value || null;
      const sourceKind = document.getElementById("audio-source-kind").value || "microphone";
      currentMeetingId = await invoke("start_realtime_capture", {
        audioDevice: e,
        meetingId: mid,
        audioSource: sourceKind,
        authToken: getAuthToken(),
      });
      recording = true;
      startLatencyTicker();
      btn.dataset.state = "recording";
      btn.textContent = "停止录音";
      dot.className = "dot live";
      status.textContent = t("capturing");
      btn.disabled = false;
      // 显示结束会议按钮 (ADR-0022)
      const endBtn = document.getElementById("btn-end-meeting");
      if (endBtn) endBtn.style.display = "";
      // 2026-06-27: 不再调 refreshDocs — 内容由 SSE doc-status 自动推流
      await refreshChatHistory();
      // 2026-07-01 ADR-0024: 录音中可能已生成 demo, 加载版本列表
      loadDemoVersions();
      // 2026-07-01 ADR-0028 Commit 4: 拉 collab 全量 (initial state)
      refreshCollab();
    } catch (e) {
      status.textContent = "❌ " + e;
      btn.disabled = false;
    }
  } else {
    // 停止录音
    btn.disabled = true;
    recording = false;
    stopLatencyTicker();
    btn.dataset.state = "idle";
    btn.textContent = "开始录音";
    btn.disabled = false;
    dot.className = "dot";
    status.textContent = t("stopped");
    try {
      await invoke("stop_capture");
    } catch (e) {
      status.textContent = "❌ " + e;
    }
  }
});

// === 2026-07-01 ADR-0022: 首页会议选择 / 输入 / 校验 / 按钮启用 ===
const MEETING_ID_RE = /^[A-Za-z0-9_-]+$/;  // 跟服务端 _validate_meeting_id 一致

function resolveMeetingId() {
  const sel = document.getElementById("meeting-select");
  const input = document.getElementById("meeting-new");
  const selVal = sel && sel.value ? sel.value.trim() : "";
  const inputVal = input && input.value ? input.value.trim() : "";
  if (inputVal) {
    // 输入框优先 (新会议)
    if (!MEETING_ID_RE.test(inputVal)) {
      alert("会议名只能含字母数字下划线连字符, 无空格/中文");
      input.focus();
      return null;
    }
    if (inputVal.length < 3 || inputVal.length > 48) {
      alert("会议名长度需 3-48 字符");
      input.focus();
      return null;
    }
    return inputVal;
  }
  if (selVal) return selVal;
  return null;
}

function updateRecBtnState() {
  const btn = document.getElementById("btn-rec");
  const sel = document.getElementById("meeting-select");
  const input = document.getElementById("meeting-new");
  if (!btn || btn.dataset.state !== "idle") return;  // 录音中不变
  const selVal = sel && sel.value ? sel.value.trim() : "";
  const inputVal = input && input.value ? input.value.trim() : "";
  if (selVal || inputVal) {
    btn.disabled = false;
    btn.title = "";
  } else {
    btn.disabled = true;
    btn.title = "请先选择已有会议或输入新会议名";
  }
}

async function loadMeetings() {
  const sel = document.getElementById("meeting-select");
  if (!sel) return;
  const gpu = await getGpuUrl();
  try {
    const r = await fetchWithAuth(`${gpu}/api/meetings`);
    const data = await r.json();
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">— 选择已有会议 —</option>' +
      data.meetings.map(m =>
        `<option value="${m.meeting_id}">${m.meeting_id}${m.audio_source && m.audio_source !== "microphone" ? " · " + m.audio_source : ""} · ${m.last_updated ? m.last_updated.slice(0, 16).replace("T", " ") : ""}</option>`
      ).join("");
    sel.value = currentVal;  // 保留用户之前选的值 (刷新后)
  } catch (e) {
    console.warn("loadMeetings 失败:", e);
  }
}

// 输入框输入时清空下拉 (避免冲突), 反之亦然
document.getElementById("meeting-new")?.addEventListener("input", () => {
  const sel = document.getElementById("meeting-select");
  if (sel && document.getElementById("meeting-new").value) sel.value = "";
  updateRecBtnState();
});
document.getElementById("meeting-select")?.addEventListener("change", () => {
  const input = document.getElementById("meeting-new");
  if (input && document.getElementById("meeting-select").value) input.value = "";
  updateRecBtnState();
});

// === 2026-07-01 ADR-0022: 结束会议按钮 ===
document.getElementById("btn-end-meeting")?.addEventListener("click", async () => {
  if (!currentMeetingId) return;
  if (!confirm(`确定结束会议 "${currentMeetingId}"?\n会议结束后无法继续录音.`)) return;
  try {
    // 先停录音
    await invoke("stop_capture");
    // 再调服务端 close
    const gpu = await getGpuUrl();
    const r = await fetchWithAuth(`${gpu}/api/meetings/${currentMeetingId}/close`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    status_recording_update("会议已结束");
    currentMeetingId = null;
    await loadMeetings();  // 刷新列表
    // 重置 UI
    const endBtn = document.getElementById("btn-end-meeting");
    if (endBtn) endBtn.style.display = "none";
    const btn = document.getElementById("btn-rec");
    if (btn) {
      btn.dataset.state = "idle";
      btn.textContent = "开始录音";
    }
  } catch (e) {
    status_recording_update("❌ 结束会议失败: " + e);
  }
});

function status_recording_update(msg) {
  const dot = document.getElementById("rec-dot");
  const status = document.getElementById("rec-status");
  if (status) status.textContent = msg;
  if (dot && msg.includes("结束")) dot.className = "dot";
}

// === 2026-07-01 ADR-0024: demo 版本切换 ===
// 2026-07-03 v0.8.4: 修复用户反馈"demo 版本列表延迟 + 选择不跳转"
// 根因: doc-status SSE kind=demo 自动 frame.srcdoc = content 覆盖了用户选择的老版本
let demoVersions = [];  // [{version, created_at, summary, file_size}, ...]
let selectedDemoVersion = null;  // 用户主动选中的版本 (null = 跟最新自动同步)

async function loadDemoVersions() {
  if (!currentMeetingId) return;
  const gpu = await getGpuUrl();
  try {
    const r = await fetchWithAuth(`${gpu}/api/meetings/${currentMeetingId}/demo/versions`);
    if (!r.ok) return;
    const data = await r.json();
    demoVersions = data.versions || [];
    renderDemoVersionSelect();
    if (demoVersions.length > 0) {
      // 切换会议 → reset 回最新
      const latest = demoVersions[0];  // 倒序, [0] 是最新
      selectedDemoVersion = latest.version;
      loadDemoVersion(latest.version);
      const sel = document.getElementById("demo-version-select");
      if (sel) sel.value = String(latest.version);
    } else {
      selectedDemoVersion = null;
    }
  } catch (e) {
    console.warn("loadDemoVersions 失败:", e);
  }
}

function renderDemoVersionSelect() {
  const sel = document.getElementById("demo-version-select");
  if (!sel) return;
  if (demoVersions.length === 0) {
    sel.innerHTML = '<option value="">— 暂无版本 —</option>';
    document.getElementById("demo-latest-btn").disabled = true;
    return;
  }
  sel.innerHTML = demoVersions.map(v =>
    `<option value="${v.version}">v${v.version} · ${v.summary || "(无描述)"} · ${formatTs(v.created_at)}</option>`
  ).join("");
  document.getElementById("demo-latest-btn").disabled = false;
}

function formatTs(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function loadDemoVersion(version) {
  if (!currentMeetingId) return;
  const gpu = await getGpuUrl();
  // 直接 fetch HTML 内容 (绕开 SSE 自动路径), 写到 iframe.srcdoc
  const url = `${gpu}/docs/${currentMeetingId}/demo_v${version}.html`;
  try {
    const r = await fetchWithAuth(url, { cache: "no-store" });
    if (!r.ok) {
      console.warn("loadDemoVersion HTTP", r.status, url);
      return;
    }
    const html = await r.text();
    const frame = document.getElementById("demo-iframe");
    if (frame) frame.srcdoc = html;
  } catch (e) {
    console.warn("loadDemoVersion fetch fail:", e);
  }
  selectedDemoVersion = version;
  // 更新 info
  const v = demoVersions.find(x => x.version === version);
  const info = document.getElementById("demo-version-info");
  if (info && v) {
    info.textContent = `v${v.version} · 大小: ${(v.file_size / 1024).toFixed(1)} KB`;
  }
}

document.getElementById("demo-version-select")?.addEventListener("change", (e) => {
  const v = parseInt(e.target.value, 10);
  if (!isNaN(v)) loadDemoVersion(v);
});

document.getElementById("demo-latest-btn")?.addEventListener("click", () => {
  if (demoVersions.length === 0) return;
  const latest = demoVersions[0];
  const sel = document.getElementById("demo-version-select");
  if (sel) sel.value = String(latest.version);
  loadDemoVersion(latest.version);
});

// === 监听 Tauri 后端事件 ===
// 2026-06-27 加强: 时间戳 + 说话人分色块 + 自动滚动到顶 + 新增提示动画
// ── 实时 ASR 去重: 同 begin_time 的行更新而非新增 ──
const _asrDedup = new Map();  // key: "startSec|spkId", value: DOM element

listen("transcript-segment", (e) => {
  const seg = e.payload;
  if (latencyTimer) {
    latencyStartMs = Date.now();
    document.getElementById("latency").textContent = `已采集 0.0s (出字中)`;
  }
  segCount += 1;
  const list = document.getElementById("stream-list");

  // 两种模式:
  // A) cleaned 多行 (HTTP chunk): seg.cleaned==true, text 可能含 \n
  // B) WS 实时 (百炼): 单行, is_sentence_end 标记完整性
  const isCleaned = seg.cleaned === true;
  const lines = isCleaned
    ? (seg.text || "").split("\n").filter(l => l.trim())
    : [seg.text || ""];

  for (const line of lines) {
    let timeStr = "", spkId = seg.speaker_id || "SPEAKER_00", text = line;
    // 旧版 cleaned 行: [MM:SS] SPEAKER: text
    const m = line.match(/^\[(\d+:\d+[\.\d]*)\]\s*(SPEAKER_\w+|UNKNOWN):\s*(.*)/);
    if (m) { timeStr = m[1]; spkId = m[2]; text = m[3]; }
    else {
      const startSec = seg.start_sec || 0;
      timeStr = `${String(Math.floor(startSec/60)).padStart(2,"0")}:${(startSec%60).toFixed(1).padStart(4,"0")}`;
    }

    // 实时模式去重: 同时间+说话人的行原地更新
    const dedupKey = `${seg.start_sec?.toFixed?.(1) || "0.0"}|${spkId}`;
    let item = isCleaned ? null : _asrDedup.get(dedupKey);

    if (item) {
      const textSpan = item.querySelector(".text");
      if (textSpan) textSpan.textContent = text;
      if (seg.is_sentence_end) { item.classList.add("stream-item-final"); _asrDedup.delete(dedupKey); }
      item.classList.add("stream-item-fresh");
      setTimeout(() => item.classList.remove("stream-item-fresh"), 600);
    } else {
      const colorIdx = parseInt(spkId.slice(-2), 10) % 8;
      item = document.createElement("div");
      item.className = "stream-item";
      if (isCleaned) item.classList.add("stream-item-cleaned");
      if (seg.is_sentence_end) item.classList.add("stream-item-final");
      item.innerHTML =
        `<span class="time">${timeStr}</span>` +
        `<span class="spk spk-${colorIdx}">${escapeHtml(spkId)}</span>` +
        ` <span class="text">${escapeHtml(text)}</span>`;
      list.insertBefore(item, list.firstChild);
      item.classList.add("stream-item-fresh");
      setTimeout(() => item.classList.remove("stream-item-fresh"), 600);
      if (!isCleaned && !seg.is_sentence_end) _asrDedup.set(dedupKey, item);
    }
  }

  document.getElementById("seg-count").textContent = `${segCount} 段 (${_asrDedup.size} 进行中)`;
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
    // 2026-07-03 v0.8.4: demo 自动同步 latest only (用户主动选老版本时不动 iframe)
    if (kind === "demo" && is_demo) {
      const v = demoVersions[0]?.version;
      if (selectedDemoVersion != null && selectedDemoVersion !== v) {
        return;
      }
      selectedDemoVersion = v;
      const frame = document.getElementById("demo-iframe");
      if (frame) frame.srcdoc = content;
      const sel = document.getElementById("demo-version-select");
      if (sel && v != null) sel.value = String(v);
      return;
    }
    const body = block?.querySelector(".doc-body");
    if (body) body.textContent = content;
  } else if (kind && docState === "stored" && !content) {
    fetchDocContent(kind, is_demo);
  }
});

// v0.22.6: SSE doc-update 不再含 content, 客户端按需 GET
async function fetchDocContent(kind, is_demo) {
  if (kind === "demo") return;
  try {
    const gpu = await getGpuUrl();
    const resp = await fetchWithAuth(`${gpu}/api/meetings/${encodeURIComponent(currentMeetingId)}/docs/${encodeURIComponent(kind)}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const content = data.content || "";
    docsByKind[kind] = { kind, status: "stored", content, is_demo };
    const block = document.querySelector(`.doc-block[data-kind="${kind}"]`);
    const body = block?.querySelector(".doc-body");
    if (body) body.textContent = content;
  } catch (_) {}
}

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
  // v0.22.9: 如果正在录音中，忽略 meeting-complete
  // (可能是 SSE 事件重放或服务端旧线程)
  if (recording) {
    console.log("meeting-complete 忽略 — 当前正在录音中");
    return;
  }
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

// v0.22.5: demo-new-version 事件 — 重新拉取版本列表
listen("demo-new-version", (e) => {
  console.log("demo-new-version:", e.payload);
  loadDemoVersions();
});

// 2026-07-01 ADR-0028 Commit 4: SSE `collab-update` 推流 — 实时刷新疑问面板
// payload: { action: "ask"|"answer", qid, section, question, answer, status, asker, answerer }
// 注: ask_question/answer_question 端点用 asker/answerer, 但 GET /collab 返 asked_by/answered_by
listen("collab-update", (e) => {  const p = e.payload || {};
  if (p.action === "ask") {
    upsertPendingQuestion({
      qid: p.qid,
      section: p.section,
      question: p.question,
      asker: p.asker || "agent",
      status: p.status || "pending",
    });
    bumpPendingCount(+1);
    const panel = document.getElementById("collab-panel");
    if (panel) panel.open = true;
  } else if (p.action === "answer") {
    movePendingToAnswered({
      qid: p.qid,
      answer: p.answer,
      answerer: p.answerer,
    });
    bumpPendingCount(-1);
  }
});

// === 协作疑问面板 (ADR-0028 Commit 4) ===
// 状态: pendingQuestions + answeredQuestions 各是 Map<qid, item>
// 初始: 切会议时调 GET /api/meetings/{id}/collab 拉全量
let pendingQuestions = new Map();
let answeredQuestions = new Map();
let pendingCountDelta = 0; // SSE 增量计数 (初始拉全量时不用, 后续用 delta)

function upsertPendingQuestion(item) {
  pendingQuestions.set(item.qid, item);
  renderCollabPanel();
}

function movePendingToAnswered({ qid, answer, answerer }) {
  const p = pendingQuestions.get(qid);
  if (p) {
    answeredQuestions.set(qid, { ...p, answer, answerer, status: "answered" });
    pendingQuestions.delete(qid);
  } else {
    // 服务端推 answer 但本地 pending 里没有 (可能切会议后才有推送) → 占位条目
    answeredQuestions.set(qid, {
      qid, answer, answerer, status: "answered",
      section: "?", question: "(略)", asker: "?",
    });
  }
  renderCollabPanel();
}

function bumpPendingCount(delta) {
  pendingCountDelta += delta;
  renderCollabBadge();
}

function renderCollabBadge() {
  const badge = document.getElementById("collab-pending-count");
  const info = document.getElementById("collab-collapsed-info");
  const n = pendingQuestions.size + pendingCountDelta;
  if (n > 0) {
    badge.textContent = n;
    badge.style.display = "";
    info.textContent = `有 ${n} 个待答疑问`;
  } else {
    badge.style.display = "none";
    info.textContent = "";
  }
}

function renderCollabPanel() {
  const pendingWrap = document.getElementById("collab-pending");
  const answeredWrap = document.getElementById("collab-answered");
  const answeredCount = document.getElementById("collab-answered-count");

  // pending
  const pendingList = Array.from(pendingQuestions.values());
  if (pendingList.length === 0) {
    pendingWrap.innerHTML = `<div class="collab-empty">暂无待答疑问。Agent 提问后会出现在这里。</div>`;
  } else {
    pendingWrap.innerHTML = pendingList.map((q) => renderPendingItem(q)).join("");
    // 绑定 [回答] 按钮
    pendingWrap.querySelectorAll(".collab-answer-btn").forEach((btn) => {
      btn.addEventListener("click", () => showAnswerInline(btn.dataset.qid));
    });
    pendingWrap.querySelectorAll(".collab-answer-send").forEach((btn) => {
      btn.addEventListener("click", () => submitAnswer(btn.dataset.qid));
    });
    pendingWrap.querySelectorAll(".collab-answer-cancel").forEach((btn) => {
      btn.addEventListener("click", () => {
        const item = btn.closest(".collab-q-item");
        if (item) item.querySelector(".collab-answer-form").style.display = "none";
      });
    });
  }

  // answered
  const answeredList = Array.from(answeredQuestions.values()).reverse(); // 最新在前
  answeredCount.textContent = answeredList.length;
  answeredWrap.innerHTML = answeredList.length === 0
    ? `<div class="collab-empty">尚无已回答条目</div>`
    : answeredList.map((q) => renderAnsweredItem(q)).join("");

  renderCollabBadge();
}

function renderPendingItem(q) {
  const asker = q.asked_by || q.asker || "agent";
  return `<div class="collab-q-item" data-qid="${escapeHtml(q.qid)}">
    <div class="collab-q-head">
      <span class="collab-q-section">${escapeHtml(q.section || "?")}</span>
      <span class="collab-q-asker">${escapeHtml(asker)}</span>
      <button type="button" class="collab-answer-btn" data-qid="${escapeHtml(q.qid)}">回答</button>
    </div>
    <div class="collab-q-body">${escapeHtml(q.question)}</div>
    <div class="collab-answer-form" style="display:none;">
      <textarea placeholder="输入回答 (回车提交, Shift+回车换行)" rows="2"></textarea>
      <div class="collab-answer-actions">
        <button type="button" class="collab-answer-send" data-qid="${escapeHtml(q.qid)}">发送</button>
        <button type="button" class="collab-answer-cancel" data-qid="${escapeHtml(q.qid)}">取消</button>
      </div>
    </div>
  </div>`;
}

function renderAnsweredItem(q) {
  // 2026-07-01: GET /collab 返 asked_by/answered_by (markdown 解析), SSE 推 asker/answerer (端点参数)
  // 这里兼容两种来源
  const asker = q.asked_by || q.asker || "agent";
  const answerer = q.answered_by || q.answerer || "VP";
  return `<div class="collab-q-item answered" data-qid="${escapeHtml(q.qid)}">
    <div class="collab-q-head">
      <span class="collab-q-section">${escapeHtml(q.section || "?")}</span>
      <span class="collab-q-asker">问: ${escapeHtml(asker)}</span>
      <span class="collab-q-answerer">答: ${escapeHtml(answerer)}</span>
    </div>
    <div class="collab-q-body">${escapeHtml(q.question)}</div>
    <div class="collab-q-answer">→ ${escapeHtml(q.answer || "")}</div>
  </div>`;
}

function showAnswerInline(qid) {
  const item = pendingWrap_for(qid);
  if (!item) return;
  const form = item.querySelector(".collab-answer-form");
  form.style.display = "";
  const ta = form.querySelector("textarea");
  ta.focus();
  ta.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitAnswer(qid); }
  };
}

function pendingWrap_for(qid) {
  return document.querySelector(`#collab-pending .collab-q-item[data-qid="${CSS.escape(qid)}"]`);
}

async function submitAnswer(qid) {
  if (!currentMeetingId) return;
  const item = pendingWrap_for(qid);
  if (!item) return;
  const ta = item.querySelector(".collab-answer-form textarea");
  const answer = (ta.value || "").trim();
  if (!answer) return;
  try {
    const gpuUrlLocal = await getGpuUrl();
    const url = `${gpuUrlLocal}/api/meetings/${encodeURIComponent(currentMeetingId)}/answer_question?qid=${encodeURIComponent(qid)}&answer=${encodeURIComponent(answer)}&answerer=VP`;
    const resp = await fetchWithAuth(url, { method: "POST" });
    const result = await resp.json();
    if (!resp.ok || !result.ok) throw new Error(result.error || "提交失败");
    // SSE 会推 collab-update, 不必本地立即改 — 但清空输入框 + 隐藏表单
    ta.value = "";
    item.querySelector(".collab-answer-form").style.display = "none";
  } catch (e) {
    alert("回答失败: " + e);
  }
}

async function refreshCollab() {
  if (!currentMeetingId) return;
  try {
    const gpuUrlLocal = await getGpuUrl();
    const url = `${gpuUrlLocal}/api/meetings/${encodeURIComponent(currentMeetingId)}/collab`;
    const resp = await fetchWithAuth(url);
    if (!resp.ok) return;
    const data = await resp.json();
    pendingQuestions = new Map((data.pending || []).map((q) => [q.qid, q]));
    answeredQuestions = new Map((data.answered || []).map((q) => [q.qid, q]));
    pendingCountDelta = 0; // 拉全量后, delta 重新开始
    renderCollabPanel();
  } catch (e) {
    console.warn("拉取 collab 失败", e);
  }
}

document.getElementById("collab-ask-btn").addEventListener("click", async () => {
  if (!currentMeetingId) {
    alert("请先开始会议");
    return;
  }
  const section = document.getElementById("collab-section").value;
  const qInput = document.getElementById("collab-q-input");
  const question = (qInput.value || "").trim();
  if (!question) return;
  try {
    const gpuUrlLocal = await getGpuUrl();
    const url = `${gpuUrlLocal}/api/meetings/${encodeURIComponent(currentMeetingId)}/ask_question?section=${encodeURIComponent(section)}&question=${encodeURIComponent(question)}&asker=VP`;
    const resp = await fetchWithAuth(url, { method: "POST" });
    const result = await resp.json();
    if (!resp.ok || !result.ok) throw new Error(result.error || "提问失败");
    qInput.value = "";
    // SSE 会推 collab-update, 不用本地立即加
  } catch (e) {
    alert("提问失败: " + e);
  }
});

document.getElementById("collab-q-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); document.getElementById("collab-ask-btn").click(); }
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
  // 旧 5 类 facts 已废弃 (v0.12.0)，客户端不再显示
  // 保持 listener 兼容旧 payload
  if (stats.cleaned_text_length != null) {
    const segEl = document.getElementById("seg-count");
    if (segEl) segEl.textContent = `${stats.cleaned_text_length} 字清理`;
  }
});

// 2026-06-27: 6 文档改并列展示, 删除 click 切换 + refreshDocs + renderDoc 单 viewer
// 内容全部由 SSE "doc-status" 推流自动写入对应 .doc-block

listen("error", (e) => {
  document.getElementById("rec-status").textContent = "❌ " + e.payload;
  document.getElementById("rec-dot").className = "dot err";
  recording = false;
  stopLatencyTicker();
  const btn = document.getElementById("btn-rec");
  if (btn) {
    btn.dataset.state = "idle";
    btn.textContent = "开始录音";
    btn.disabled = false;
  }
});

// === KB 检索 ===
// 2026-07-03 v0.8.4: 兼容 rag 返回 {score, text, metadata} 字段; 缺失字段降级显示
async function kbSearch() {
  const q = document.getElementById("kb-q").value.trim();
  if (!q) return;
  const gpuUrlLocal = await getGpuUrl();
  let results;
  try {
    const r = await fetchWithAuth(`${gpuUrlLocal}/api/kb/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, top_k: 5, meeting_id: currentMeetingId || null, scope: currentMeetingId ? "current" : "all" }),
    });
    const data = await r.json();
    results = data.results || [];
  } catch (e) {
    document.getElementById("kb-results").innerHTML = `<div style='color:var(--err);padding:8px;'>检索失败: ${escapeHtml(String(e))}</div>`;
    return;
  }
  if (results.length === 0) {
    document.getElementById("kb-results").innerHTML = `<div style='color:var(--text2);padding:20px;'>${t("noResult")}</div>`;
    return;
  }
  const html = results.map((r, i) => {
    const meta = r.metadata || {};
    const dist = r.distance ?? r.score ?? null;
    const distTxt = dist != null ? Number(dist).toFixed(3) : "-";
    const snippet = r.snippet ?? r.text ?? r.document ?? "(无内容)";
    const badge = `${meta.meeting_id || "?"}/${meta.doc_kind || meta.source || "kb"}`;
    return `
    <div class="kb-result">
      <div class="head">
        <span class="badge">${i+1}</span>
        <span>${escapeHtml(badge)}</span>
        <span>dist=${distTxt}</span>
      </div>
      <div>${escapeHtml(snippet).slice(0, 200)}</div>
    </div>
  `;
  }).join("");
  document.getElementById("kb-results").innerHTML = html;
}
document.getElementById("kb-btn").addEventListener("click", kbSearch);
document.getElementById("kb-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") kbSearch();
});

// 2026-07-03 v0.8.4: KB 上传按钮 (走 /api/kb/upload multipart)
async function kbUpload() {
  if (!currentMeetingId) {
    document.getElementById("kb-upload-status").textContent = "请先开始会议";
    return;
  }
  const fileInput = document.getElementById("kb-file");
  const f = fileInput.files[0];
  if (!f) {
    document.getElementById("kb-upload-status").textContent = "请选择 .txt / .md / .pdf 文件";
    return;
  }
  const statusEl = document.getElementById("kb-upload-status");
  statusEl.textContent = `上传中: ${f.name} (${Math.round(f.size/1024)}KB)`;
  try {
    const gpuUrlLocal = await getGpuUrl();
    const fd = new FormData();
    fd.append("meeting_id", currentMeetingId);
    fd.append("file", f, f.name);
    const r = await fetchWithAuth(`${gpuUrlLocal}/api/kb/upload`, { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "upload kb 失败");
    statusEl.textContent = `✓ 上传成功: ${data.filename || f.name} → doc_id=${data.doc_id || "?"} (${data.chars || data.char_count || 0} chars)`;
    fileInput.value = "";
  } catch (e) {
    statusEl.textContent = "❌ 上传失败: " + e.message;
  }
}
document.getElementById("kb-upload-btn").addEventListener("click", kbUpload);
// 会议开始后启用上传按钮
// v0.8.4 (ADR-0037): applyMeeting 是 Tauri 注入的全局函数, 在 vite preview / e2e stub
// 场景下可能没定义. 用 typeof check 避免 ReferenceError, fallback 到 no-op.
const _origApplyMeeting = typeof applyMeeting !== "undefined" ? applyMeeting : () => {};
function applyMeetingEnableKbUpload() {
  const btn = document.getElementById("kb-upload-btn");
  if (btn) btn.disabled = !currentMeetingId;
}
// 在 toggle 流程末尾 hook (用 MutationObserver 监听 currentMeetingId 变化成本太高, 直接定时扫)
setInterval(applyMeetingEnableKbUpload, 1000);

// === VP Chat ===
// 2026-07-01 ADR-0023: 附件状态 + 选/删/预览 + fetch multipart 直发
const chatAttachments = []; // [{ file: File, previewUrl?: string }]

function renderChatAttachments() {
  const wrap = document.getElementById("chat-attachments");
  if (!wrap) return;
  if (chatAttachments.length === 0) { wrap.innerHTML = ""; return; }
  wrap.innerHTML = chatAttachments.map((a, i) => {
    const isImg = a.file.type.startsWith("image/");
    const sizeKb = Math.round(a.file.size / 1024);
    const thumb = isImg && a.previewUrl
      ? `<img src="${a.previewUrl}" class="chat-attach-thumb" />`
      : `<span class="chat-attach-icon">${isImg ? "🖼️" : "📄"}</span>`;
    return `<div class="chat-attach-chip">
      ${thumb}
      <span class="chat-attach-name" title="${escapeHtml(a.file.name)}">${escapeHtml(a.file.name)}</span>
      <span class="chat-attach-size">${sizeKb}KB</span>
      <button type="button" class="chat-attach-rm" data-idx="${i}" title="移除">×</button>
    </div>`;
  }).join("");
  // 删按钮
  wrap.querySelectorAll(".chat-attach-rm").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = parseInt(btn.dataset.idx, 10);
      const a = chatAttachments[i];
      if (a && a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      chatAttachments.splice(i, 1);
      renderChatAttachments();
    });
  });
}

document.getElementById("chat-attach").addEventListener("click", () => {
  document.getElementById("chat-file").click();
});
document.getElementById("chat-file").addEventListener("change", (e) => {
  const files = Array.from(e.target.files || []);
  for (const f of files) {
    const isImg = f.type.startsWith("image/");
    chatAttachments.push({
      file: f,
      previewUrl: isImg ? URL.createObjectURL(f) : undefined,
    });
  }
  e.target.value = ""; // 清空, 允许同文件再次选
  renderChatAttachments();
});

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
  // 至少要有文本或附件
  if (!message && chatAttachments.length === 0) return;
  const filesSnapshot = chatAttachments.slice();
  input.value = "";
  document.getElementById("chat-status").textContent = chatAttachments.length
    ? `上传 ${chatAttachments.length} 个附件 + 问 Hermes...`
    : "Hermes 正在思考...";

  try {
    let result;
    if (filesSnapshot.length > 0) {
      // 2026-07-01 ADR-0023 Phase 6: multipart 直发, 不走 Rust invoke
      // (reqwest multipart 拼接在 Rust 里要再 base64 一遍, webview fetch 直发更短)
      const gpuUrlLocal = await getGpuUrl();
      const fd = new FormData();
      fd.append("text", message);
      for (const a of filesSnapshot) fd.append("files", a.file, a.file.name);
      const url = gpuUrlLocal + "/api/meetings/" + encodeURIComponent(currentMeetingId) + "/chat";
      const resp = await fetchWithAuth(url, { method: "POST", body: fd });
      result = await resp.json();
      if (!resp.ok) throw new Error(result.error || "upload chat 失败");
    } else {
      // 2026-06-26: 走 invoke (Rust reqwest), 不再 webview fetch
      result = await invoke("post_meeting_chat", {
        meetingId: currentMeetingId,
        message,
        authToken: getAuthToken(),
        context: {
          active_panel: document.querySelector(".bottom-nav button.active")?.dataset.panel || "chat",
          selected_doc_kind: document.querySelector(".doc-block.stored")?.dataset.kind || null,
        },
      });
    }
    if (result.user_message) renderChatMessage(result.user_message);
    if (result.assistant_message) {
      renderChatMessage(result.assistant_message);
      // 2026-07-03 v0.8.4: assistant_message.content 空时给显式提示 (LLM 失败时常见)
      if (!result.assistant_message.content) {
        const status = document.getElementById("chat-status");
        const errMsg = result.error ? ` (${result.error})` : "";
        status.textContent = `⚠️ 服务器没返答${errMsg}; 输入已记录到 chat 历史`;
      }
    }
    // 清附件 + 释放 preview URL
    for (const a of chatAttachments) if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    chatAttachments.length = 0;
    renderChatAttachments();
    document.getElementById("chat-status").textContent = "Hermes 已回复";
  } catch (e) {
    document.getElementById("chat-status").textContent = "Chat 失败：" + e;
  }
}

async function refreshChatHistory() {
  if (!currentMeetingId) return;
  try {
    // 2026-06-26: 走 invoke (Rust reqwest), 不再 webview fetch
    const result = await invoke("fetch_meeting_chat_history", { meetingId: currentMeetingId, authToken: getAuthToken() });
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
  // 2026-07-03 v0.8.4: 主动消息 (is_proactive=True) 不再当 user-q, 改为 '💡 提示' (灰色, 不抢眼)
  // 真问题走 collab panel (SSE collab-update), 不在这里
  const isProactive = !!msg.is_proactive;
  const proactiveClass = isProactive ? " proactive" : "";
  const role = msg.role === "user"
    ? "VP"
    : (isProactive ? "💡 VPBuddy 提示" : (msg.source === "hermes" ? "Hermes" : "VPBuddy"));
  // 主动消息前缀图标 + 灰色样式
  const iconPrefix = isProactive ? "💡 " : "";
  item.className = `chat-msg ${msg.role || "assistant"} ${msg.status || "ok"}${proactiveClass}`;
  // 附件 chip (用户上传时)
  const attachments = (msg.attachments && Array.isArray(msg.attachments)) ? msg.attachments : [];
  const attachHtml = attachments.length
    ? `<div class="chat-msg-attachs">${attachments.map((f) => {
        const isImg = (f.content_type || "").startsWith("image/") ||
                      /\.(png|jpe?g|gif|webp)$/i.test(f.filename || "");
        return `<span class="chat-msg-attach-chip">${isImg ? "🖼️" : "📄"} ${escapeHtml(f.filename || "?")}</span>`;
      }).join("")}</div>`
    : "";
  item.innerHTML = `
    <div class="chat-meta"><span>${role}</span><span>${escapeHtml(msg.created_at || "")}</span></div>
    <div class="chat-content">${iconPrefix}${escapeHtml(msg.content || "")}</div>
    ${attachHtml}
  `;
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;
}

// 2026-07-02 Phase 7 v0.8.0: 缓存所有设备, 按 audio-source-kind 动态 filter 渲染
//   - microphone: 只列 is_loopback=false (普通麦克风)
//   - loopback:   只列 is_loopback=true  (Linux .monitor / macOS BlackHole); 没设备 → 提示
//   - both:       列全部 (mic + loopback), 但 UI 提示默认行为 (v0.8.0 简化: 用默认 mic + 默认 loopback)
let allAudioDevices = [];

async function initAudioDevices() {
  try {
    const devices = await invoke("list_audio_devices");
    allAudioDevices = devices || [];
    renderAudioDevices();
  } catch (e) {
    console.warn("获取音频设备失败", e);
    const recStatus = document.getElementById("rec-status");
    if (recStatus) recStatus.textContent = "❌ 音频设备枚举失败: " + e;
  }
}

// 2026-07-02 Phase 7 v0.8.0: 按 audio-source-kind filter + 渲染 device dropdown
// 同步: 检测 macOS loopback 缺失 → 显示 "装 BlackHole" banner
function renderAudioDevices() {
  const select = document.getElementById("audio-device");
  const kind = document.getElementById("audio-source-kind").value || "microphone";
  // 清空现有 options (保留 placeholder)
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = kind === "loopback" ? "默认 loopback 设备" : "默认音频设备";
  select.appendChild(placeholder);

  if (allAudioDevices.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "⚠️ 未检测到任何输入设备";
    select.appendChild(opt);
    const recStatus = document.getElementById("rec-status");
    if (recStatus) recStatus.textContent = "⚠️ 无输入设备 — 检查 Windows 麦克风隐私设置";
    return;
  }

  // 按 kind filter
  const filtered = allAudioDevices.filter((d) => {
    if (kind === "microphone" || kind === "mic") return !d.is_loopback;
    if (kind === "loopback") return d.is_loopback;
    if (kind === "both") return true;  // both: 列全部 (UI 简化: 用默认)
    return !d.is_loopback;  // fallback
  });

  for (const d of filtered) {
    const opt = document.createElement("option");
    opt.value = d.id;
    const kindTag = d.is_loopback ? " 🔁" : " 🎤";
    opt.textContent = d.is_default ? `${d.name}（默认）${kindTag}` : `${d.name}${kindTag}`;
    select.appendChild(opt);
  }

  // 2026-07-03 v0.8.4: 实情显示 + 2026-07-04 P#e2e: fix ReferenceError banner is not defined
  const banner = document.getElementById("audio-warning");
  if (!banner) return;
  // Detect: 平台 + filter 结果里有没有 true loopback 设备
  const ua = navigator.userAgent || "";
  const isWin = /Windows/i.test(ua);
  const hasLoopback = filtered.some((d) => d.is_loopback);

  // Windows 永远 hasLoopback=false (Rust 端 is_loopback_device_name → 恒 false).
  // 此时 banner 就显示 "cpal 在当前客户端不暴露 WASAPI loopback; 当前 fallback 录 mic"
  if (kind === "loopback" || kind === "both") {
    if (isWin && !hasLoopback) {
      banner.style.display = "";
      banner.innerHTML =
        '🪟 Windows 内录 (系统声) 在当前客户端未支持 — cpal 抽象层不暴露 WASAPI loopback. ' +
        '当前 fallback 录麦克风, 系统声不进。' +
        '想录系统声: <strong>装虚拟声卡</strong> (e.g. <a href="https://vb-audio.com/Cable/" target="_blank">VB-Audio VoiceMeeter</a> / <a href="https://github.com/ExistentialAudio/BlackHole" target="_blank">BlackHole for Windows 移植</a>) 并在 Windows 声音设置把系统输出指向它。';
      return;
    }
  }
  // macOS + 没有 loopback 设备 → 提示装 BlackHole
  if (kind === "loopback" || kind === "both") {
    const isMac = /Mac/i.test(ua) && !/iPhone|iPad/.test(ua);
    if (isMac && !hasLoopback) {
      banner.style.display = "";
      banner.innerHTML =
        '🍎 macOS 未检测到 BlackHole — 内录需先装 <a href="https://github.com/ExistentialAudio/BlackHole/releases" target="_blank">BlackHole 2ch</a> ' +
        '(免费开源, 装完在「音频 MIDI 设置」设为输出即可)';
      return;
    }
  }
  // Linux: 正常 (.monitor 自带) — 无 banner
  banner.style.display = "none";
}

// 2026-07-02 Phase 7 v0.8.0: audio-source-kind 切换 → 重新 render device dropdown
document.getElementById("audio-source-kind").addEventListener("change", () => {
  renderAudioDevices();
});

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

// 2026-07-07 ADR-0047: 启动时先检查 token → 未登录则显示 auth overlay
// initAfterAuth() 在登录成功后或 token 验证通过后由 checkAuthAndInit() 调用
checkAuthAndInit();

// 2026-06-27: 设置页显示客户端日志路径 + 复制按钮
// (已移到 initAfterAuth, 这里只留事件绑定)

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
    setTimeout(() => { btn.textContent = "打开目录"; }, 2000);
  }
});

// 2026-06-28: 打开配置文件 (一键直达, 用户能直接 vim 改 ~/.vpbuddy-client.yaml)
document.getElementById("btn-open-config-dir")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-open-config-dir");
  if (!btn) return;
  try {
    await invoke("open_config_dir_cmd");
    btn.textContent = "✓ 已打开";
    setTimeout(() => { btn.textContent = "打开配置文件"; }, 2000);
  } catch (e) {
    btn.textContent = "❌ " + e;
    setTimeout(() => { btn.textContent = "打开配置文件"; }, 2000);
  }
});

