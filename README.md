# AI Voice Tutor

A bilingual (English / Urdu / Roman Urdu) voice assistant built for Rizwan. It acts as a personal assistant, English speaking coach, mock interviewer, and technical mentor for **AI, Python, Networking (Cisco, Fortinet, Palo Alto), and Network Security**.

You talk to it in the browser. It answers out loud and remembers your lesson position, progress levels, notes, and English corrections between sessions. When you say *"Continue my course"*, it picks up where you left off.

The tutor's behaviour comes from the master system prompt in [`prompts/master_system_prompt.md`](prompts/master_system_prompt.md). Edit that file to change how it teaches.

## How it works

```
Browser mic ── recorded audio ──► /api/transcribe (Groq Whisper, en / ur) ──► text
   └─► POST /api/chat (streaming) ──► FastAPI ──► Groq (default) or Claude
                                         │  tools: progress, lesson, notes, English corrections
                                         └─► Postgres (Neon) or SQLite
   ◄── streamed reply ── shown on screen + read aloud sentence by sentence (speechSynthesis)
```

- **Speech-to-text uses Groq Whisper** (`whisper-large-v3`). The page records your voice, stops automatically when you pause, and the server transcribes it. This works in every modern browser, including **iPhone Safari**, and handles Urdu much better than browser dictation. If `GROQ_API_KEY` isn't set, the page falls back to the browser's built-in speech recognition (Chrome/Edge).
- **Urdu voice with ElevenLabs (free plan works):** set `ELEVENLABS_API_KEY` and Urdu sentences are spoken with ElevenLabs' **Eleven v3** model (the ElevenLabs model that supports Urdu). By default only Urdu sentences use ElevenLabs and English uses the device voice, which makes the free ~10,000 characters/month last much longer (`ELEVENLABS_SCOPE=all` to use it for everything). If the quota runs out, the app switches to the device voice automatically. To stretch the free credits: the tutor keeps spoken Urdu to 2–3 short sentences (longer details go on screen), each reply uses at most `ELEVENLABS_REPLY_CHAR_BUDGET` characters of HD voice, repeated sentences are replayed from memory, and an **HD Urdu voice** switch (with the characters left this month, if the key has the "User: read" permission) lets you turn it off any time.
- **Choose the English voice and speed:** tap **🔊 Voice** under the message box. **Natural voices** (Troy, Austin, Daniel, Autumn, Diana, Hannah) come from Groq's Orpheus English model through the same `GROQ_API_KEY` (free tier; long sentences are split into 200-character pieces). The first time, accept the model's terms once in the Groq console (Playground → `canopylabs/orpheus-v1-english`). **Phone voices** are the device's built-in voices (iPhone hides most downloaded Enhanced/Premium voices from web apps). **⏩ Speed** (main screen and one-to-one) cycles 0.7×–1.5×; the choice is saved on the device and applies to every voice. If a natural voice fails, the app falls back to the phone voice.
- **Text-to-speech can also use Azure Speech neural voices** when `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are set. Urdu sentences are spoken by a native Pakistani Urdu voice (`ur-PK-AsadNeural` by default), and English sentences by an English neural voice. The tutor then writes Urdu in Urdu script so it is pronounced correctly. Without Azure, the device's built-in voices are used; phones often have no Urdu voice, so the tutor falls back to Roman Urdu. Code blocks and CLI commands appear on screen but are not read aloud.
- **Groq** powers the tutor by default, through the official `groq` Python SDK. The default model is `openai/gpt-oss-120b`, which is fast, good at tool calling, and multilingual. Replies stream and the model's reasoning is hidden. You can change the model with `GROQ_MODEL`.
- **Groq free plan:** requests are kept small so they fit Groq's free-tier limits (for `gpt-oss-120b`, 8,000 tokens per minute). Groq uses a condensed prompt ([`prompts/compact_system_prompt.md`](prompts/compact_system_prompt.md)) and only the recent part of the conversation; lesson, progress and notes stay available through the memory tools. If Groq asks the app to wait a few seconds, it shows "waiting" and retries on its own. For heavier daily use, Groq's pay-as-you-go Dev tier removes these limits.
- **Claude** is also supported. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` to use it instead. It uses the official `anthropic` SDK with adaptive thinking and prompt caching. Each provider keeps its own conversation history. Progress, notes, and lesson position are shared.
- **Study library** ([`knowledge/`](knowledge)): 21 topics and 100+ interview/practice questions with model answers across networking (subnetting, VLAN/STP, routing, OSPF, BGP, EIGRP, FHRP, NAT/ACL/DHCP/DNS, troubleshooting, SD-WAN), security (firewalls with FortiGate/Palo Alto, IPsec VPN, NAC/802.1X/ISE, Zero Trust), Python and network automation, AI (LLMs, RAG, agents/MCP), English corrections and interview skills. The tutor uses it through the `search_knowledge` and `get_practice_questions` tools, and you can browse it with the **📚 Library** button, reveal answers, listen to them, or tap **Practice with tutor** to be asked and scored. Add more by editing or adding JSON files in `knowledge/` (same format).
- **Memory tools** let the tutor save and read its own progress data: `update_progress`, `set_current_lesson` / `get_current_lesson`, `save_note` / `get_notes`, `log_english_correction`, and `get_learner_profile`.

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd AI-Agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then add your GROQ_API_KEY
uvicorn app.main:app --reload
```

Open **http://localhost:8000** and allow microphone access.

> To use it from your phone on the same Wi-Fi, run `uvicorn app.main:app --host 0.0.0.0`. Note that browsers only allow the microphone on `localhost` or HTTPS, so for phone use put it behind HTTPS (for example a tunnel such as `cloudflared` or `ngrok`, or a small cloud deployment).

### Deploy on Vercel

The repo is ready to deploy on Vercel as-is. Vercel detects the FastAPI app at `app/main.py`, and `vercel.json` allows responses of up to 5 minutes.

1. Import the GitHub repo into Vercel.
2. **Project → Settings → Environment Variables**: add `GROQ_API_KEY` and `APP_PASSCODE`. Without `APP_PASSCODE`, the API refuses all requests on Vercel, so nobody else can spend your API credit.
3. **Database (keeps your progress and notes permanently)**. Any Postgres works. Without one the app uses a temporary SQLite file in `/tmp` that resets often.
   - **Supabase (free plan):** create a project, then click **Connect** at the top of the dashboard and copy the **Transaction pooler** connection string (port `6543`). Replace `[YOUR-PASSWORD]` with your database password; if the password has symbols like `@`, `#`, or `/`, URL-encode them (for example `@` → `%40`). Add it in Vercel as `DATABASE_URL`. Don't use the "Direct connection" string, because it is IPv6-only and Vercel can't reach it. The app creates its tables on first use and turns on row-level security for them.
   - **Neon:** Storage → Create Database → Neon, and connect it to this project (sets `DATABASE_URL`).
4. **Optional, recommended for Urdu: Azure Speech voice.** In the Azure portal create a **Speech service** resource (Free F0 tier: 500,000 characters/month), then from **Keys and Endpoint** copy **Key 1** and the **Location/Region** (for example `eastus`). Add them in Vercel as `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION`.
5. Redeploy. Open the site, and enter the passcode once per browser when asked.

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` or `anthropic` |
| `GROQ_API_KEY` | – | Your Groq API key (from console.groq.com/keys) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Any Groq chat model that supports tool calling, e.g. `llama-3.3-70b-versatile` |
| `GROQ_REASONING_EFFORT` | `low` | `low` / `medium` / `high`. Only sent to reasoning models (gpt-oss, qwen3) |
| `GROQ_HISTORY_CHARS` | `6000` | How much recent conversation is sent per request (~4 characters per token) |
| `GROQ_MAX_TOKENS` | `2048` | Max reply length, including hidden reasoning |
| `GROQ_STT_MODEL` | `whisper-large-v3` | Speech recognition model (`whisper-large-v3-turbo` is faster) |
| `GROQ_TTS_MODEL` | `canopylabs/orpheus-v1-english` | Natural English voices offered in 🔊 Voice |
| `ELEVENLABS_API_KEY` | – | ElevenLabs key: natural Urdu voice (takes priority over Azure) |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` | Voice to use. On the **free plan** only ElevenLabs' premade (default) voices work through the API; Voice Library voices need a paid plan, and the app falls back to the default voice automatically |
| `ELEVENLABS_MODEL` | `eleven_v3` | Must support Urdu; v3 does |
| `ELEVENLABS_SCOPE` | `urdu` | `urdu` = only Urdu sentences (saves quota), `all` = every sentence |
| `ELEVENLABS_REPLY_CHAR_BUDGET` | `350` | Max characters per reply sent to ElevenLabs; the rest stays on screen |
| `AZURE_SPEECH_KEY` | – | Azure Speech key: enables natural Urdu and English voices |
| `AZURE_SPEECH_REGION` | – | Azure Speech region, e.g. `eastus` |
| `AZURE_URDU_VOICE` | `ur-PK-AsadNeural` | Urdu voice (`ur-PK-UzmaNeural` for a female voice) |
| `AZURE_ENGLISH_VOICE` | `en-US-AndrewNeural` | English voice (e.g. `en-US-AvaNeural`, `en-GB-RyanNeural`) |
| `AZURE_SPEECH_RATE` | `0%` | Speaking speed, e.g. `-10%` for slower |
| `ANTHROPIC_API_KEY` | – | Only needed with `LLM_PROVIDER=anthropic` |
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model |
| `EFFORT` | `medium` | Claude effort: `low` / `medium` / `high` / `xhigh` / `max` |
| `ENABLE_FALLBACK` | `true` | Claude server-side refusal fallback (Anthropic beta) |
| `DATABASE_URL` | – | Postgres URL (Supabase transaction pooler, Neon, ...). Used instead of SQLite when set |
| `DB_PATH` | `tutor.db` | SQLite file for memory |
| `APP_PASSCODE` | – | If set, the API requires this passcode. Required on Vercel |
| `MAX_HISTORY_MESSAGES` | `80` | After this many messages the chat starts fresh. Progress, notes, and lesson position are kept |

## Install it as an app (iPhone / Android)

The tutor is an installable web app (PWA): it gets its own robot icon, opens full screen without browser bars, and updates automatically.

- **iPhone / iPad:** open the site in **Safari** → tap **Share** → **Add to Home Screen** → **Add**. Open it from the new "AI Tutor" icon and allow the microphone once.
- **Android:** open the site in **Chrome** → menu **⋮** → **Install app** (or **Add to Home screen**).

The installed app keeps its own storage, so it asks for the passcode once more the first time. A native App Store build would need a Mac with Xcode and an Apple Developer account (for example by wrapping this site with Capacitor).

## Using it

- **Mic button**: tap, speak, and pause. Recording stops by itself after about 1.5 seconds of silence (or tap again), then the tutor replies by voice.
- **EN / اردو** switch: tells speech recognition which language you're speaking. You can always *type* in any language, including Roman Urdu.
- **Hands-free**: the mic reopens automatically after the tutor finishes speaking, so it works like a phone call.
- **Mode chips**: Teach me, Explain simply, Deep dive, Quiz me, Give me a lab, Interview me, Correct my English, Speak Urdu, Speak English, Translate, Revise, Test me, Homework, Continue, Plan my day. If you type a topic first (for example "BGP") and then tap a chip, the tutor uses that topic.
- **Progress**: shows the current lesson, the level for each subject and topic (Not Started → Mastered), recent English corrections, and notes.
- **New chat**: clears the conversation but keeps everything the tutor has learned about you.

Example things to say:

- "OSPF mujhe properly samajh nahi aa raha."
- "What should I study today?"
- "Start interview for a Senior Network Security Engineer role."
- "Correct my English: I am working in UAE from 2015."
- "Give me a lab on BGP route filtering with prefix lists."
- "Explain how a RAG system works, simply."

**Urdu voice output:** with Azure Speech configured, Urdu is spoken by a native Pakistani Urdu neural voice. Without it, the app depends on the device having an Urdu voice; if it doesn't, the tutor writes Urdu in Roman Urdu so the English voice can read it.

## Development

```bash
pip install -r requirements-dev.txt
pytest
# optional: also run the memory tests against Postgres
TEST_DATABASE_URL=postgresql://user@localhost/test pytest
```

The tests use fake Claude and Groq API responses, so they need no API key or network.

```
app/
  main.py      FastAPI routes: UI, /api/chat (SSE), /api/library, /api/progress, /api/history, /api/reset
  knowledge.py study library loader, keyword search and practice-question picker
  groq_agent.py  Groq streaming + tool-calling loop (default provider)
  agent.py     Claude streaming + tool loop, prompt caching, refusal handling
  stt.py       speech-to-text with Groq Whisper
  tts.py       text-to-speech with ElevenLabs (Eleven v3) or Azure neural voices
  tools.py     memory tool schemas, validation, execution
  memory.py    SQLite / Postgres storage (history, progress, notes, lesson, corrections, profile)
  modes.py     quick learning-mode commands
  config.py    settings from .env
prompts/master_system_prompt.md   the tutor's personality and teaching rules
knowledge/     study library: topic notes and Q&A with model answers (JSON)
static/        index.html, app.js, styles.css (voice UI), manifest + sw.js + icons (installable app)
tests/         pytest suite
```

## Roadmap

1. **Voice choice in the UI**: pick male/female Urdu voice and speaking speed from the page.
2. **Knowledge base (RAG)**: upload your notes, vendor docs, and configs so the tutor can cite them.
3. **Python sandbox**: run and check the exercises the tutor gives.
4. **Network lab integration**: connect to a GNS3 / EVE-NG / Containerlab lab with Netmiko to run `show` commands during troubleshooting drills.
5. **Pronunciation scoring** for English practice.
6. **Study calendar**: push the daily plan to Google Calendar.
