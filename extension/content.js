// Slide Remote — content script running on docs.google.com/presentation/*.
//
// Two jobs:
//   1. Observe the DOM to detect (a) whether we're in slideshow/presenter
//      mode and (b) the current slide / total / speaker notes. Report
//      state changes to the background service worker.
//   2. Receive commands from the background (next/prev/black/end/etc) and
//      inject equivalent keyboard events into the page.
//
// Google Slides' DOM is not documented and changes from time to time. The
// selectors below are best-effort and grouped so each piece can be tuned
// independently if Google reshuffles things. The general approach is:
//   - parse the URL to detect slideshow vs editor mode
//   - read "N / M" page indicators where present, fall back to URL parsing
//   - read notes from the editor's notes pane (slideshow itself doesn't
//     render notes; presenter view is a separate window)
//   - dispatch synthetic KeyboardEvents on the focused element / document

(() => {
  'use strict';

  const POLL_MS = 750;
  const URL_PRESENT_RE = /\/presentation\/d\/[^/]+\/(present|edit)/;

  let lastReport = null;

  // ─── State extraction ───────────────────────────────────────────────

  function isPresenting() {
    // Slideshow URL form: /presentation/d/<id>/present?...
    return /\/presentation\/d\/[^/]+\/present(\b|\?)/.test(location.href);
  }

  function readCurrentTotal() {
    // Try a few selectors known to host slide counters at various times.
    // The first match wins. Adjust this list when Slides changes.
    const selectors = [
      '.punch-viewer-page-indicator',         // slideshow page indicator
      '.docs-material-menu-button-caption',   // editor toolbar caption
      '[aria-label*="Slide"][aria-label*="of"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const txt = (el.textContent || '').trim();
      // Match e.g. "3 / 12" or "Slide 3 of 12"
      let m = txt.match(/(\d+)\s*[\/]\s*(\d+)/);
      if (!m) m = txt.match(/(\d+)\s*of\s*(\d+)/i);
      if (m) return { current: +m[1], total: +m[2] };
    }
    // Fall back to URL fragment for slideshow position (when present).
    const m = location.hash.match(/[?&]slide=id\.([^&]+)/);
    if (m) {
      // Index unknown without DOM; report current=1, total=0 so phone
      // shows something instead of "—/—".
      return { current: 1, total: 0 };
    }
    return { current: 0, total: 0 };
  }

  function readNotes() {
    // Speaker-notes pane in the editor: a content-editable region under
    // .punch-present-iframe or .docs-notes-pane. We grab the active
    // slide's notes block.
    const candidates = [
      '.punch-present-iframe + * [data-notes]',
      '[role="textbox"][aria-label*="notes" i]',
      '.docs-notes-pane',
    ];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el && el.textContent) {
        const t = el.textContent.trim();
        if (t) return t;
      }
    }
    return '';
  }

  function collectState() {
    const present = isPresenting();
    const { current, total } = readCurrentTotal();
    const notes = present ? readNotes() : '';
    return {
      in_show: present,
      current,
      total,
      notes,
    };
  }

  function maybeReport() {
    const s = collectState();
    const key = `${s.in_show}|${s.current}|${s.total}|${s.notes}`;
    if (key === lastReport) return;
    lastReport = key;
    try {
      chrome.runtime.sendMessage({ type: 'state', ...s });
    } catch (e) {
      // Service worker might be asleep — silently skip; next tick will retry.
    }
  }

  // ─── Polling and DOM observation ────────────────────────────────────

  // A small interval-based poll keeps things simple and resilient to
  // Google Slides changing its DOM. 750 ms is plenty fast for a presenter's
  // navigation cadence.
  setInterval(maybeReport, POLL_MS);

  // MutationObserver gives us faster responsiveness on slide changes.
  try {
    const obs = new MutationObserver(() => maybeReport());
    obs.observe(document.documentElement, { childList: true, subtree: true });
  } catch (_) { /* not critical */ }

  // ─── Command injection ──────────────────────────────────────────────

  // Map our command names onto the keyboard shortcuts Google Slides uses
  // in slideshow mode. These match PowerPoint's slideshow shortcuts so
  // the protocol is uniform across both targets.
  const KEY_FOR = {
    next:  { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
    prev:  { key: 'ArrowLeft',  code: 'ArrowLeft',  keyCode: 37 },
    black: { key: 'b',          code: 'KeyB',       keyCode: 66 },
    white: { key: 'w',          code: 'KeyW',       keyCode: 87 },
    start: { key: 's',          code: 'KeyS',       keyCode: 83, ctrlKey: true, shiftKey: true }, // Ctrl+Shift+S starts slideshow
    end:   { key: 'Escape',     code: 'Escape',     keyCode: 27 },
  };

  function dispatchKey(target, descriptor, type) {
    const ev = new KeyboardEvent(type, {
      key: descriptor.key,
      code: descriptor.code,
      keyCode: descriptor.keyCode,
      which: descriptor.keyCode,
      bubbles: true,
      cancelable: true,
      ctrlKey: !!descriptor.ctrlKey,
      shiftKey: !!descriptor.shiftKey,
      altKey: !!descriptor.altKey,
      metaKey: !!descriptor.metaKey,
    });
    target.dispatchEvent(ev);
  }

  function injectCommand(name) {
    const d = KEY_FOR[name];
    if (!d) return false;
    // Try active element first, then the slideshow iframe if we're in
    // presenter mode, then the document body.
    const targets = [
      document.activeElement,
      document.querySelector('iframe.punch-present-iframe'),
      document.body,
      document,
    ];
    for (const target of targets) {
      if (!target) continue;
      try {
        dispatchKey(target, d, 'keydown');
        dispatchKey(target, d, 'keyup');
        return true;
      } catch (_) { /* try next */ }
    }
    return false;
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg) return;
    if (msg.type === 'cmd') {
      const ok = injectCommand(msg.command);
      sendResponse({ ok });
      return true;
    }
  });

  // Initial report so the helper hydrates immediately on page load.
  maybeReport();
})();
