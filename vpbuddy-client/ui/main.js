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
    await invoke("start_capture", { autoUpload: document.getElementById("auto-upload").checked });
    recording = true;
    document.getElementById("btn-start").disabled = true;
    document.getElementById("btn-stop").disabled = false;
    document.getElementById("rec-dot").className = "dot live";
    document.getElementById("rec-status").textContent = "采集中...";
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
    document.getElementById("rec-status").textContent = "已停止";
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
  const { meeting_id, kind, state, count } = e.payload;
  const card = document.querySelector(`.doc-card[data-kind="${kind}"]`);
  if (card) {
    card.className = `doc-card ${state}`;
    card.querySelector(".doc-count").textContent = count || 0;
    card.querySelector(".doc-state").textContent = state === "stored" ? "✓ 已生成" : state === "queued" ? "..." : "✗";
  }
});

// 实时结构化事实更新 (REQ/GOAL/FEAT/RISK/QUE)
listen("state-update", (e) => {
  const stats = e.payload;
  const factsPanel = document.getElementById("panel-facts");
  if (factsPanel) {
    factsPanel.innerHTML = `
      <div class="fact-stats">
        <div class="fact-item"><span class="fact-label">需求 REQ</span><span class="fact-count">${stats.requirements || 0}</span></div>
        <div class="fact-item"><span class="fact-label">目标 GOAL</span><span class="fact-count">${stats.goals || 0}</span></div>
        <div class="fact-item"><span class="fact-label">功能 FEAT</span><span class="fact-count">${stats.features || 0}</span></div>
        <div class="fact-item"><span class="fact-label">风险 RISK</span><span class="fact-count">${stats.risks || 0}</span></div>
        <div class="fact-item"><span class="fact-label">问题 QUE</span><span class="fact-count">${stats.questions || 0}</span></div>
      </div>
    `;
  }
});

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
  document.getElementById("kb-results").innerHTML = html || "<div style='color:var(--text2);padding:20px;'>无结果</div>";
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
