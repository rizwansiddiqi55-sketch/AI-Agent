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
  server: false,     // Azure voice available (set from /api/config)
  items: [],
  playing: false,
  gen: 0,            // bumped on cancel so stale playback is ignored
  audio: new Audio(),
  load() {
    this.voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  },
  hasUrdu() {
    return this.server || this.voices.some((v) => v.lang.toLowerCase().startsWith("ur"));
  },
  pickVoice(text) {
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
    if (this.server) this.serverSpeak(clean);
    else this.deviceSpeak(clean);
  },
  deviceSpeak(clean) {
    if (!window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(clean);
    const voice = this.pickVoice(clean);
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.rate = 1.0;
    this.started();
    u.onend = u.onerror = () => this.finished();
    speechSynthesis.speak(u);
  },
  serverSpeak(clean) {
    // Start fetching right away so the next sentence is ready when the current one ends.
    const promise = api("/api/tts", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: clean }),
    }).then(async (res) => {
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      return res.blob();
    });
    promise.catch(() => {});  // handled in playNext
    this.items.push({ text: clean, promise });
    this.started();
    if (!this.playing) this.playNext(this.gen);
  },
  async playNext(gen) {
    const item = this.items.shift();
    if (!item) { this.playing = false; return; }
    this.playing = true;
    let url = null;
    try {
      const blob = await item.promise;
      if (gen !== this.gen) return;
      url = URL.createObjectURL(blob);
      this.audio.src = url;
      await new Promise((resolve, reject) => {
        this.audio.onended = resolve;
        this.audio.onerror = () => reject(new Error("audio playback failed"));
        this.audio.play().catch(reject);
      });
    } catch (err) {
      if (gen !== this.gen) return;
      // Fall back to the device voice for this and the remaining sentences.
      this.server = false;
      setStatus(`Natural voice unavailable (${err.message}). Using the device voice.`);
      const rest = [item, ...this.items];
      this.items = [];
      this.playing = false;
      this.queue = Math.max(0, this.queue - rest.length);
      rest.forEach((i) => this.deviceSpeak(i.text));
      if (this.queue === 0) this.onIdle();
      return;
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
    if (gen !== this.gen) return;
    this.finished();
    this.playNext(gen);
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

  ensureContext() {
    this.audioCtx = this.audioCtx || new AudioCtx();
    if (this.audioCtx.state === "suspended") this.audioCtx.resume().catch(() => {});
    return this.audioCtx;
  },

  async start() {
    const ctx = this.ensureContext();  // created/resumed while still inside the tap on iOS
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      setStatus(err.name === "NotAllowedError" ? "Microphone permission denied. Allow it in your browser settings." : `Mic error: ${err.message}`);
      return;
    }
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    this.chunks = [];
    this.cancelled = false;
    this.discard = false;
    this.active = true;
    this.rms = 0;
    this.source = ctx.createMediaStreamSource(this.stream);
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
    this.stream.getTracks().forEach((t) => t.stop());
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

function startListening() {
  if (listening || busy || micMode === "none") return;
  tts.cancel();
  textInput.value = "";
  if (micMode === "server") {
    recorder.start();
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
  else startListening();
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
    else if (tts.queue > 0) { tts.cancel(); startListening(); }
    else if (!busy && !transcribing) startListening();
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
    if (listening) {
      if (micMode === "server") { recorder.discard = true; recorder.stop(true); } else stopListening();
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
    call.update(failed ? "Something went wrong. Tap the robot to try again." : "");
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

async function loadConfig() {
  const res = await api("/api/config");
  if (!res.ok) return;
  const data = await res.json();
  tts.server = !!data.tts;
}

// Modes first so a passcode prompt appears once, then server features and the transcript.
loadModes().then(loadConfig).then(loadHistory);
