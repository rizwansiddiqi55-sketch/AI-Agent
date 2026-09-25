# AI Voice Tutor

A bilingual (English / Urdu / Roman Urdu) voice assistant built for Rizwan. It acts as a personal assistant, English speaking coach, mock interviewer, and technical mentor for **AI, Python, Networking (Cisco, Fortinet, Palo Alto), and Network Security**.

You talk to it in the browser. It answers out loud and remembers your lesson position, progress levels, notes, and English corrections between sessions. When you say *"Continue my course"*, it picks up where you left off.

The tutor's behaviour comes from the master system prompt in [`prompts/master_system_prompt.md`](prompts/master_system_prompt.md). Edit that file to change how it teaches.

## How it works

```
Browser mic ── Web Speech API (en-US / ur-PK) ──► text
   └─► POST /api/chat (streaming) ──► FastAPI ──► Claude (Anthropic API)
                                         │  tools: progress, lesson, notes, English corrections
                                         └─► SQLite (tutor.db)
   ◄── streamed reply ── shown on screen + read aloud sentence by sentence (speechSynthesis)
```

- **Speech-to-text and text-to-speech run in the browser** (Web Speech API), so they are free and need no extra keys. Use **Chrome or Edge** for voice input. Code blocks and CLI commands appear on screen but are not read aloud.
- **Claude** powers the tutor through the official `anthropic` Python SDK. It streams replies and uses adaptive thinking. The long system prompt is cached (prompt caching) so every turn stays fast and cheap. Server-side refusal fallback is turned on by default.
- **Memory tools** let the tutor save and read its own progress data: `update_progress`, `set_current_lesson` / `get_current_lesson`, `save_note` / `get_notes`, `log_english_correction`, and `get_learner_profile`.

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd AI-Agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

Open **http://localhost:8000** in Chrome or Edge and allow microphone access.

> To use it from your phone on the same Wi-Fi, run `uvicorn app.main:app --host 0.0.0.0`. Note that browsers only allow the microphone on `localhost` or HTTPS, so for phone use put it behind HTTPS (for example a tunnel such as `cloudflared` or `ngrok`, or a small cloud deployment).

### Deploy on Vercel

The repo is ready to deploy on Vercel as-is. Vercel detects the FastAPI app at `app/main.py`, and `vercel.json` allows responses of up to 5 minutes.

1. Import the GitHub repo into Vercel.
2. **Project → Settings → Environment Variables**: add `ANTHROPIC_API_KEY` and `APP_PASSCODE`. Without `APP_PASSCODE`, the API refuses all requests on Vercel, so nobody else can spend your API credit.
3. **Project → Storage → Create Database → Neon (Postgres)**: connect it to the project. This sets `DATABASE_URL`, which keeps your progress and notes permanently. Without it the app uses a temporary SQLite file in `/tmp` that resets often.
4. Redeploy. Open the site, and enter the passcode once per browser when asked.

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Your Anthropic API key (required) |
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model |
| `EFFORT` | `medium` | `low` / `medium` / `high` / `xhigh` / `max`. Lower is faster and cheaper; higher gives deeper reasoning |
| `ENABLE_FALLBACK` | `true` | Server-side refusal fallback (Anthropic beta) |
| `DATABASE_URL` | – | Postgres URL (e.g. Neon). Used instead of SQLite when set |
| `DB_PATH` | `tutor.db` | SQLite file for memory |
| `APP_PASSCODE` | – | If set, the API requires this passcode. Required on Vercel |
| `MAX_HISTORY_MESSAGES` | `80` | After this many messages the chat starts fresh. Progress, notes, and lesson position are kept |

## Using it

- **Mic button**: tap, speak, and pause. The tutor replies by voice. Tap again to stop early.
- **EN / اردو** switch: sets the speech-recognition language. You can always *type* in any language, including Roman Urdu.
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

The tests use a fake Claude client, so they need no API key or network.

```
app/
  main.py      FastAPI routes: UI, /api/chat (SSE), /api/progress, /api/history, /api/reset
  agent.py     Claude streaming + tool loop, prompt caching, refusal handling
  tools.py     memory tool schemas, validation, execution
  memory.py    SQLite / Postgres storage (history, progress, notes, lesson, corrections, profile)
  modes.py     quick learning-mode commands
  config.py    settings from .env
prompts/master_system_prompt.md   the tutor's personality and teaching rules
static/        index.html, app.js, styles.css (voice UI)
tests/         pytest suite
```

## Roadmap

1. **Better voices**: optional server-side speech (Whisper-class STT, neural TTS with a natural Urdu voice) behind the same UI.
2. **Knowledge base (RAG)**: upload your notes, vendor docs, and configs so the tutor can cite them.
3. **Python sandbox**: run and check the exercises the tutor gives.
4. **Network lab integration**: connect to a GNS3 / EVE-NG / Containerlab lab with Netmiko to run `show` commands during troubleshooting drills.
5. **Pronunciation scoring** for English practice.
6. **Study calendar**: push the daily plan to Google Calendar.
