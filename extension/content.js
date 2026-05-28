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
    // Wide net: scan visible text for "N / M" or "N of M" anywhere.
    // Avoids hand-curating selectors when Google reshuffles classes.
    try {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const t = (node.nodeValue || '').trim();
        if (!t || t.length > 32) continue;  // skip large blobs
        let m = t.match(/^(\d+)\s*[\/]\s*(\d+)$/);
        if (!m) m = t.match(/^(\d+)\s*of\s*(\d+)$/i);
        if (m && +m[2] >= +m[1] && +m[2] <= 999) {
          return { current: +m[1], total: +m[2] };
        }
      }
    } catch (_) { /* ignore */ }
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
    let { current, total } = readCurrentTotal();
    const notes = present ? readNotes() : '';
    // When we *know* we're in slideshow mode (URL says so) but the DOM
    // scrape didn't yield a counter, fall back to current=1/total=1 so
    // the helper recognises Slides as the active source and starts
    // routing commands here. Counter accuracy can be tuned later by
    // adding selectors to readCurrentTotal().
    if (present && total === 0) {
      current = 1;
      total = 1;
    }
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

  // Map our command names onto the keyboard shortcuts Google Slides uses.
  // Notes:
  //   - "start" uses Ctrl+F5 (Slides' actual "Present from beginning"
  //     binding). Browser-level fullscreen requires a real user gesture
  //     though, so even with the right shortcut the browser may decline
  //     to fullscreen — slideshow mode will still engage, the user just
  //     has to confirm or hit F11 themselves.
  //   - other commands match PowerPoint shortcuts so the BLE protocol
  //     is uniform.
  const KEY_FOR = {
    next:  { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
    prev:  { key: 'ArrowLeft',  code: 'ArrowLeft',  keyCode: 37 },
    black: { key: 'b',          code: 'KeyB',       keyCode: 66 },
    white: { key: 'w',          code: 'KeyW',       keyCode: 87 },
    start: { key: 'F5',         code: 'F5',         keyCode: 116, ctrlKey: true },
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

  // Try several known aria-label patterns for the Slides "Present" button.
  // If we find one, we click it as a fallback when the keyboard shortcut
  // doesn't take.
  function findPresentButton() {
    const selectors = [
      '[aria-label="Slideshow"]',
      '[aria-label*="lideshow" i]',
      '[aria-label*="resentation" i]',
      '[aria-label*="resent" i][role="button"]',
      '#scb-button',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function dispatchToCommonTargets(d) {
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

  function injectCommand(name) {
    const d = KEY_FOR[name];
    if (!d) return false;

    // For Start, try the keyboard shortcut first, then a button click as
    // a fallback — Google sometimes refactors keyboard handling without
    // refactoring the toolbar button.
    if (name === 'start') {
      const sentKey = dispatchToCommonTargets(d);
      // Give the keyboard handler a moment; if we don't see a URL change
      // soon, click the Present button instead.
      setTimeout(() => {
        if (!isPresenting()) {
          const btn = findPresentButton();
          if (btn) btn.click();
        }
      }, 250);
      return sentKey;
    }

    return dispatchToCommonTargets(d);
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
