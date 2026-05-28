// Slide Remote — popup status panel.
//
// Asks the background service worker for the current connection /
// page state and renders it. Re-asks every 500 ms while the popup is open.

const $ = (id) => document.getElementById(id);

function setDot(el, klass) {
  el.classList.remove('ok', 'warn', 'error');
  if (klass) el.classList.add(klass);
}

async function refresh() {
  let info = { connected: false, slidesTab: false, current: 0, total: 0, in_show: false };
  try {
    info = await chrome.runtime.sendMessage({ type: 'popup_query' });
  } catch (_) { /* background may be sleeping; show defaults */ }
  if (!info) info = { connected: false, slidesTab: false, current: 0, total: 0, in_show: false };

  setDot($('conn-dot'), info.connected ? 'ok' : 'error');
  $('conn-text').textContent = info.connected ? 'connected' : 'disconnected';

  setDot($('page-dot'), info.slidesTab ? 'ok' : 'warn');
  $('page-text').textContent = info.slidesTab
    ? (info.in_show ? 'in slideshow' : 'editor open')
    : 'no Google Slides tab';

  $('slide-text').textContent = info.total
    ? `${info.current} / ${info.total}`
    : '—';
}

refresh();
setInterval(refresh, 500);
