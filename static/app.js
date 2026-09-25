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
}

// ---------- Text-to-speech ----------
const tts = {
  voices: [],
  queue: 0,
  load() {
    this.voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  },
  hasUrdu() {
    return this.voices.some((v) => v.lang.toLowerCase().startsWith("ur"));
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
    if (!window.speechSynthesis) return;
    const clean = cleanForSpeech(text);
    if (!clean) return;
    const u = new SpeechSynthesisUtterance(clean);
    const voice = this.pickVoice(clean);
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.rate = 1.0;
    this.queue++;
    stopBtn.hidden = false;
    u.onend = u.onerror = () => {
      this.queue = Math.max(0, this.queue - 1);
      if (this.queue === 0) this.onIdle();
    };
    speechSynthesis.speak(u);
  },
  cancel() {
    if (window.speechSynthesis) speechSynthesis.cancel();
    this.queue = 0;
    stopBtn.hidden = true;
  },
  onIdle() {
    stopBtn.hidden = true;
    if (handsFree.checked && !busy && recognition) startListening();
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

// ---------- Speech recognition ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;

if (SR) {
  recognition = new SR();
  recognition.interimResults = true;
  recognition.continuous = false;
  let finalText = "";
  recognition.onstart = () => { listening = true; finalText = ""; micBtn.classList.add("listening"); setStatus("Listening…"); };
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
    listening = false;
    micBtn.classList.remove("listening");
    if (statusEl.textContent === "Listening…") setStatus("");
    const text = textInput.value.trim();
    if (text) send(text);
  };
} else {
  micBtn.disabled = true;
  $("micHint").hidden = false;
}

function startListening() {
  if (!recognition || listening || busy) return;
  tts.cancel();
  textInput.value = "";
  recognition.lang = recogLang;
  try { recognition.start(); } catch { /* already started */ }
}

micBtn.addEventListener("click", () => {
  if (listening) recognition.stop();
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

// ---------- Chat ----------
let busy = false;

async function send(text, mode = null) {
  if (busy || !text.trim()) return;
  busy = true;
  tts.cancel();
  textInput.value = "";
  addMessage("user", text);
  const el = addMessage("assistant", "");
  el.innerHTML = '<span class="muted">…</span>';
  let full = "";
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
          chunker.push(event.text);
        } else if (event.type === "status") {
          setStatus(event.text);
          if (full && !full.endsWith("\n")) { full += "\n\n"; chunker.push("\n"); }
        } else if (event.type === "info") {
          addMessage("info", event.text);
        } else if (event.type === "error") {
          addMessage("error", event.text);
        }
      }
    }
  } catch (err) {
    addMessage("error", `Connection problem: ${err.message}`);
  } finally {
    chunker.flush();
    if (!full) el.remove();
    setStatus("");
    busy = false;
    if (tts.queue === 0) tts.onIdle();
    if (!$("progressPanel").hidden) loadProgress();
  }
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (listening) recognition.stop();
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

// Modes first so a passcode prompt appears once, then the transcript.
loadModes().then(loadHistory);
