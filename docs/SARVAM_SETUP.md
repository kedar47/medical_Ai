# Sarvam AI speech-to-text — setup guide

Phone calls and browser voice turns are transcribed by **Sarvam AI** (model
`saarika:v2.5`), which is built for Hindi, English and Hinglish on 8 kHz phone
audio. Local **Faster-Whisper** stays installed as an automatic backup: if
Sarvam fails or times out for one turn, that turn is transcribed by Whisper and
the call continues.

Without the settings below the app still runs, but silently uses Whisper only,
which is much less accurate on phone audio. Always check `/voice/status`
(step 4) before testing.

---

## 1. What you need

| Item | Where it comes from |
|---|---|
| Sarvam API key (`sk_...`) | Shared privately by the project owner. Never from git. A new key can be created at <https://dashboard.sarvam.ai> → API Keys. |
| This repository, latest `main` | `git pull` |
| Python 3.12 + `.venv`, or Docker Desktop | See README sections 5 and 7 |
| Kokoro voice files in `models/kokoro/` | Not in git; copy from the project owner (README section 5) |

`httpx` (used to call Sarvam) is already in `requirements.txt`. No extra
package is needed.

## 2. Add these lines to `.env`

`.env` is in the project root (same folder as `docker-compose.yml`). Create it
from `.env.example` if it does not exist. Add:

```dotenv
VOICE_STT_PROVIDER=sarvam
SARVAM_API_KEY=<the Sarvam key>
SARVAM_STT_MODEL=saarika:v2.5
SARVAM_STT_LANGUAGE=unknown
SARVAM_TIMEOUT_SECONDS=8
```

Only the first two lines are required; the other three are the defaults.

| Variable | Meaning |
|---|---|
| `VOICE_STT_PROVIDER` | `sarvam` turns Sarvam on. `faster-whisper` (default) uses Whisper only. |
| `SARVAM_API_KEY` | The key. Without it the app falls back to Whisper even when the provider is `sarvam`. |
| `SARVAM_STT_MODEL` | Sarvam model. Keep `saarika:v2.5`. |
| `SARVAM_STT_LANGUAGE` | `unknown` = Sarvam detects Hindi or English per turn (English comes back in English letters, Hindi in Devanagari). If Sarvam guesses another language (Punjabi, Odia...), the turn is retried once as `hi-IN` automatically. |
| `SARVAM_TIMEOUT_SECONDS` | Per-request timeout before falling back to Whisper. |

Never commit `.env` or the key. `.env` is already in `.gitignore`.

## 3. Restart the app

Docker:

```bash
docker compose up -d --build api
```

Without Docker (Windows, SQLite demo, see README 7.1):

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Settings are read once at start-up, so always restart after changing `.env`.

## 4. Check it is on

Open `http://localhost:8000/voice/status` (or port 8001 without Docker):

```json
{"enabled": true, "stt": {"provider": "sarvam", "ready": true, ...}, ...}
```

If it shows `"provider": "faster-whisper"`, the `.env` lines are missing, the
key is empty, or the app was not restarted.

Quick real test: open the dashboard → **AI Voice Assistant** → Start
Conversation → record "ब्रूफेन 400 के दो पत्ते". The transcript should be exact
and BRUFEN 400MG ×2 strips is added.

## 5. Use the client's demo medicine list (optional, to match production)

Production runs on `data/client_demo_medicines.csv`: 28 medicines from the
client's product list. The import adds/updates rows but does not switch off
other medicines, so run both commands:

```powershell
.\.venv\Scripts\python.exe -m app.scripts.import_medicines data/client_demo_medicines.csv
.\.venv\Scripts\python.exe -m app.scripts.activate_only_csv data/client_demo_medicines.csv
```

The second command switches every other medicine off (nothing is deleted).
In Docker: `docker compose exec -T -w /app api python -m app.scripts.import_medicines data/client_demo_medicines.csv`, then the same with `activate_only_csv`.

## 6. Where the code is

| File | What it does |
|---|---|
| `app/services/voice/sarvam_stt.py` | `SarvamSTT`: calls `POST https://api.sarvam.ai/speech-to-text` (header `api-subscription-key`), maps the language, retries a non-Hindi/English guess as `hi-IN`, falls back to Whisper on any error. Empty transcript = "no speech". |
| `app/services/voice/__init__.py` | `build_stt()` wraps Whisper in `SarvamSTT` when the provider is `sarvam` and a key is set. |
| `app/core/config.py` | The `SARVAM_*` and `VOICE_STT_PROVIDER` settings. |
| `app/api/telephony_routes.py` | With Sarvam on, a phone utterance can last up to 25 s (12 s with Whisper), so long medicine lists are not cut off. |
| `app/services/voice_service.py` | `_normalize_heard()` fixes Sarvam's spellings before matching (split names, "201" for "200 एक", English number words in Devanagari, ...). |
| `app/core/medicine_aliases.py` | Hindi and misheard spellings of each demo medicine. Add new ones here when a call log shows a miss. |
| `app/services/voice/tts.py` | Kokoro voice. Phonemizes with espeak language flags removed so English medicine names are not read as "एन ... ही". |
| `tests/test_sarvam_stt.py` | Adapter tests with a mocked HTTP client. |

## 7. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests never call Sarvam: `tests/conftest.py` forces
`VOICE_STT_PROVIDER=faster-whisper`, and the Sarvam adapter tests use a mocked
HTTP client. No key is needed to run them.

## 8. Deploying (production server)

The production server already has the Sarvam settings in its `.env`; nothing
there changes when deploying new code. Deploys are done by the project owner:

1. Push to `main`.
2. On the server: check no call is active, then
   `sudo git pull --ff-only && sudo docker compose up -d --build api`.
3. Check `https://api.medicalai.softgoway.com/voice/status` shows `sarvam`.

## 9. Cost and limits

- Pay per audio minute (about ₹0.5–1.5 per minute). A typical order call is
  1–2 minutes of caller speech.
- One request takes up to 30 s of audio; the app's phone utterances stay under 25 s.
- Latency from the server: about 0.7–1.3 s per turn.

## 10. Setting up with Claude Code

`CLAUDE.md` in the project root tells Claude Code how to set this project up.
Open the project folder in Claude Code and say:

> Set up this project locally with Sarvam speech-to-text, following CLAUDE.md
> and docs/SARVAM_SETUP.md. Ask me for the Sarvam key.

Claude Code will create `.env`, ask for the key, install dependencies, start
the backend, and check `/voice/status`. Give it the key when it asks; it is
written only to `.env`.
