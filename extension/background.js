// Audio Hub browser extension.
//
// Works on its own: the toolbar popup lists tabs playing sound (Now playing / Recent)
// with pause, mute and volume; there are keyboard shortcuts, a badge with the number
// of tabs playing, and an option to let only one tab play at a time.
//
// When the Audio Hub desktop app is running it also reports the tabs there
// (ws://127.0.0.1:8765/ext) and carries out the app's commands.

const HUB_URL = "ws://127.0.0.1:8765/ext";
const REPORT_EVERY_MS = 1500;
const RECENT_MINUTES = 10; // tabs with no player drop out of Recent after this
const RECENT_CAP = 15;

const DEFAULT_SETTINGS = { hub: true, onePlayer: false };

let ws = null;
let retryMs = 1000;
let reportTimer = null;
let settings = { ...DEFAULT_SETTINGS };
// tab id -> { lastPlayed: ms | null, dismissedAt: ms | null }. Tabs here have played sound;
// kept until the tab closes. Stored so it survives the service worker being restarted.
let meta = {};

const ready = Promise.all([
  chrome.storage.session.get("meta").then(r => { meta = r.meta || {}; }),
  chrome.storage.sync.get(DEFAULT_SETTINGS).then(s => { settings = s; }),
]);
function saveMeta() { chrome.storage.session.set({ meta }); }

function track(id) {
  if (!meta[id]) meta[id] = { lastPlayed: null, dismissedAt: null };
  return meta[id];
}

// ---- which browser is this ----------------------------------------------------
function browserKey() {
  const brands = (navigator.userAgentData?.brands || []).map(b => b.brand);
  if (brands.includes("Microsoft Edge")) return "msedge";
  if (brands.includes("Brave")) return "brave";
  if (brands.includes("Opera")) return "opera";
  return "chrome";
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
  const unmuted = els.filter(e => !e.muted);
  if (unmuted.length) els = unmuted;
  if (!els.length) return { hasMedia: false };
  const playing = els.filter(e => !e.paused);
  const main = playing[0] || els.find(e => e.played) || els[0];
  return { hasMedia: true, paused: playing.length === 0, volume: main.volume };
}

function site(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

// ---- the list of tabs with sound ------------------------------------------------
async function collect() {
  await ready;
  const now = Date.now();
  const tabs = await chrome.tabs.query({});
  const alive = new Set(tabs.map(t => t.id));
  for (const id of Object.keys(meta)) if (!alive.has(Number(id))) delete meta[id];
  for (const t of tabs) if (t.audible) track(t.id);

  const out = await Promise.all(tabs.filter(t => meta[t.id]).map(async t => {
    const media = await mediaState(t.id);
    const playing = media.hasMedia ? !media.paused : !!t.audible;
    const m = meta[t.id];
    if (playing) m.lastPlayed = now;
    return {
      id: t.id, windowId: t.windowId,
      title: t.title || "", site: site(t.url), favIconUrl: t.favIconUrl || "",
      audible: !!t.audible, muted: !!t.mutedInfo?.muted,
      playing, lastPlayed: m.lastPlayed, dismissedAt: m.dismissedAt,
      ...media,
    };
  }));
  saveMeta();
  return out;
}

// Same rules as the desktop app: Now playing, then Recent newest first.
function split(tabs) {
  const now = Date.now();
  const playing = tabs.filter(t => t.playing);
  const recent = tabs.filter(t => !t.playing
    && !(t.dismissedAt && (t.lastPlayed || 0) <= t.dismissedAt)
    && (t.hasMedia || (t.lastPlayed && now - t.lastPlayed < RECENT_MINUTES * 60000)));
  recent.sort((a, b) => (b.lastPlayed || 0) - (a.lastPlayed || 0));
  return { playing, recent: recent.slice(0, RECENT_CAP) };
}

// Tabs that were paused before the extension started still count.
async function scanExistingTabs() {
  await ready;
  const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"], discarded: false });
  await Promise.all(tabs.map(async t => {
    const els = (await inPage(t.id, pageProbe)).flat();
    if (els.some(e => (e.played || !e.paused) && !e.muted)) track(t.id);
  }));
  saveMeta();
}

// ---- actions (from the popup, shortcuts or the desktop app) ------------------------
async function pauseTab(id) { await inPage(id, pageSetPlaying, [false]); }

async function pauseAll(exceptId) {
  const tabs = await collect();
  await Promise.all(tabs.filter(t => t.playing && t.hasMedia && t.id !== exceptId).map(t => pauseTab(t.id)));
}

async function act(c) {
  const id = c.tabId;
  switch (c.cmd) {
    case "mute":
      await chrome.tabs.update(id, { muted: !!c.value });
      break;
    case "playpause": {
      const state = await mediaState(id);
      if (state.hasMedia) await inPage(id, pageSetPlaying, [state.paused]);
      break;
    }
    case "volume":
      await inPage(id, pageSetVolume, [Math.max(0, Math.min(1, Number(c.value)))]);
      break;
    case "dismiss":
      await ready;
      track(id).dismissedAt = Date.now();
      saveMeta();
      break;
    case "focus": {
      const tab = await chrome.tabs.update(id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      break;
    }
    case "solo": // keep this tab, pause the rest
      await pauseAll(id);
      break;
    case "pauseAll":
      await pauseAll();
      break;
  }
  reportSoon();
}

// Play/pause the tab you'd most likely mean: one that's playing, else the latest paused one.
async function toggleLatest() {
  const { playing, recent } = split(await collect());
  if (playing.length) {
    await Promise.all(playing.filter(t => t.hasMedia).map(t => pauseTab(t.id)));
  } else {
    const last = recent.find(t => t.hasMedia);
    if (last) await act({ cmd: "playpause", tabId: last.id });
  }
}

// ---- badge: how many tabs are making sound -------------------------------------------
async function updateBadge() {
  const n = (await chrome.tabs.query({ audible: true })).length;
  chrome.action.setBadgeText({ text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#2fb862" });
}

// ---- desktop app connection --------------------------------------------------------
async function report() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const tabs = await collect();
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "tabs", tabs }));
}

let soon = null;
function reportSoon() {
  clearTimeout(soon);
  soon = setTimeout(report, 150);
}

function hubConnected() { return !!ws && ws.readyState === WebSocket.OPEN; }

async function connect() {
  await ready;
  if (!settings.hub) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const sock = ws = new WebSocket(HUB_URL);
  sock.onopen = async () => {
    retryMs = 1000;
    sock.send(JSON.stringify({ type: "hello", browser: browserKey() }));
    await scanExistingTabs();
    report();
    clearInterval(reportTimer);
    reportTimer = setInterval(report, REPORT_EVERY_MS); // also keeps the worker awake while connected
  };
  sock.onmessage = e => {
    try { act(JSON.parse(e.data)); } catch {}
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    clearInterval(reportTimer);
    ws = null;
    if (settings.hub) {
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 30000);
    }
  };
  sock.onerror = () => {}; // onclose follows and retries
}

function disconnect() {
  clearInterval(reportTimer);
  const sock = ws;
  ws = null;
  if (sock) sock.close();
}

// ---- messages from the popup ----------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === "list") {
      await ready;
      reply({ ...split(await collect()), hub: hubConnected(), settings });
    } else if (msg.type === "cmd") {
      await act(msg);
      reply({ ok: true });
    } else if (msg.type === "settings") {
      settings = { ...settings, ...msg.value };
      await chrome.storage.sync.set(settings);
      if (settings.hub) connect(); else disconnect();
      reply({ settings });
    }
  })();
  return true; // reply comes later
});

// ---- events ----------------------------------------------------------------------------
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if ("audible" in info) {
    updateBadge();
    if (info.audible) {
      await ready;
      track(tabId).lastPlayed = Date.now();
      saveMeta();
      if (settings.onePlayer) pauseAll(tabId); // a tab started: pause the others
    }
  }
  if ("audible" in info || "mutedInfo" in info || "title" in info) reportSoon();
});
chrome.tabs.onRemoved.addListener(() => { updateBadge(); reportSoon(); });

chrome.commands.onCommand.addListener(cmd => {
  if (cmd === "toggle-latest") toggleLatest();
  else if (cmd === "pause-all") pauseAll();
});

// The worker can be stopped while Audio Hub isn't running; this wakes it to try again.
chrome.alarms.create("audiohub-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(() => { connect(); scanExistingTabs(); updateBadge(); });
connect();
updateBadge();
