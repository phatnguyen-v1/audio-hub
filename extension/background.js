// Audio Hub browser extension: reports tabs playing sound to the desktop app
// (ws://127.0.0.1:8765/ext) and carries out its pause / mute / volume commands.

const HUB_URL = "ws://127.0.0.1:8765/ext";
const REPORT_EVERY_MS = 1500;

let ws = null;
let retryMs = 1000;
let reportTimer = null;
let tracked = new Set(); // tab ids that have played sound; kept until the tab closes

// ---- which browser is this ----------------------------------------------------
function browserKey() {
  const brands = (navigator.userAgentData?.brands || []).map(b => b.brand);
  if (brands.includes("Microsoft Edge")) return "msedge";
  if (brands.includes("Brave")) return "brave";
  if (brands.includes("Opera")) return "opera";
  return "chrome";
}

// ---- tracked tabs survive the service worker being restarted -------------------
const ready = chrome.storage.session.get("tracked").then(r => {
  tracked = new Set(r.tracked || []);
});
function saveTracked() {
  chrome.storage.session.set({ tracked: [...tracked] });
}

// ---- functions that run inside the page ----------------------------------------
function pageProbe() {
  return [...document.querySelectorAll("video, audio")]
    .filter(e => e.currentSrc || e.srcObject)
    .map(e => ({ paused: e.paused || e.ended, volume: e.volume, played: e.currentTime > 0, muted: e.muted }));
}

function pageSetPlaying(play) {
  let els = [...document.querySelectorAll("video, audio")].filter(e => e.currentSrc || e.srcObject);
  if (!play) { els.forEach(e => e.pause()); return; }
  if (els.some(e => !e.muted)) els = els.filter(e => !e.muted);
  const best = els.sort((a, b) => b.currentTime - a.currentTime)[0];
  if (best) best.play().catch(() => {});
}

function pageSetVolume(v) {
  window.__audioHubVolume = v;
  document.querySelectorAll("video, audio").forEach(e => { e.volume = v; });
  if (!window.__audioHubHooked) {
    window.__audioHubHooked = true;
    // Keep the chosen level for videos that start later (next video, playlists).
    document.addEventListener("play", e => {
      if (window.__audioHubVolume != null && "volume" in e.target) e.target.volume = window.__audioHubVolume;
    }, true);
  }
}

async function inPage(tabId, func, args = []) {
  try {
    const results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func, args });
    return results.map(r => r.result).filter(Boolean);
  } catch {
    return []; // chrome:// pages, the Web Store, discarded tabs...
  }
}

async function mediaState(tabId) {
  let els = (await inPage(tabId, pageProbe)).flat();
  // Ignore silent extras such as YouTube's muted hover previews when there's a real player.
  const audible = els.filter(e => !e.muted);
  if (audible.length) els = audible;
  if (!els.length) return { hasMedia: false };
  const playing = els.filter(e => !e.paused);
  const main = playing[0] || els.find(e => e.played) || els[0];
  return { hasMedia: true, paused: playing.length === 0, volume: main.volume };
}

function site(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

// ---- reporting -------------------------------------------------------------------
async function report() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  await ready;
  const tabs = await chrome.tabs.query({});
  const alive = new Set(tabs.map(t => t.id));
  for (const id of [...tracked]) if (!alive.has(id)) tracked.delete(id);
  for (const t of tabs) if (t.audible) tracked.add(t.id);
  saveTracked();

  const out = [];
  for (const t of tabs) {
    if (!tracked.has(t.id)) continue;
    const media = await mediaState(t.id);
    out.push({
      id: t.id,
      title: t.title || "",
      site: site(t.url),
      audible: !!t.audible,
      muted: !!t.mutedInfo?.muted,
      ...media,
    });
  }
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "tabs", tabs: out }));
}

// Tabs that were paused before the extension started still count.
async function scanExistingTabs() {
  await ready;
  const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"], discarded: false });
  await Promise.all(tabs.map(async t => {
    const els = (await inPage(t.id, pageProbe)).flat();
    if (els.some(e => e.played || !e.paused)) tracked.add(t.id);
  }));
  saveTracked();
}

let soon = null;
function reportSoon() {
  clearTimeout(soon);
  soon = setTimeout(report, 150);
}

// ---- commands from the desktop app ---------------------------------------------------
async function onCommand(c) {
  const tabId = c.tabId;
  if (c.cmd === "mute") {
    await chrome.tabs.update(tabId, { muted: !!c.value });
  } else if (c.cmd === "playpause") {
    const state = await mediaState(tabId);
    if (state.hasMedia) await inPage(tabId, pageSetPlaying, [state.paused]);
  } else if (c.cmd === "volume") {
    await inPage(tabId, pageSetVolume, [Math.max(0, Math.min(1, Number(c.value)))]);
  }
  reportSoon();
}

// ---- connection --------------------------------------------------------------------
function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  ws = new WebSocket(HUB_URL);
  ws.onopen = async () => {
    retryMs = 1000;
    ws.send(JSON.stringify({ type: "hello", browser: browserKey() }));
    await scanExistingTabs();
    report();
    clearInterval(reportTimer);
    reportTimer = setInterval(report, REPORT_EVERY_MS); // also keeps the worker awake while connected
  };
  ws.onmessage = e => {
    try { onCommand(JSON.parse(e.data)); } catch {}
  };
  ws.onclose = () => {
    clearInterval(reportTimer);
    ws = null;
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 30000);
  };
  ws.onerror = () => {}; // onclose follows and retries
}

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if ("audible" in info || "mutedInfo" in info || "title" in info) reportSoon();
});
chrome.tabs.onRemoved.addListener(reportSoon);

// The worker can be stopped while Audio Hub isn't running; this wakes it to try again.
chrome.alarms.create("audiohub-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
