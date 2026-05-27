// PPT Remote — Web Bluetooth client.
// Pairs with the Python helper advertising the GATT service below.

const SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0';
const CHAR_COMMAND = '12345678-1234-5678-1234-56789abcdef1';
const CHAR_NOTES   = '12345678-1234-5678-1234-56789abcdef2';
const CHAR_SLIDE   = '12345678-1234-5678-1234-56789abcdef3';

const CMD = { NEXT: 0x01, PREV: 0x02, BLACK: 0x03, WHITE: 0x04, START: 0x05, END: 0x06 };

const els = {
  connect:      document.getElementById('connectBtn'),
  statusText:   document.getElementById('statusText'),
  dot:          document.getElementById('dot'),
  banner:       document.getElementById('banner'),
  slideCounter: document.getElementById('slideCounter'),
  counterTotal: document.getElementById('counterTotal'),
  modeTag:      document.getElementById('modeTag'),
  timer:        document.getElementById('timer'),
  progressBar:  document.getElementById('progressBar'),
  notes:        document.getElementById('notes'),
  notesSlideTag:document.getElementById('notesSlideTag'),
  prev:         document.getElementById('prevBtn'),
  next:         document.getElementById('nextBtn'),
  black:        document.getElementById('blackBtn'),
  white:        document.getElementById('whiteBtn'),
  start:        document.getElementById('startBtn'),
  end:          document.getElementById('endBtn'),
};

const state = {
  device: null,
  server: null,
  commandChar: null,
  notesChar: null,
  slideChar: null,
  connecting: false,
  notesBuf: new Map(),       // slide -> { total, chunks: Array(total), received }
  renderedSlide: -1,
  currentSlide: 0,
  totalSlides: 0,
  hasSlides: false,
  timerStart: null,
  timerHandle: null,
};

// ---- UI helpers --------------------------------------------------------

function setBodyClass(name) {
  document.body.classList.remove('is-connected', 'is-connecting', 'is-disconnected');
  document.body.classList.add(name);
}

function setStatus(label, kind) {
  els.statusText.textContent = label;
  els.dot.className = `dot ${kind}`;
  if (kind === 'connected')        setBodyClass('is-connected');
  else if (kind === 'connecting')  setBodyClass('is-connecting');
  else                             setBodyClass('is-disconnected');
}

function setMode(text, live) {
  els.modeTag.textContent = text;
  els.modeTag.classList.toggle('live', !!live);
}

function setControlsEnabled(enabled) {
  for (const b of [els.prev, els.next, els.black, els.white, els.start, els.end]) {
    b.disabled = !enabled;
  }
}

function showBanner(text, withReconnect) {
  els.banner.classList.remove('hidden');
  els.banner.innerHTML = '';
  const span = document.createElement('span');
  span.textContent = text;
  els.banner.appendChild(span);
  if (withReconnect) {
    const btn = document.createElement('button');
    btn.textContent = 'Reconnect';
    btn.addEventListener('click', () => { hideBanner(); connect(); });
    els.banner.appendChild(btn);
  }
}

function hideBanner() {
  els.banner.classList.add('hidden');
  els.banner.innerHTML = '';
}

function setCounter(current, total) {
  const hasData = total > 0;
  state.hasSlides = hasData;
  if (!hasData) {
    els.slideCounter.textContent = '—';
    els.counterTotal.textContent = '/ —';
    els.progressBar.style.width = '0%';
    els.notesSlideTag.classList.add('hidden');
    return;
  }
  // Pulse the counter when the slide actually changes.
  if (current !== state.currentSlide) {
    els.slideCounter.classList.remove('pulse');
    void els.slideCounter.offsetWidth; // restart animation
    els.slideCounter.classList.add('pulse');
  }
  els.slideCounter.textContent = String(current);
  els.counterTotal.textContent = `/ ${total}`;
  const pct = Math.max(0, Math.min(100, (current / total) * 100));
  els.progressBar.style.width = `${pct}%`;

  els.notesSlideTag.textContent = `Slide ${current}`;
  els.notesSlideTag.classList.remove('hidden');
}

function fmtTime(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

function startTimer() {
  if (state.timerStart) return;
  state.timerStart = Date.now();
  state.timerHandle = setInterval(() => {
    els.timer.textContent = fmtTime(Date.now() - state.timerStart);
  }, 500);
}

function resetTimer() {
  if (state.timerHandle) clearInterval(state.timerHandle);
  state.timerHandle = null;
  state.timerStart = null;
  els.timer.textContent = '00:00';
}

function haptic(ms) {
  if (navigator.vibrate) {
    try { navigator.vibrate(ms); } catch (_) {}
  }
}

// ---- BLE plumbing ------------------------------------------------------

async function connect() {
  if (!('bluetooth' in navigator)) {
    showBanner('Web Bluetooth is not supported in this browser. Use Chrome/Edge on Android, or Bluefy on iOS.', false);
    return;
  }
  if (state.connecting) return;
  state.connecting = true;
  setStatus('Requesting…', 'connecting');
  setMode('Pairing', false);
  els.connect.disabled = true;

  try {
    const device = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SERVICE_UUID] }],
      optionalServices: [SERVICE_UUID],
    });
    state.device = device;
    device.addEventListener('gattserverdisconnected', onDisconnect);

    setStatus('Connecting…', 'connecting');
    const server = await device.gatt.connect();
    state.server = server;

    const service = await server.getPrimaryService(SERVICE_UUID);
    state.commandChar = await service.getCharacteristic(CHAR_COMMAND);
    state.notesChar   = await service.getCharacteristic(CHAR_NOTES);
    state.slideChar   = await service.getCharacteristic(CHAR_SLIDE);

    state.notesChar.addEventListener('characteristicvaluechanged', onNotes);
    state.slideChar.addEventListener('characteristicvaluechanged', onSlideInfo);
    await state.notesChar.startNotifications();
    await state.slideChar.startNotifications();

    setStatus('Connected', 'connected');
    setMode('Ready', true);
    els.connect.disabled = false;
    setControlsEnabled(true);
    hideBanner();
    haptic(15);
  } catch (err) {
    console.error('connect failed', err);
    setStatus('Connect', 'disconnected');
    setMode('Not connected', false);
    els.connect.disabled = false;
    setControlsEnabled(false);
    if (err && err.name !== 'NotFoundError') {
      // NotFoundError = user cancelled the chooser; no banner needed.
      showBanner(`Connection failed: ${err.message || err.name}`, true);
    }
  } finally {
    state.connecting = false;
  }
}

function disconnect() {
  if (state.device && state.device.gatt && state.device.gatt.connected) {
    state.device.gatt.disconnect();
  } else {
    onDisconnect();
  }
}

function onDisconnect() {
  setStatus('Connect', 'disconnected');
  setMode('Not connected', false);
  setControlsEnabled(false);
  els.connect.disabled = false;
  state.server = null;
  state.commandChar = null;
  state.notesChar = null;
  state.slideChar = null;
  state.notesBuf.clear();
  state.renderedSlide = -1;
  state.currentSlide = 0;
  state.totalSlides = 0;
  setCounter(0, 0);
  resetTimer();
  showBanner('Disconnected from PPT Remote.', true);
  haptic(30);
}

async function sendCommand(byte) {
  if (!state.commandChar) return;
  const data = Uint8Array.of(byte);
  haptic(8);
  try {
    if (state.commandChar.writeValueWithoutResponse) {
      await state.commandChar.writeValueWithoutResponse(data);
    } else {
      await state.commandChar.writeValue(data);
    }
  } catch (err) {
    console.warn('write failed', err);
    showBanner(`Write failed: ${err.message || err.name}`, true);
  }
}

// ---- Notifications -----------------------------------------------------

function onSlideInfo(ev) {
  const v = ev.target.value;
  if (!v || v.byteLength < 2) return;
  const current = v.getUint8(0);
  const total   = v.getUint8(1);
  setCounter(current, total);
  state.currentSlide = current;
  state.totalSlides = total;
  if (current > 0 && total > 0) {
    setMode('Live', true);
    startTimer();
  } else {
    setMode('No deck open', false);
  }
}

function onNotes(ev) {
  const v = ev.target.value;
  if (!v || v.byteLength < 3) return;
  const slide       = v.getUint8(0);
  const chunkIdx    = v.getUint8(1);
  const totalChunks = v.getUint8(2);
  const body = new Uint8Array(v.buffer, v.byteOffset + 3, v.byteLength - 3);

  if (totalChunks === 0) { renderNotes(slide, ''); return; }

  let entry = state.notesBuf.get(slide);
  if (!entry || entry.total !== totalChunks) {
    entry = { total: totalChunks, chunks: new Array(totalChunks), received: 0 };
    state.notesBuf.set(slide, entry);
  }

  if (!entry.chunks[chunkIdx]) {
    entry.chunks[chunkIdx] = body.slice();
    entry.received += 1;
  } else {
    entry.chunks[chunkIdx] = body.slice();
  }

  if (entry.received >= entry.total) {
    const totalBytes = entry.chunks.reduce((n, c) => n + (c ? c.length : 0), 0);
    const merged = new Uint8Array(totalBytes);
    let off = 0;
    for (const c of entry.chunks) {
      if (c) { merged.set(c, off); off += c.length; }
    }
    const text = new TextDecoder('utf-8').decode(merged);
    renderNotes(slide, text);
    state.notesBuf.delete(slide);

    for (const k of state.notesBuf.keys()) {
      if (k !== slide) state.notesBuf.delete(k);
    }
  }
}

function renderNotes(slide, text) {
  state.renderedSlide = slide;
  const trimmed = (text || '').trim();
  // Swap with a brief fade so changes feel intentional rather than jarring.
  els.notes.classList.add('swap');
  setTimeout(() => {
    if (!trimmed) {
      els.notes.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = state.hasSlides
        ? 'No notes for this slide.'
        : 'Waiting for PowerPoint…';
      els.notes.appendChild(p);
    } else {
      els.notes.textContent = text;
    }
    els.notes.scrollTop = 0;
    els.notes.classList.remove('swap');
  }, 120);
}

// ---- Wire up UI --------------------------------------------------------

els.connect.addEventListener('click', () => {
  if (state.server && state.server.connected) disconnect();
  else connect();
});

els.next.addEventListener('click',  () => sendCommand(CMD.NEXT));
els.prev.addEventListener('click',  () => sendCommand(CMD.PREV));
els.black.addEventListener('click', () => sendCommand(CMD.BLACK));
els.white.addEventListener('click', () => sendCommand(CMD.WHITE));
els.start.addEventListener('click', () => sendCommand(CMD.START));
els.end.addEventListener('click',   () => sendCommand(CMD.END));

// Keyboard shortcuts when a hardware keyboard is attached (e.g. desktop testing).
document.addEventListener('keydown', (e) => {
  if (!state.commandChar) return;
  if (e.repeat) return;
  switch (e.key) {
    case 'ArrowRight': case 'PageDown': case ' ':
      sendCommand(CMD.NEXT); e.preventDefault(); break;
    case 'ArrowLeft': case 'PageUp':
      sendCommand(CMD.PREV); e.preventDefault(); break;
    case 'b': case 'B':
      sendCommand(CMD.BLACK); e.preventDefault(); break;
    case 'w': case 'W':
      sendCommand(CMD.WHITE); e.preventDefault(); break;
    case 'Escape':
      sendCommand(CMD.END); e.preventDefault(); break;
  }
});

// Initial state
setStatus('Connect', 'disconnected');
setMode('Not connected', false);
setControlsEnabled(false);

if (!('bluetooth' in navigator)) {
  showBanner('Web Bluetooth is not supported in this browser. Use Chrome/Edge on Android, or Bluefy on iOS.', false);
  els.connect.disabled = true;
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch((err) => {
      console.warn('SW registration failed', err);
    });
  });
}
