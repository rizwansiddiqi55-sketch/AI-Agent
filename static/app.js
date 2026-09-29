"use strict";

// ---------- Session ----------
function storageGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } }

let sessionId = storageGet("tutor.session");
if (!sessionId) {
  sessionId = "s-" + Math.random().toString(36).slice(2, 10);
  storageSet("tutor.session", sessionId);
}

const $ = (id) => document.getElementById(id);

// ---------- API with passcode ----------
let passcode = storageGet("tutor.passcode") || "";

async function api(path, options = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), "X-App-Passcode": passcode },
    });
    if (res.status !== 401) {
      if (res.status === 503) {
        const body = await res.clone().json().catch(() => ({}));
        if (body.detail) addMessage("error", body.detail);
      }
      return res;
    }
    const entered = window.prompt(attempt ? "Wrong passcode. Try again:" : "Enter your tutor passcode:");
    if (entered === null) return res;
    passcode = entered.trim();
    storageSet("tutor.passcode", passcode);
  }
  return fetch(path, options);
}
const transcript = $("transcript");
const statusEl = $("status");
const textInput = $("textInput");
const micBtn = $("micBtn");
const stopBtn = $("stopBtn");
const handsFree = $("handsFree");

let recogLang = storageGet("tutor.lang") || "en-US";
handsFree.checked = storageGet("tutor.handsfree") === "1";
handsFree.addEventListener("change", () => storageSet("tutor.handsfree", handsFree.checked ? "1" : "0"));

const URDU_RE = /[؀-ۿ]/;

// ---------- Rendering ----------
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderMarkdown(text) {
  const parts = text.split(/```/);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      const body = part.replace(/^[\w+-]*\n/, "");
      return `<pre><code>${escapeHtml(body)}</code></pre>`;
    }
    let html = escapeHtml(part)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/^#{1,6}\s*(.+)$/gm, "<b>$1</b>");
    return html.split(/\n{2,}/).map((p) => p.trim() ? `<p>${p.replace(/\n/g, "<br>")}</p>` : "").join("");
  }).join("");
}

function addMessage(role, text) {
  $("emptyState")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  setMessageText(el, text);
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
  return el;
}

function setMessageText(el, text) {
  if (el.classList.contains("assistant")) el.innerHTML = renderMarkdown(text);
  else el.textContent = text;
  el.dir = URDU_RE.test(text.slice(0, 80)) ? "rtl" : "auto";
  transcript.scrollTop = transcript.scrollHeight;
}

function setStatus(text) {
  statusEl.textContent = text || "";
  statusEl.hidden = !text;
  if (typeof call !== "undefined") call.update(text);
}

// ---------- Text-to-speech ----------
// Preferred: natural Azure neural voices from the server (/api/tts), including a native
// Pakistani Urdu voice. Fallback: the device's own speechSynthesis voices.
function silentWavUrl() {
  // 0.1 s of silence, used to unlock audio playback on iOS from a tap
  const samples = 800, buf = new ArrayBuffer(44 + samples * 2), v = new DataView(buf);
  const str = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + samples * 2, true); str(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 8000, true); v.setUint32(28, 16000, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, samples * 2, true);
  let bin = ""; new Uint8Array(buf).forEach((b) => { bin += String.fromCharCode(b); });
  return "data:audio/wav;base64," + btoa(bin);
}

const tts = {
  voices: [],
  queue: 0,          // sentences waiting or playing (queue > 0 means "speaking")
  server: false,     // server voice available (set from /api/config)
  scope: "all",      // "urdu": only Urdu sentences use the server voice
  hdEnabled: storageGet("tutor.hd") !== "0",  // user switch for the natural Urdu voice (saves free quota)
  budget: null,      // max server-voice characters per reply (null = unlimited)
  replyChars: 0,
  skipped: false,
  cache: new Map(),  // text -> audio blob promise, so repeated sentences cost nothing
  items: [],
  playing: false,
  gen: 0,            // bumped on cancel so stale playback is ignored
  audio: new Audio(),
  enVoiceName: storageGet("tutor.enVoice") || "",  // "" = automatic, "groq:<voice>", or a device voice name
  rate: Number(storageGet("tutor.rate")) || 1,     // speaking speed for all voices
  groqVoices: [],    // natural English voices from the server (Groq), set from /api/config
  geminiVoices: [],  // [{name, desc}] Gemini voices, set from /api/config
  enOk: true,        // false after a natural-voice error: use the device voice for the rest of the session
  enPausedUntil: 0,  // free plans are limited per minute/day: phone voice until the limit clears
  hdPausedUntil: 0,  // same for the Urdu voice
  // The chosen natural English voice as "groq:<name>" / "gemini:<Name>", or "" for the phone voice.
  englishVoice() {
    const [provider, name] = this.enVoiceName.split(":");
    const available = provider === "groq" ? this.groqVoices.includes(name)
      : provider === "gemini" ? this.geminiVoices.some((v) => v.name === name) : false;
    if (!available || !this.enOk || Date.now() < this.enPausedUntil) return "";
    return this.enVoiceName;
  },
  load() {
    this.voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  },
  useHd() { return this.server && this.hdEnabled; },
  hasUrdu() {
    return this.useHd() || this.voices.some((v) => v.lang.toLowerCase().startsWith("ur"));
  },
  newReply() { this.replyChars = 0; this.skipped = false; },
  englishVoices() {
    return this.voices.filter((v) => v.lang.toLowerCase().startsWith("en"));
  },
  pickVoice(text) {
    if (this.enVoiceName && !URDU_RE.test(text)) {
      const chosen = this.voices.find((v) => v.name === this.enVoiceName);
      if (chosen) return chosen;
    }
    const want = URDU_RE.test(text) ? ["ur"] : ["en-gb", "en-us", "en-in", "en"];
    for (const prefix of want) {
      const v = this.voices.find((v) => v.lang.toLowerCase().replace("_", "-").startsWith(prefix));
      if (v) return v;
    }
    return null;
  },
  speak(text) {
    const clean = cleanForSpeech(text);
    if (!clean) return;
    // One ordered queue: HD voice (Gemini/ElevenLabs/Azure) for Urdu (or all) sentences, the chosen Groq
    // English voice for English sentences, device voice otherwise.
    const urdu = URDU_RE.test(clean);
    let useServer = this.useHd() && (this.scope === "all" || urdu);
    if (useServer && this.budget && this.replyChars > 0 && this.replyChars + clean.length > this.budget) {
      // Over this reply's credit budget: Urdu stays on screen, English uses the device voice.
      this.skipped = true;
      if (URDU_RE.test(clean)) return;
      useServer = false;
    }
    const voice = !urdu ? this.englishVoice() : "";
    const kind = useServer && !(voice && this.scope === "all") ? "hd" : (voice ? "en" : "");
    if (kind === "hd" && !this.cache.has(`hd::${clean}`)) this.replyChars += clean.length;
    // Join queued sentences into one request: the free voices are limited by requests per minute
    // (Groq also allows only 200 characters per request).
    const last = this.items[this.items.length - 1];
    const maxJoin = voice.startsWith("groq:") ? 200 : 450;
    if (kind && last && last.kind === kind && last.voice === voice && !last.promise
        && last.text.length + clean.length < maxJoin) {
      last.text += " " + clean;
      return;
    }
    this.items.push({ text: clean, kind, voice, promise: null });
    this.started();
    if (!this.playing) this.playNext(this.gen);
  },
  // Fetch lazily: only the playing sentence and the next one (free plans allow few requests at once).
  fetchAudio(item) {
    if (!item || !item.kind || item.promise) return;
    const key = `${item.kind}:${item.voice}:${item.text}`;
    if (this.cache.has(key)) { item.promise = this.cache.get(key); return; }
    const [path, payload] = item.kind === "en"
      ? ["/api/tts/english", { text: item.text, voice: item.voice }]
      : ["/api/tts", { text: item.text }];
    item.promise = api(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }).then(async (res) => {
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const err = new Error(data.detail || `HTTP ${res.status}`);
        err.status = res.status;
        err.retryAfter = Number(res.headers.get("Retry-After")) || 0;
        throw err;
      }
      return res.blob();
    });
    this.cache.set(key, item.promise);
    item.promise.catch(() => this.cache.delete(key));  // errors handled in playNext
    if (this.cache.size > 100) this.cache.delete(this.cache.keys().next().value);
  },
  deviceSay(clean) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) return resolve();
      const u = new SpeechSynthesisUtterance(clean);
      const voice = this.pickVoice(clean);
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.rate = this.rate;
      u.onend = u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  },
  async playServer(item, gen) {
    let url = null;
    try {
      const blob = await item.promise;
      if (gen !== this.gen) return;
      // Prefetch the next sentence only now, so sentences that arrived meanwhile are joined into it.
      if (this.ready(this.items[0])) this.fetchAudio(this.items[0]);
      url = URL.createObjectURL(blob);
      this.audio.src = url;
      this.audio.defaultPlaybackRate = this.rate;  // loading a new src resets playbackRate to this
      this.audio.playbackRate = this.rate;
      await new Promise((resolve, reject) => {
        this.audio.onended = resolve;
        this.audio.onerror = () => reject(new Error("audio playback failed"));
        this.audio.play().catch(reject);
      });
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
  },
  async playNext(gen) {
    const item = this.items.shift();
    if (!item) { this.playing = false; return; }
    this.playing = true;
    const ready = this.ready(item);
    if (ready) this.fetchAudio(item);
    if (!ready && this.ready(this.items[0])) this.fetchAudio(this.items[0]);  // prefetch while the phone speaks
    try {
      if (ready) await this.playServer(item, gen);
      else await this.deviceSay(item.text);
    } catch (err) {
      if (gen !== this.gen) return;
      if (item.kind === "en") {
        if (err.status === 429) {
          // Free-plan limit: phone voice until it clears (long waits mean a daily limit).
          const wait = err.retryAfter || 20;
          this.enPausedUntil = Date.now() + wait * 1000;
          if (wait > 60) setStatus(`Natural voice free limit reached; phone voice for about ${Math.ceil(wait / 60)} min.`);
        } else {
          this.enOk = false;
          setStatus(`English voice unavailable (${err.message}). Using the phone voice.`);
        }
      } else if (err.status === 429 && err.retryAfter) {
        // Urdu voice free limit: phone voice until it clears.
        this.hdPausedUntil = Date.now() + err.retryAfter * 1000;
        if (err.retryAfter > 60) setStatus(`Urdu voice free limit reached; phone voice for about ${Math.ceil(err.retryAfter / 60)} min.`);
      } else if (err.status !== 429) {
        // Quota used up, bad key, network...: use the device voice for the rest of this session.
        this.server = false;
        setStatus(`Natural voice unavailable (${err.message}). Using the device voice.`);
      }
      // Busy (429): just this sentence falls back; keep trying the natural voice for the next ones.
      await this.deviceSay(item.text);
    }
    if (gen !== this.gen) return;
    this.finished();
    this.playNext(gen);
  },
  ready(item) {
    if (!item) return false;
    if (item.kind === "hd") return this.useHd() && (!!item.promise || Date.now() >= this.hdPausedUntil);
    return item.kind === "en" && this.enOk && (!!item.promise || Date.now() >= this.enPausedUntil);
  },
  started() {
    this.queue++;
    stopBtn.hidden = false;
    call.update();
  },
  finished() {
    this.queue = Math.max(0, this.queue - 1);
    if (this.queue === 0) this.onIdle();
  },
  unlock() {
    // Must run inside a tap on iOS; later plays on the same element are then allowed.
    this.audio.src = silentWavUrl();
    this.audio.play().catch(() => {});
  },
  cancel() {
    this.gen++;
    this.items = [];
    this.playing = false;
    try { this.audio.pause(); } catch { /* ignore */ }
    if (window.speechSynthesis) speechSynthesis.cancel();
    this.queue = 0;
    stopBtn.hidden = true;
    call.update();
  },
  onIdle() {
    stopBtn.hidden = true;
    call.update();
    // In one-to-one mode the conversation keeps going hands-free.
    if ((handsFree.checked || call.open) && !busy) startListening();
  },
};
if (window.speechSynthesis) {
  tts.load();
  speechSynthesis.onvoiceschanged = () => tts.load();
}

function cleanForSpeech(s) {
  return s
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*|__|[*#>]/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Splits streamed text into speakable sentences, skipping fenced code blocks.
class SpeechChunker {
  constructor() { this.buf = ""; this.inCode = false; }
  push(delta) { this.buf += delta; this.drain(false); }
  flush() { this.drain(true); }
  drain(final) {
    while (this.buf) {
      if (this.inCode) {
        const end = this.buf.indexOf("```");
        if (end === -1) { if (final) this.buf = ""; return; }
        this.buf = this.buf.slice(end + 3);
        this.inCode = false;
        continue;
      }
      const fence = this.buf.indexOf("```");
      const m = /[.!?؟۔:](\s|$)|\n/.exec(this.buf);
      const sentEnd = m && (m.index + m[0].length < this.buf.length || final) ? m.index + m[0].length : -1;
      if (fence !== -1 && (sentEnd === -1 || fence < sentEnd)) {
        tts.speak(this.buf.slice(0, fence));
        this.buf = this.buf.slice(fence + 3);
        this.inCode = true;
        continue;
      }
      if (sentEnd !== -1) {
        tts.speak(this.buf.slice(0, sentEnd));
        this.buf = this.buf.slice(sentEnd);
        continue;
      }
      if (final) { tts.speak(this.buf); this.buf = ""; }
      return;
    }
  }
}

// ---------- Speech input ----------
// Preferred: record audio and transcribe on the server with Groq Whisper (works on iPhone
// Safari and handles Urdu well). Fallback: the browser's own SpeechRecognition.
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const AudioCtx = window.AudioContext || window.webkitAudioContext;
const CAN_RECORD = !!(AudioCtx && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
let micMode = CAN_RECORD ? "server" : SR ? "browser" : "none";
let listening = false;
let transcribing = false;

function whisperLang() { return recogLang.startsWith("ur") ? "ur" : "en"; }

function setListeningUI(on, label = "Listening…") {
  listening = on;
  micBtn.classList.toggle("listening", on);
  if (on) setStatus(label);
  else if (statusEl.textContent === label) setStatus("");
  call.update();
}

// iOS only allows speech output after it has been started from a tap once.
let audioUnlocked = false;
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  tts.unlock();
  if (CAN_RECORD) recorder.ensureContext();
  if (window.speechSynthesis) {
    try { speechSynthesis.speak(new SpeechSynthesisUtterance("")); } catch { /* ignore */ }
  }
}

// --- Server (Whisper) recorder with automatic end-of-speech detection ---
// Captures raw audio with Web Audio and encodes a 16 kHz mono WAV in the page. (Safari's
// MediaRecorder MP4 output is sometimes rejected by Whisper; WAV is always accepted.)
const TARGET_RATE = 16000;

function encodeWav(chunks, inputRate) {
  let length = 0;
  for (const c of chunks) length += c.length;
  const ratio = inputRate / TARGET_RATE;
  const outLength = Math.floor(length / ratio);
  const pcm = new Int16Array(outLength);
  // Merge + downsample by averaging each output window
  let chunkIdx = 0, offset = 0;
  const readSample = () => {
    while (chunkIdx < chunks.length && offset >= chunks[chunkIdx].length) { chunkIdx++; offset = 0; }
    return chunkIdx < chunks.length ? chunks[chunkIdx][offset++] : 0;
  };
  let consumed = 0;
  for (let i = 0; i < outLength; i++) {
    const until = Math.floor((i + 1) * ratio);
    let sum = 0, n = 0;
    while (consumed < until) { sum += readSample(); n++; consumed++; }
    const v = Math.max(-1, Math.min(1, n ? sum / n : 0));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buf);
  const str = (o, t) => [...t].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); str(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: "audio/wav" });
}

const recorder = {
  audioCtx: null, stream: null, source: null, processor: null, chunks: [], timer: null,
  cancelled: false, discard: false, active: false, rms: 0,

  startId: 0, failedStarts: 0,

  // Call directly inside a tap: iOS only lets a page resume audio from a user gesture.
  // After the tutor speaks, iOS puts the context into "suspended" or "interrupted".
  ensureContext(fromTap = false) {
    if (this.audioCtx && (this.audioCtx.state === "closed" || (fromTap && this.failedStarts > 0))) {
      try { this.audioCtx.close(); } catch { /* ignore */ }
      this.audioCtx = null;  // recreate inside this tap so it starts running
      this.failedStarts = 0;
    }
    this.audioCtx = this.audioCtx || new AudioCtx();
    if (this.audioCtx.state !== "running") {
      try { this.audioCtx.resume().catch(() => {}); } catch { /* ignore */ }
    }
    return this.audioCtx;
  },

  // Wait (briefly) for the context to run. resume() can hang forever on iOS outside a tap.
  waitRunning(ctx, ms = 1500) {
    if (ctx.state === "running") return Promise.resolve(true);
    return new Promise((resolve) => {
      const onChange = () => { if (ctx.state === "running") done(true); };
      const done = (ok) => { clearTimeout(timer); ctx.removeEventListener("statechange", onChange); resolve(ok); };
      const timer = setTimeout(() => done(ctx.state === "running"), ms);
      ctx.addEventListener("statechange", onChange);
      try { ctx.resume().catch(() => {}); } catch { /* ignore */ }
    });
  },

  releaseStream(stream) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
  },

  async start(fromTap = false) {
    const id = ++this.startId;
    const ctx = this.ensureContext(fromTap);
    this.releaseStream(this.stream);  // never leave an old mic stream open
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      setStatus(err.name === "NotAllowedError" ? "Microphone permission denied. Allow it in your browser settings." : `Mic error: ${err.message}`);
      return;
    }
    if (id !== this.startId) { this.releaseStream(stream); return; }  // superseded by a newer tap
    const running = await this.waitRunning(ctx);
    if (id !== this.startId) { this.releaseStream(stream); return; }
    if (!running) {
      // Typically an automatic (hands-free) restart right after the tutor spoke on iOS.
      this.releaseStream(stream);
      this.failedStarts++;
      setStatus("Tap the robot to talk");
      return;
    }
    this.failedStarts = 0;
    this.stream = stream;
    this.chunks = [];
    this.cancelled = false;
    this.discard = false;
    this.active = true;
    this.rms = 0;
    this.source = ctx.createMediaStreamSource(stream);
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (e) => {
      if (!this.active) return;
      const input = e.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(input));
      let sum = 0;
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
      this.rms = Math.sqrt(sum / input.length);
    };
    this.source.connect(this.processor);
    this.processor.connect(ctx.destination);  // needed for onaudioprocess to fire; outputs silence
    setListeningUI(true);
    this.watchSilence();
  },

  watchSilence() {
    const started = Date.now();
    let heardSpeech = false;
    let lastLoud = Date.now();
    this.timer = setInterval(() => {
      const now = Date.now();
      call.setLevel(this.rms);
      if (this.rms > 0.02) { heardSpeech = true; lastLoud = now; }
      if (heardSpeech && now - lastLoud > 1500) this.stop();          // paused after speaking
      else if (!heardSpeech && now - started > 8000) this.stop(true);  // nothing said
      else if (now - started > 60000) this.stop();                      // hard limit
    }, 100);
  },

  stop(cancel = false) {
    if (!this.active) return;
    this.cancelled = this.cancelled || cancel;
    this.active = false;
    clearInterval(this.timer);
    try { this.source.disconnect(); this.processor.disconnect(); } catch { /* ignore */ }
    this.processor.onaudioprocess = null;
    this.releaseStream(this.stream);
    this.stream = null;
    this.finish();
  },

  async finish() {
    setListeningUI(false);
    if (this.discard) { this.discard = false; return; }
    const rate = this.audioCtx.sampleRate;
    const samples = this.chunks.reduce((n, c) => n + c.length, 0);
    if (this.cancelled || samples < rate * 0.4) {
      setStatus(this.cancelled ? "Didn't hear anything. Tap the mic and try again." : "");
      return;
    }
    const blob = encodeWav(this.chunks, rate);
    this.chunks = [];
    setStatus("Transcribing…");
    transcribing = true;
    call.update();
    try {
      const res = await api(`/api/transcribe?lang=${whisperLang()}`, {
        method: "POST", headers: { "Content-Type": "audio/wav" }, body: blob,
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 501 && SR) {
        micMode = "browser";
        setStatus("Server speech recognition unavailable; using the browser's. Tap the mic again.");
        return;
      }
      if (!res.ok) { setStatus(data.detail || `Speech recognition failed (${res.status}).`); return; }
      setStatus("");
      if (data.text) send(data.text);
      else setStatus("Didn't catch that. Try again.");
    } catch (err) {
      setStatus(`Speech recognition failed: ${err.message}`);
    } finally {
      transcribing = false;
      call.update();
    }
  },
};

// --- Browser SpeechRecognition fallback ---
let recognition = null;
if (SR) {
  recognition = new SR();
  recognition.interimResults = true;
  recognition.continuous = false;
  let finalText = "";
  recognition.onstart = () => { finalText = ""; setListeningUI(true); };
  recognition.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    textInput.value = (finalText + interim).trim();
  };
  recognition.onerror = (e) => {
    if (e.error === "not-allowed") setStatus("Microphone permission denied.");
    else if (e.error !== "no-speech" && e.error !== "aborted") setStatus(`Mic error: ${e.error}`);
  };
  recognition.onend = () => {
    setListeningUI(false);
    const text = textInput.value.trim();
    if (text) send(text);
  };
}

if (micMode === "none") {
  micBtn.disabled = true;
  $("micHint").hidden = false;
}

function startListening(fromTap = false) {
  if (listening || busy || micMode === "none") return;
  tts.cancel();
  textInput.value = "";
  if (micMode === "server") {
    recorder.start(fromTap);
  } else {
    recognition.lang = recogLang;
    try { recognition.start(); } catch { /* already started */ }
  }
}

function stopListening() {
  if (micMode === "server") recorder.stop();
  else if (recognition) recognition.stop();
}

micBtn.addEventListener("click", () => {
  unlockAudio();
  if (listening) stopListening();
  else startListening(true);
});

document.querySelectorAll(".seg-btn").forEach((btn) => {
  btn.classList.toggle("active", btn.dataset.lang === recogLang);
  btn.addEventListener("click", () => {
    recogLang = btn.dataset.lang;
    storageSet("tutor.lang", recogLang);
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
  });
});

// ---------- One-to-one call with the robot ----------
// `var` so earlier helpers (setStatus) can safely check it before this line runs.
var call = {
  open: false,
  el: $("call"),
  ring: document.querySelector("#call .ring"),
  lastStatus: "",

  state() {
    if (listening) return "listening";
    if (tts.queue > 0) return "speaking";
    if (busy || transcribing) return "thinking";
    return "idle";
  },

  update(statusText) {
    if (statusText !== undefined) this.lastStatus = statusText || "";
    if (!this.open) return;
    const state = this.state();
    this.el.dataset.state = state;
    const labels = {
      listening: ["Listening… pause when you're done", "Tap to send"],
      thinking: [transcribing ? "Understanding what you said…" : "Thinking…", "Please wait"],
      speaking: ["Speaking… tap to interrupt", "Tap to interrupt"],
      idle: [this.lastStatus || "Tap the robot to start talking", "Tap to talk"],
    };
    let [status, button] = labels[state];
    // Show tool progress / rate-limit waits while thinking
    if (state === "thinking" && !transcribing && this.lastStatus) status = this.lastStatus;
    $("callStatus").textContent = status;
    $("callTalk").textContent = button;
    $("callTalk").disabled = state === "thinking";
    $("robotBtn").setAttribute("aria-label", button);
    if (state !== "listening") this.setLevel(0);
  },

  setLevel(rms) {
    if (!this.open || !this.ring) return;
    this.ring.style.setProperty("--level", Math.min(1, rms * 12).toFixed(2));
  },

  caption(who, text) {
    const el = who === "you" ? $("capYou") : $("capTutor");
    const clean = cleanForSpeech(text.replace(/```[\s\S]*?(```|$)/g, " [code on screen] "));
    el.textContent = clean ? (who === "you" ? `You: ${clean}` : clean) : "";
    el.dir = URDU_RE.test(clean.slice(0, 60)) ? "rtl" : "auto";
  },

  toggleTalk() {
    unlockAudio();
    if (listening) stopListening();
    else if (tts.queue > 0) { tts.cancel(); startListening(true); }
    else if (!busy && !transcribing) startListening(true);
  },

  start() {
    unlockAudio();
    this.open = true;
    this.el.hidden = false;
    $("callLang").textContent = recogLang.startsWith("ur") ? "اردو · Urdu" : "English";
    this.lastStatus = "";
    this.caption("you", "");
    this.caption("tutor", "");
    this.update();
    if (micMode === "none") {
      $("callStatus").textContent = "Voice input isn't supported in this browser.";
      return;
    }
    startListening();
  },

  end() {
    this.open = false;
    this.el.hidden = true;
    if (micMode === "server") {
      recorder.startId++;  // cancel a start that is still waiting for the mic
      if (listening) { recorder.discard = true; recorder.stop(true); }
      recorder.releaseStream(recorder.stream);
    } else if (listening) {
      stopListening();
    }
    tts.cancel();
    setStatus("");
  },
};

$("callBtn").addEventListener("click", () => call.start());
$("callEnd").addEventListener("click", () => call.end());
$("robotBtn").addEventListener("click", () => call.toggleTalk());
$("callTalk").addEventListener("click", () => call.toggleTalk());
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && call.open) call.end(); });

// ---------- Chat ----------
let busy = false;

async function send(text, mode = null) {
  if (busy || !text.trim()) return;
  busy = true;
  tts.cancel();
  textInput.value = "";
  addMessage("user", text);
  tts.newReply();
  call.caption("you", text);
  call.caption("tutor", "");
  call.update();
  const el = addMessage("assistant", "");
  el.innerHTML = '<span class="muted">…</span>';
  let full = "";
  let failed = false;
  const chunker = new SpeechChunker();

  try {
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, mode, session_id: sessionId, urdu_voice: tts.hasUrdu() }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!line.startsWith("data:")) continue;
        const event = JSON.parse(line.slice(5));
        if (event.type === "text") {
          setStatus("");
          full += event.text;
          setMessageText(el, full);
          call.caption("tutor", full);
          chunker.push(event.text);
        } else if (event.type === "status") {
          setStatus(event.text);
          if (full && !full.endsWith("\n")) { full += "\n\n"; chunker.push("\n"); }
        } else if (event.type === "info") {
          addMessage("info", event.text);
        } else if (event.type === "error") {
          addMessage("error", event.text);
          call.caption("tutor", event.text);
          failed = true;
        }
      }
    }
  } catch (err) {
    addMessage("error", `Connection problem: ${err.message}`);
    call.caption("tutor", `Connection problem: ${err.message}`);
    failed = true;
  } finally {
    chunker.flush();
    if (!full) el.remove();
    setStatus("");
    busy = false;
    if (tts.skipped && !failed) setStatus("Rest of the reply is on screen (saving HD voice credits).");
    call.update(failed ? "Something went wrong. Tap the robot to try again."
      : tts.skipped ? "Rest of the reply is on screen (saving HD voice credits)." : "");
    if (tts.useHd()) setTimeout(refreshHdUsage, 4000);
    // Don't reopen the mic automatically after an error (avoids an error loop in 1:1 mode).
    if (tts.queue === 0 && !failed) tts.onIdle();
    if (!$("progressPanel").hidden) loadProgress();
  }
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  unlockAudio();
  if (listening) stopListening();
  else send(textInput.value);
});
stopBtn.addEventListener("click", () => tts.cancel());

// ---------- Modes ----------
async function loadModes() {
  const res = await api("/api/modes");
  if (!res.ok) return;
  const modes = await res.json();
  const nav = $("modes");
  for (const [key, label] of Object.entries(modes)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = label;
    b.addEventListener("click", () => {
      unlockAudio();
      const extra = textInput.value.trim();
      send(extra ? `${label}: ${extra}` : label, key);
    });
    nav.appendChild(b);
  }
}

// ---------- Study library ----------
const library = { topics: null, subject: "All", query: "" };

async function openLibrary() {
  $("libraryPanel").hidden = false;
  if (!library.topics) {
    $("libraryBody").innerHTML = '<p class="muted">Loading…</p>';
    const res = await api("/api/library");
    if (!res.ok) { $("libraryBody").innerHTML = '<p class="muted">Could not load the library.</p>'; return; }
    library.topics = (await res.json()).topics;
    renderSubjects();
  }
  renderLibrary();
}

function renderSubjects() {
  const subjects = ["All", ...new Set(library.topics.map((t) => t.subject))];
  const box = $("librarySubjects");
  box.innerHTML = "";
  for (const sub of subjects) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = sub;
    b.setAttribute("aria-pressed", String(sub === library.subject));
    b.addEventListener("click", () => {
      library.subject = sub;
      box.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
      renderLibrary();
    });
    box.appendChild(b);
  }
}

function practice(text, mode = null) {
  $("libraryPanel").hidden = true;
  unlockAudio();
  send(text, mode);
}

function renderLibrary() {
  const q = library.query.trim().toLowerCase();
  const match = (text) => !q || text.toLowerCase().includes(q);
  const body = $("libraryBody");
  body.innerHTML = "";
  let topicCount = 0, qaCount = 0;
  for (const t of library.topics) {
    if (library.subject !== "All" && t.subject !== library.subject) continue;
    const topicHit = match(t.title) || t.key_points.some(match);
    const qas = t.qa.filter((x) => topicHit || match(x.q) || match(x.a));
    if (!topicHit && !qas.length) continue;
    topicCount++;
    qaCount += qas.length;

    const d = document.createElement("details");
    d.className = "lib-topic";
    if (q) d.open = true;
    d.innerHTML = `<summary><span>${escapeHtml(t.title)}</span><span class="meta">${escapeHtml(t.subject)} · ${qas.length} Q&amp;A</span></summary>`;
    const inner = document.createElement("div");
    inner.className = "lib-inner";
    let html = `<h4>Key points</h4><ul class="points">${t.key_points.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`;
    if (t.commands.length) html += `<h4>Commands</h4><pre>${escapeHtml(t.commands.join("\n"))}</pre>`;
    html += `<h4>Questions</h4>`;
    inner.innerHTML = html;
    for (const item of qas) {
      const qd = document.createElement("details");
      qd.className = "qa";
      qd.innerHTML = `<summary>${escapeHtml(item.q)}<span class="lvl">${escapeHtml(item.level)}</span></summary>
        <p class="answer">${escapeHtml(item.a)}</p>`;
      const actions = document.createElement("div");
      actions.className = "qa-actions";
      const practiceBtn = document.createElement("button");
      practiceBtn.type = "button";
      practiceBtn.className = "ghost";
      practiceBtn.textContent = "🎯 Practice with tutor";
      practiceBtn.addEventListener("click", () => practice(
        `Ask me this question, wait for my answer, then score it out of 10 against the library's model answer and show me a better answer: "${item.q}"`));
      const listenBtn = document.createElement("button");
      listenBtn.type = "button";
      listenBtn.className = "ghost";
      listenBtn.textContent = "🔊 Listen";
      listenBtn.addEventListener("click", () => { unlockAudio(); tts.cancel(); tts.speak(item.a); });
      actions.append(practiceBtn, listenBtn);
      qd.appendChild(actions);
      inner.appendChild(qd);
    }
    const ta = document.createElement("div");
    ta.className = "lib-topic-actions";
    for (const [label, text, mode] of [
      ["📖 Teach me this", `Teach me ${t.title}`, "teach"],
      ["❓ Quiz me", `Quiz me on ${t.title}`, "quiz"],
      ["💼 Interview me", `Interview me on ${t.title}`, "interview"],
    ]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ghost";
      b.textContent = label;
      b.addEventListener("click", () => practice(text, mode));
      ta.appendChild(b);
    }
    inner.appendChild(ta);
    d.appendChild(inner);
    body.appendChild(d);
  }
  $("libraryCount").textContent = `${topicCount} topics · ${qaCount} questions with model answers`;
  if (!topicCount) body.innerHTML = '<p class="muted">No matches. Try another word, or ask the tutor directly.</p>';
}

$("libraryBtn").addEventListener("click", openLibrary);
$("closeLibrary").addEventListener("click", () => { $("libraryPanel").hidden = true; });
$("librarySearch").addEventListener("input", (e) => { library.query = e.target.value; renderLibrary(); });

// ---------- Progress panel ----------
async function loadProgress() {
  const res = await api("/api/progress");
  if (!res.ok) return;
  const data = await res.json();
  const body = $("progressBody");
  const lesson = data.current_lesson;
  let html = `<h3>Current lesson</h3><p class="muted">${
    lesson ? escapeHtml(`${lesson.subject} · ${lesson.topic} — next: ${lesson.step}`) : "Not started yet — try “Plan my day”."
  }</p>`;
  for (const subject of data.subjects) {
    const items = data.progress.filter((p) => p.subject === subject);
    html += `<h3>${escapeHtml(subject)}</h3>`;
    html += items.length
      ? `<ul>${items.map((p) => `<li>${escapeHtml(p.topic)} <span class="level">${escapeHtml(p.level)}</span></li>`).join("")}</ul>`
      : `<p class="muted">Not Started</p>`;
  }
  if (data.english_corrections.length) {
    html += `<h3>Recent English corrections</h3><ul>${data.english_corrections
      .map((c) => `<li><s>${escapeHtml(c.original)}</s><br>${escapeHtml(c.corrected)}</li>`).join("")}</ul>`;
  }
  if (data.notes.length) {
    html += `<h3>Notes</h3><ul>${data.notes
      .map((n) => `<li><b>${escapeHtml(n.topic)}</b>: ${escapeHtml(n.content)}</li>`).join("")}</ul>`;
  }
  body.innerHTML = html;
}

// ---- English voice settings ----
function fillVoiceList() {
  const select = $("enVoice");
  const voices = tts.englishVoices();
  select.innerHTML = "";
  select.append(new Option("Automatic (phone voice)", ""));
  if (tts.geminiVoices.length) {
    const group = document.createElement("optgroup");
    group.label = "Gemini voices (free, online)";
    for (const v of tts.geminiVoices) group.append(new Option(`${v.name} (${v.desc})`, "gemini:" + v.name));
    select.append(group);
  }
  if (tts.groqVoices.length) {
    const group = document.createElement("optgroup");
    group.label = "Groq voices (free, 10 per minute)";
    for (const name of tts.groqVoices) {
      group.append(new Option(name[0].toUpperCase() + name.slice(1), "groq:" + name));
    }
    select.append(group);
  }
  if (voices.length) {
    const group = document.createElement("optgroup");
    group.label = "Phone voices";
    for (const v of voices) group.append(new Option(`${v.name} (${v.lang})`, v.name));
    select.append(group);
  }
  const known = [...select.options].some((o) => o.value === tts.enVoiceName);
  select.value = known ? tts.enVoiceName : "";
  const natural = tts.groqVoices.length + tts.geminiVoices.length;
  $("voiceCount").textContent =
    `${natural ? natural + " natural voices · " : ""}` +
    `${voices.length} phone voice${voices.length === 1 ? "" : "s"} visible to this app.`;
}
const RATES = [0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.5];
function setRate(value) {
  tts.rate = Math.min(1.5, Math.max(0.5, Number(value) || 1));
  storageSet("tutor.rate", String(tts.rate));
  try { tts.audio.playbackRate = tts.rate; } catch { /* ignore */ }  // applies to audio already playing
  showRate();
}
function showRate() {
  const label = `${tts.rate.toFixed(1)}×`;
  $("rateValue").textContent = label;
  $("rate").value = String(tts.rate);
  $("speedBtn").textContent = `⏩ Speed ${label}`;
  $("speedCallBtn").textContent = `⏩ ${label}`;
}
function nextRate() {
  const i = RATES.findIndex((r) => r > tts.rate + 0.001);
  setRate(i === -1 ? RATES[0] : RATES[i]);
}
for (const id of ["speedBtn", "speedCallBtn"]) $(id).addEventListener("click", nextRate);
showRate();
$("voiceBtn").addEventListener("click", () => {
  tts.load();
  fillVoiceList();
  showRate();
  $("voicePanel").hidden = false;
});
$("closeVoice").addEventListener("click", () => { $("voicePanel").hidden = true; });
$("enVoice").addEventListener("change", (e) => {
  tts.enVoiceName = e.target.value;
  tts.enOk = true;  // give a newly chosen natural voice another try
  tts.enPausedUntil = 0;
  storageSet("tutor.enVoice", tts.enVoiceName);
});
$("rate").addEventListener("input", (e) => setRate(e.target.value));
$("testVoice").addEventListener("click", () => {
  unlockAudio();
  tts.cancel();
  tts.newReply();
  tts.speak("Hello Rizwan, this is how I will sound when I teach you. Shall we start?");
});
if (window.speechSynthesis) {
  speechSynthesis.addEventListener("voiceschanged", () => {
    if (!$("voicePanel").hidden) fillVoiceList();
  });
}

$("progressBtn").addEventListener("click", () => { $("progressPanel").hidden = false; loadProgress(); });
$("closePanel").addEventListener("click", () => { $("progressPanel").hidden = true; });

$("resetBtn").addEventListener("click", async () => {
  if (!confirm("Start a new conversation? Your progress, notes and lesson position are kept.")) return;
  tts.cancel();
  await api("/api/reset", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  transcript.innerHTML = "";
  addMessage("info", "New conversation started.");
});

// ---------- Restore transcript ----------
async function loadHistory() {
  const res = await api(`/api/history?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) return;
  const data = await res.json();
  for (const m of data.messages) addMessage(m.role, m.text);
}

// ---------- HD voice (ElevenLabs) switch and credits ----------
function renderHd() {
  const on = tts.hdEnabled;
  $("hdVoice").checked = on;
  const btn = $("hdCallBtn");
  btn.textContent = `HD ${on ? "On" : "Off"}`;
  btn.setAttribute("aria-pressed", String(on));
}

function setHd(on) {
  tts.hdEnabled = on;
  storageSet("tutor.hd", on ? "1" : "0");
  if (!on) tts.cancel();
  renderHd();
}

function setupHdControls() {
  if (!tts.server) return;
  $("hdLabel").hidden = false;
  $("hdCallBtn").hidden = false;
  renderHd();
  refreshHdUsage();
}

async function refreshHdUsage() {
  try {
    const res = await api("/api/tts/usage");
    const usage = res.ok ? (await res.json()).usage : null;
    if (!usage) return;
    const text = `${usage.left.toLocaleString()} of ${usage.limit.toLocaleString()} chars left`;
    $("hdUsageMain").textContent = `(${text})`;
    $("hdUsageCall").textContent = `HD voice: ${text}`;
    $("hdUsageCall").hidden = false;
  } catch { /* usage is optional */ }
}

$("hdVoice").addEventListener("change", (e) => setHd(e.target.checked));
$("hdCallBtn").addEventListener("click", () => setHd(!tts.hdEnabled));

async function loadConfig() {
  const res = await api("/api/config");
  if (!res.ok) return;
  const data = await res.json();
  tts.server = !!data.tts;
  tts.scope = data.tts_scope || "all";
  tts.budget = data.tts_reply_budget || null;
  tts.groqVoices = Array.isArray(data.english_voices) ? data.english_voices : [];
  tts.geminiVoices = Array.isArray(data.gemini_voices) ? data.gemini_voices : [];
  setupHdControls();
}

// Installable app: register the service worker (offline shell, home-screen install).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

// Modes first so a passcode prompt appears once, then server features and the transcript.
loadModes().then(loadConfig).then(loadHistory)
  .catch(() => setStatus(navigator.onLine ? "Could not reach the server. Pull down or reopen to retry." : "You're offline. Connect to the internet to talk to your tutor."));
