// Slide Remote — Google Slides bridge (background service worker).
//
// Maintains a single WebSocket connection to the helper at
//   ws://127.0.0.1:7799
// and pipes:
//   - State updates from the content script ── (slide, total, notes) ──>
//   - Command messages from the helper       ── (next/prev/black/...) ──>
//     into the content script for synthetic key injection.
//   - Thumbnail captures via chrome.tabs.captureVisibleTab on request.

const BRIDGE_URL = 'ws://127.0.0.1:7799';
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const VERSION = '1.0.0';

let ws = null;
let reconnectDelay = RECONNECT_MIN_MS;
let reconnectTimer = null;
let activeTabId = null;
let lastState = null;

// ─── WebSocket ────────────────────────────────────────────────────────

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  try {
    ws = new WebSocket(BRIDGE_URL);
  } catch (e) {
    console.warn('[slide-remote] failed to construct WebSocket:', e);
    scheduleReconnect();
    return;
  }
  ws.addEventListener('open', () => {
    console.log('[slide-remote] connected to helper');
    reconnectDelay = RECONNECT_MIN_MS;
    sendToHelper({ type: 'hello', version: VERSION });
    // Re-emit our last known state so the helper hydrates on reconnect.
    if (lastState) sendToHelper({ type: 'state', ...lastState });
    setBadge('•', '#a78bfa');
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); }
    catch (_) { return; }
    handleHelperMessage(msg);
  });
  ws.addEventListener('close', () => {
    console.log('[slide-remote] helper disconnected');
    ws = null;
    setBadge('', '#888');
    scheduleReconnect();
  });
  ws.addEventListener('error', () => {
    // Errors trigger a close event right after; rely on that for retry.
  });
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    connect();
  }, reconnectDelay);
}

function sendToHelper(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  try { ws.send(JSON.stringify(obj)); return true; }
  catch (e) { console.warn('[slide-remote] send failed:', e); return false; }
}

// ─── Helper → extension command handling ─────────────────────────────

async function handleHelperMessage(msg) {
  const type = msg && msg.type;
  if (type === 'cmd') {
    const tabId = await pickSlidesTab();
    if (!tabId) return;
    chrome.tabs.sendMessage(tabId, { type: 'cmd', command: msg.command });
    return;
  }
  if (type === 'request_image') {
    const tabId = await pickSlidesTab();
    if (!tabId) return;
    // Make sure the tab is the active foreground tab — captureVisibleTab
    // only works on the visible tab in its window.
    try {
      const tab = await chrome.tabs.get(tabId);
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
        format: 'jpeg',
        quality: 70,
      });
      const b64 = dataUrl.replace(/^data:image\/jpeg;base64,/, '');
      const slide = lastState ? lastState.current : 0;
      sendToHelper({ type: 'image', slide, jpeg_b64: b64 });
    } catch (e) {
      console.warn('[slide-remote] captureVisibleTab failed:', e);
    }
    return;
  }
}

async function pickSlidesTab() {
  // If we know the active Slides tab and it's still alive, use it.
  if (activeTabId !== null) {
    try {
      const tab = await chrome.tabs.get(activeTabId);
      if (tab && /docs\.google\.com\/presentation/.test(tab.url || '')) {
        return activeTabId;
      }
    } catch (_) { /* fall through */ }
  }
  // Otherwise find any open Slides tab.
  const tabs = await chrome.tabs.query({ url: '*://docs.google.com/presentation/*' });
  if (tabs.length === 0) return null;
  activeTabId = tabs[0].id;
  return activeTabId;
}

// ─── Content script → background ──────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === 'state') {
    activeTabId = sender.tab ? sender.tab.id : activeTabId;
    lastState = {
      in_show: !!msg.in_show,
      current: Number(msg.current) || 0,
      total: Number(msg.total) || 0,
      notes: typeof msg.notes === 'string' ? msg.notes : '',
    };
    sendToHelper({ type: 'state', ...lastState });
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === 'request_image_from_bg') {
    // Content script can request a capture if it knows the slide just changed.
    handleHelperMessage({ type: 'request_image' }).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === 'popup_query') {
    pickSlidesTab().then((tabId) => {
      sendResponse({
        connected: !!ws && ws.readyState === WebSocket.OPEN,
        slidesTab: tabId !== null,
        in_show: !!(lastState && lastState.in_show),
        current: lastState ? lastState.current : 0,
        total: lastState ? lastState.total : 0,
      });
    });
    return true;  // async response
  }
});

// ─── Action badge helpers ─────────────────────────────────────────────

function setBadge(text, color) {
  try {
    chrome.action.setBadgeText({ text });
    if (color) chrome.action.setBadgeBackgroundColor({ color });
  } catch (_) { /* ignore on older builds */ }
}

// ─── Boot ─────────────────────────────────────────────────────────────

connect();
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
