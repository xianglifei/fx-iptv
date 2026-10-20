/* 联通电视 — 前端交互:频道墙、播放、机顶盒式数字选台 */
"use strict";

const $ = (id) => document.getElementById(id);
const wallEl = $("wall"), playerEl = $("player"), video = $("video");

let CHANNELS = [];
let current = null;          // 正在播放的频道对象
let activeTab = "全部";
let hls = null;
let osdTimer = null, digits = "", uiTimer = null, statusTimer = null;

/* ---------- 时钟与状态灯 ---------- */
function tickClock() {
  const d = new Date();
  $("clock").innerHTML =
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` +
    `<small>:${String(d.getSeconds()).padStart(2, "0")}</small>`;
}
setInterval(tickClock, 1000); tickClock();

/* ---------- 频道墙渲染 ---------- */
const LOGO_COLORS = ["#5b8cff,#8f6bff", "#ff8c66,#ff5e8a", "#2dd4bf,#5b8cff",
  "#f59e0b,#ef4444", "#a78bfa,#ec4899", "#34d399,#0ea5e9"];

function catCount(cat) {
  return cat === "全部" ? CHANNELS.length
    : CHANNELS.filter(c => c.cats.includes(cat)).length;
}

function renderTabs(tabs) {
  const list = ["全部", ...tabs];
  $("tabs").innerHTML = list.map(t =>
    `<span class="tab${t === activeTab ? " active" : ""}" data-tab="${t}">${t}<b>${catCount(t)}</b></span>`
  ).join("");
  $("tabs").querySelectorAll(".tab").forEach(el =>
    el.onclick = () => { activeTab = el.dataset.tab; renderTabs(tabs); renderGrid(); });
}

function visibleChannels() {
  return activeTab === "全部" ? CHANNELS
    : CHANNELS.filter(c => c.cats.includes(activeTab));
}

function renderGrid() {
  const chs = visibleChannels();
  $("ch-count").textContent = CHANNELS.length;
  $("grid").innerHTML = chs.map((c, i) => {
    const grad = LOGO_COLORS[c.num % LOGO_COLORS.length];
    const initial = c.name.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "").slice(0, 1) || "TV";
    return `
    <div class="card${current && current.num === c.num ? " playing" : ""}"
         data-num="${c.num}" style="animation-delay:${Math.min(i * 12, 400)}ms">
      <span class="num">${String(c.num).padStart(3, "0")}</span>
      <div class="logo">${c.logo
        ? `<img src="${c.logo}" loading="lazy" alt=""
               onerror="this.outerHTML='<div class=\\'fallback\\' style=\\'background:linear-gradient(135deg,${grad})\\'>${initial}</div>'">`
        : `<div class="fallback" style="background:linear-gradient(135deg,${grad})">${initial}</div>`}
      </div>
      <div class="name" title="${c.name}">${c.name}</div>
      <div class="meta"><span>${c.quality}</span><span>${c.cats[0] || ""}</span></div>
    </div>`;
  }).join("");
  $("grid").querySelectorAll(".card").forEach(el =>
    el.onclick = () => play(CHANNELS[el.dataset.num - 1]));
}

/* ---------- 播放 ---------- */
function setLoading(on, text) {
  $("loading").hidden = !on;
  if (text) $("loading-text").textContent = text;
}

function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.hidden = true, ms);
}

function updateNowBadge(ch) {
  $("now-num").textContent = String(ch.num).padStart(3, "0");
  $("now-name").textContent = ch.name;
  $("quality-badge").textContent = ch.quality;
}

function tryPlay() {
  video.play().catch(() => {
    // 浏览器自动播放策略:未静音的自动播放可能被拒,降级为静音播放
    video.muted = true;
    video.play().then(() =>
      toast("浏览器要求静音起播,点击画面可恢复声音", 4000)
    ).catch(() => {});
  });
}

function play(ch) {
  current = ch;
  wallEl.hidden = true;
  playerEl.hidden = false;
  playerEl.classList.remove("hide-ui");
  updateNowBadge(ch);
  setLoading(true, ch.cached
    ? `正在接入 ${ch.name}…`
    : `首次接入 ${ch.name},正在优化画质(约 5 秒)…`);
  document.title = `${ch.name} · 联通电视`;
  renderGrid();

  if (hls) { hls.destroy(); hls = null; }
  video.muted = false;
  const src = `/live/index.m3u8?ch=${ch.num}&t=${Date.now()}`;

  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({
      // 普通直播流(非 LL-HLS)不要开 lowLatencyMode,否则播放位置会来回追导致画面跳动
      lowLatencyMode: false,
      liveSyncDurationCount: 4,   // 保持约 4 秒缓冲,流畅优先
      maxBufferLength: 12,
      backBufferLength: 10,
      manifestLoadingTimeOut: 20000,
      manifestLoadingMaxRetry: 2,
    });
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => { ch.cached = true; setLoading(false); tryPlay(); });
    hls.on(Hls.Events.ERROR, (_, data) => {
      if (!data.fatal) return;
      if (data.response && data.response.code === 503) {
        setLoading(false);
        toast(`「${ch.name}」当前无信号,试试其他频道吧`);
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
      } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        hls.startLoad();
      } else {
        // 该频道可能是不被 MSE 支持的编码(如 4K HEVC),退回原生 HLS 播放器
        hls.destroy(); hls = null;
        fallbackNative(src, ch);
      }
    });
  } else {
    fallbackNative(src, ch);
  }

  clearTimeout(statusTimer);
  lastSignalToastNum = 0;
  pollStatus();
}

/* Safari 原生 HLS(视频直挂 src,支持 HEVC 4K) */
function fallbackNative(src, ch) {
  setLoading(true, ch.cached
    ? `正在接入 ${ch.name}…`
    : `首次接入 ${ch.name},正在优化画质(约 5 秒)…`);
  video.src = src;
  video.addEventListener("loadedmetadata", () => {
    ch.cached = true; setLoading(false); tryPlay();
  }, { once: true });
  video.addEventListener("error", () => {
    setLoading(false);
    toast(`「${ch.name}」当前无信号,试试其他频道吧`);
  }, { once: true });
}

let lastSignalToastNum = 0;

function pollStatus() {
  statusTimer = setTimeout(async () => {
    if (!current || playerEl.hidden) return;
    try {
      const st = await (await fetch("/api/status")).json();
      if (st.playing && st.channel === current.num && !st.signal
          && lastSignalToastNum !== current.num) {
        lastSignalToastNum = current.num;
        toast("信号中断,可按 ↑↓ 换个频道试试");
      }
    } catch (_) { $("live-dot").classList.add("off"); }
    pollStatus();
  }, 5000);
}

function backToWall() {
  playerEl.hidden = true;
  wallEl.hidden = false;
  document.title = "联通电视";
  if (hls) { hls.destroy(); hls = null; }
  video.pause(); video.removeAttribute("src"); video.load();
  clearTimeout(statusTimer);
  fetch("/api/stop").catch(() => {});       // 停掉后台 ffmpeg,省带宽
  current = null;
  refreshChannels();                        // 刷新各频道的"已优化"标记
}

/* ---------- 机顶盒式数字选台 ---------- */
function showOsd(text, isError = false) {
  const osd = $("osd");
  $("osd-text").textContent = text;
  osd.classList.toggle("error", isError);
  osd.hidden = false;
}

function zapByNumber() {
  const num = parseInt(digits, 10);
  digits = "";
  clearTimeout(osdTimer);
  const ch = CHANNELS[num - 1];
  if (ch) { play(ch); }
  else { showOsd(String(num), true); setTimeout(() => $("osd").hidden = true, 700); }
}

function handleDigit(d) {
  if (playerEl.hidden) return;
  digits += d;
  if (digits.length > 3) digits = d;
  showOsd(digits);
  clearTimeout(osdTimer);
  osdTimer = setTimeout(zapByNumber, 1200);
}

/* ---------- 键盘 ---------- */
document.addEventListener("keydown", (e) => {
  if (playerEl.hidden) return;
  if (e.key >= "0" && e.key <= "9") { handleDigit(e.key); return; }
  switch (e.key) {
    case "ArrowDown":
      if (current) play(CHANNELS[current.num % CHANNELS.length]);
      break;
    case "ArrowUp":
      if (current) play(CHANNELS[(current.num - 2 + CHANNELS.length) % CHANNELS.length]);
      break;
    case "Escape":
      backToWall();
      break;
    case "f": case "F":
      if (document.fullscreenElement) document.exitFullscreen();
      else playerEl.requestFullscreen?.();
      break;
  }
});

/* 鼠标静止 3 秒隐藏播放页控件(全屏观看体验) */
playerEl.addEventListener("mousemove", () => {
  playerEl.classList.remove("hide-ui");
  clearTimeout(uiTimer);
  uiTimer = setTimeout(() => playerEl.classList.add("hide-ui"), 3000);
});

$("btn-back").onclick = backToWall;

/* 点击画面:静音状态下恢复声音 */
video.addEventListener("click", () => {
  if (video.muted) {
    video.muted = false;
    video.play().catch(() => {});
  }
});

/* ---------- 启动 ---------- */
async function refreshChannels() {
  try {
    const data = await (await fetch("/api/channels")).json();
    CHANNELS = data.channels;
    renderTabs(data.tabs);
    renderGrid();
  } catch (_) {}
}

(async function init() {
  await refreshChannels();
  // 25 秒后刷新一次,吸收后台预探测的结果
  setTimeout(refreshChannels, 25000);
})();
