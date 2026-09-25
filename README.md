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
- **Text-to-speech runs in the browser** (speechSynthesis), free and with no extra key. Code blocks and CLI commands appear on screen but are not read aloud.
- **Groq** powers the tutor by default, through the official `groq` Python SDK. The default model is `openai/gpt-oss-120b`, which is fast, good at tool calling, and multilingual. Replies stream and the model's reasoning is hidden. You can change the model with `GROQ_MODEL`.
- **Groq free plan:** requests are kept small so they fit Groq's free-tier limits (for `gpt-oss-120b`, 8,000 tokens per minute). Groq uses a condensed prompt ([`prompts/compact_system_prompt.md`](prompts/compact_system_prompt.md)) and only the recent part of the conversation; lesson, progress and notes stay available through the memory tools. If Groq asks the app to wait a few seconds, it shows "waiting" and retries on its own. For heavier daily use, Groq's pay-as-you-go Dev tier removes these limits.
- **Claude** is also supported. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` to use it instead. It uses the official `anthropic` SDK with adaptive thinking and prompt caching. Each provider keeps its own conversation history. Progress, notes, and lesson position are shared.
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
4. Redeploy. Open the site, and enter the passcode once per browser when asked.

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
| `ANTHROPIC_API_KEY` | – | Only needed with `LLM_PROVIDER=anthropic` |
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model |
| `EFFORT` | `medium` | Claude effort: `low` / `medium` / `high` / `xhigh` / `max` |
| `ENABLE_FALLBACK` | `true` | Claude server-side refusal fallback (Anthropic beta) |
| `DATABASE_URL` | – | Postgres URL (Supabase transaction pooler, Neon, ...). Used instead of SQLite when set |
| `DB_PATH` | `tutor.db` | SQLite file for memory |
| `APP_PASSCODE` | – | If set, the API requires this passcode. Required on Vercel |
| `MAX_HISTORY_MESSAGES` | `80` | After this many messages the chat starts fresh. Progress, notes, and lesson position are kept |

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

**Urdu voice output** depends on your device having an Urdu text-to-speech voice. If it doesn't, the tutor automatically writes Urdu in Roman Urdu so the English voice can read it.

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
  main.py      FastAPI routes: UI, /api/chat (SSE), /api/progress, /api/history, /api/reset
  groq_agent.py  Groq streaming + tool-calling loop (default provider)
  agent.py     Claude streaming + tool loop, prompt caching, refusal handling
  stt.py       speech-to-text with Groq Whisper
  tools.py     memory tool schemas, validation, execution
  memory.py    SQLite / Postgres storage (history, progress, notes, lesson, corrections, profile)
  modes.py     quick learning-mode commands
  config.py    settings from .env
prompts/master_system_prompt.md   the tutor's personality and teaching rules
static/        index.html, app.js, styles.css (voice UI)
tests/         pytest suite
```

## Roadmap

1. **Better voices**: neural TTS with a natural Urdu voice.
2. **Knowledge base (RAG)**: upload your notes, vendor docs, and configs so the tutor can cite them.
3. **Python sandbox**: run and check the exercises the tutor gives.
4. **Network lab integration**: connect to a GNS3 / EVE-NG / Containerlab lab with Netmiko to run `show` commands during troubleshooting drills.
5. **Pronunciation scoring** for English practice.
6. **Study calendar**: push the daily plan to Google Calendar.
