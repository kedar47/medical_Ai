# AI-Powered Medicine Order Calling Agent — Phase 4 transport

## React operator dashboard

The complete local React dashboard is at **http://127.0.0.1:5173** with the API on
**http://localhost:8001**. It connects catalogue, matching, manual/voice ordering,
confirmation, persisted orders, call monitoring, system status and demo checklists.
Telephony remains disabled and live calling is unverified.

```powershell
Set-Location C:\Users\SGW_05\MyProjects\medical_Ai
$env:API_PORT="8001"
docker compose up -d --no-deps --build api
Set-Location frontend
npm ci
npm run dev
```

Protected dashboard reads require the private root `.env` dashboard settings.
The loopback Vite proxy supplies the token server-side; never put secrets in
`VITE_` variables or publish the dashboard proxy. This is local operator access,
not a multi-user login system.

See [setup and all-screen acceptance guide](docs/DASHBOARD.md),
[implementation/API/test report](docs/DASHBOARD_REPORT.md), and
[exact file manifest](docs/DASHBOARD_FILES.md).

## Current development status (2026-11-17)

- Backend: **160 passed, 2 skipped** locally on SQLite. The two skips require
  PostgreSQL row/advisory locking.
- Frontend: **31 tests passed**; `npm run build` (typecheck + Vite) passed.
- Local API end-to-end order flow passed: add two items, correct one quantity,
  fresh summary, confirm with `"confirm kar do"`, persist exactly two line items,
  reject more audio on the confirmed session, and start a new session.
- Voice latency after fixes on this 4-vCPU CPU-only machine: a 4.8 s Hindi audio
  turn took **~4.3 s without TTS** and **~9.2-9.8 s with TTS** (the reply was a
  clarification). First model load adds ~6-7 s for STT and ~6 s for TTS unless
  `VOICE_PRELOAD_MODELS=true`.
- Telephony streaming, outbound dial, status callback and transcript plumbing exist
  and are unit-tested, but **live calling remains BLOCKED**: no Exotel account,
  REST credentials, activated number, streaming entitlement, or public WSS endpoint.
- **Local software demo is ready; production/telephony is not.** Browser
  microphone testing with real speakers and a manual all-screen dashboard pass
  are still required.

See [current development status](docs/CURRENT_DEVELOPMENT_STATUS.md),
[remaining work](docs/REMAINING_WORK.md),
[demo test report](docs/DEMO_TEST_REPORT.md), and
[voice pipeline measurements](docs/voice_pipeline_metrics.json).


## Phase 4 status

Phase 4 adds authenticated Exotel Voicebot and mock telephone transport, outbound
Exotel dialling, status callbacks, persistent call/turn transcripts, PCM audio
conversion, interruption controls and integrated correction guards. **Live telephone
activation and all handset tests are BLOCKED:** no provider account/API credentials,
activated number, streaming entitlement, or public endpoint is available. Existing
Phase 1–3 features are preserved. Telephony defaults to disabled; local API remains
at http://localhost:8001. No paid AI API was added.

See [Phase 4 setup, architecture, environment variables, lifecycle, provider trial
evaluation, public WSS setup and live test checklist](docs/PHASE4.md) and
[implementation / verification report](docs/PHASE4_REPORT.md).

The historical Phase 3 scope and examples below describe the foundation; Phase 4
supersedes its telephony exclusions. Docker's default host port is now 8001.

## 1. Project Overview

This repository contains the Phase 3 implementation of an AI-powered medicine
order calling agent. It builds on the Phase 1 catalogue and Phase 2 order workflow
and adds a local, browser-based multilingual voice layer for Hindi, English, and
Hinglish conversations.

The voice layer is turn-based HTTP, not telephony. Audio is recorded in the browser,
transcribed locally with Faster-Whisper, handled by the deterministic order state
machine, and spoken back with Kokoro TTS. The LLM intent extractor is optional and
cannot confirm orders, select medicines, or mutate order state on its own.

The supplied catalogue contains 20 verified demo medicines derived from the
client's `Book1.xlsx`. Packaging variants, such as the three `BRUFEN` strengths,
remain separate catalogue records.

## 2. Implemented Scope

- FastAPI application with Swagger at `/docs`.
- PostgreSQL persistence with Docker health checks and a dedicated test database.
- Read-only catalogue APIs from Phase 1.
- Multilingual text normalization for medicine matching.
- Exact name, explicit alias, and fuzzy-suggestion matching.
- Mandatory clarification when more than one catalogue variant matches.
- Time-limited order sessions with customer context.
- Draft medicine lines keyed by session, medicine, and unit.
- Quantities validated as positive integers with a configurable per-line maximum.
- Order summaries with a server-issued confirmation token and draft digest.
- Explicit confirmation required before an order is created.
- Stale-token protection: any draft mutation invalidates the current summary.
- One persisted order per session, enforced by a database unique constraint.
- Idempotent repeat confirmation with the same token.
- PostgreSQL row locking for safe concurrent confirmation.
- Catalogue snapshots stored on confirmed order lines.
- Local Faster-Whisper speech recognition (`small`, CPU `int8` by default).
- Local Kokoro-ONNX speech synthesis at 24 kHz with Hindi and English voices.
- Voice-session state machine with add, select, update, remove, summary, confirm,
  cancel, end, and help intents.
- Text turns, multipart audio turns, transcription-only, status, end, and browser
  demo endpoints.
- Optional Ollama intent extraction with deterministic fallback.
- Mocked voice tests plus real local TTS/STT smoke tooling.
- Swagger/OpenAPI documentation and this README.

Not implemented:

- Verified live PSTN calls; Exotel REST/WSS plumbing is present but provider activation is externally blocked.
- Medicine substitution, dosage recommendations, diagnosis, or medical advice.
- Customer accounts, authentication, authorization, payments, delivery, or dashboards.
- Pharmacy fulfilment actions beyond persisting an order for review.
- Permanent raw-audio or unrestricted transcript storage.

## 3. Architecture

The Phase 2 order flow remains deterministic:

```text
customer text
    -> medicine matching
    -> explicit medicine_id selection when clarification is required
    -> order-session draft
    -> item add / update / delete
    -> summary + confirmation token + draft digest
    -> explicit confirmation with matching token
    -> persisted order with catalogue snapshot
```

The Phase 3 voice flow wraps that workflow:

```text
browser microphone
    -> audio upload
    -> local Faster-Whisper transcription
    -> deterministic control-intent parser
    -> optional Ollama JSON extraction for free-form add requests
    -> medicine matching / clarification / order workflow
    -> local Kokoro speech synthesis
    -> browser audio playback + text transcript
```

Layers:

- `app/api`: FastAPI routers, including the voice and demo routes.
- `app/schemas`: Pydantic request/response contracts.
- `app/services`: matching, catalogue, import, order, and voice orchestration.
- `app/services/voice`: STT, TTS, intent parsing, state types, and adapters.
- `app/repositories`: SQLAlchemy queries.
- `app/database`: engine, session factory, and ORM models.
- `app/static`: browser voice demo.
- `app/scripts`: catalogue import and real voice smoke test.
- `tests`: in-memory SQLite by default, PostgreSQL when `TEST_DATABASE_URL` is set.

## 4. Safety and Correctness Rules

1. Matching never auto-selects a packaging variant. If multiple active catalogue
   rows share a normalized name, the response is `CLARIFICATION_REQUIRED` and the
   caller must choose a specific `medicine_id`.
2. Fuzzy results are suggestions only. They are returned with
   `match_type: FUZZY_SUGGESTION` and are never treated as an exact match.
3. The API does not substitute medicines, infer dosage, or provide medical advice.
4. An order cannot be created without `explicit_confirmation: true`.
5. The confirmation token is tied to a server-stored SHA-256 token hash and a
   digest of the complete draft. Adding, updating, or deleting an item clears the
   token and digest.
6. A confirmation token is valid only while the session is `ACTIVE`, unexpired,
   and unchanged since the summary was generated.
7. The database enforces one order per session and one line per
   session/medicine/unit combination.
8. Confirmed orders snapshot medicine name and description so later catalogue
   edits do not rewrite historical orders.
9. Confirmation uses `SELECT ... FOR UPDATE` on PostgreSQL, and a concurrent
   request with the same token returns the already-created order idempotently.
10. Voice confirmation uses the same server token and draft digest as the REST API.
    The token is never included in assistant speech or conversation state. A changed
    draft requires a fresh summary and a new explicit confirmation.
11. Voice medicine selection is never guessed. Multiple variants require an explicit
    ordinal, strength/package choice, exact name, or `medicine_id`; fuzzy candidates
    are read-only suggestions until the customer identifies the intended medicine.
12. An optional LLM may only propose structured intents. Deterministic validation,
    Phase 2 matching rules, and the order service remain authoritative.

## 5. Prerequisites

- Docker Desktop with Docker Compose.
- Python 3.12 for local development and testing.
- Approximately 512 MB free disk for the Kokoro model files and 1-2 GB for the
  cached Faster-Whisper `small` model.
- Kokoro files at `models/kokoro/kokoro-v1.0.int8.onnx` and
  `models/kokoro/voices-v1.0.bin`. These files are mounted read-only in Docker and
  are intentionally ignored by Git.
- The client workbook `Book1.xlsx` only if the demo CSV must be regenerated.

## 6. Environment Variables

Create `.env` from the example and replace the example database password:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` | PostgreSQL user |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_DB` | Main database name |
| `DATABASE_URL` | SQLAlchemy URL used by the API |
| `TEST_DATABASE_URL` | Dedicated test database URL |
| `LOG_LEVEL` | Logging level, for example `INFO` |
| `SQL_ECHO` | Set `true` only while debugging SQL |
| `ORDER_SESSION_TTL_MINUTES` | Draft session lifetime; default `30` |
| `MAX_ORDER_ITEM_QUANTITY` | Maximum quantity per line; default `100` |
| `FUZZY_MATCH_THRESHOLD` | Fuzzy suggestion cutoff `0.0`-`1.0`; default `0.72` |
| `FUZZY_MATCH_LIMIT` | Maximum fuzzy candidates; default `5` |
| `VOICE_ENABLED` | Enable voice session APIs; default `true` |
| `VOICE_SESSION_TTL_MINUTES` | Voice-session lifetime; default `30` |
| `VOICE_MAX_AUDIO_BYTES` | Maximum audio upload size; default `10 MB` |
| `VOICE_MAX_TRANSCRIPT_CHARS` | Maximum accepted transcript length; default `1000` |
| `VOICE_MAX_TURNS` | Maximum turns per voice session; default `100` |
| `VOICE_MAX_TTS_CHARS` | Maximum characters synthesized per reply; default `600` |
| `VOICE_STT_MODEL` | Faster-Whisper model name; default `small` |
| `VOICE_STT_DEVICE` | STT device; default `cpu` |
| `VOICE_STT_COMPUTE_TYPE` | STT compute type; default `int8` |
| `VOICE_STT_BEAM_SIZE` | STT beam size; default `1` |
| `VOICE_PRELOAD_MODELS` | Load STT/TTS models in a startup background thread; default `false`. Set `true` on the demo laptop. |
| `VOICE_STT_HOTWORDS_ENABLED` | Opt-in Whisper hotwords; default `false`. Locally measured 15-20x slower and can leak hints into transcripts. |
| `VOICE_TTS_ENABLED` | Enable speech synthesis; default `true` |
| `VOICE_TTS_VOICE_HI` | Hindi Kokoro voice; default `hf_alpha` |
| `VOICE_TTS_VOICE_EN` | English Kokoro voice; default `af_heart` |
| `VOICE_TTS_SPEED` | Kokoro speaking speed; default `1.0` |
| `KOKORO_MODEL_PATH` | Kokoro ONNX model path |
| `KOKORO_VOICES_PATH` | Kokoro voice-pack path |
| `VOICE_LLM_ENABLED` | Enable optional Ollama intent extraction; default `false` |
| `OLLAMA_BASE_URL` | Ollama base URL, normally `http://localhost:11434` |
| `OLLAMA_DOCKER_BASE_URL` | Ollama URL used by the API container; default `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | Ollama model; default `qwen2.5:3b` |
| `OLLAMA_TIMEOUT_SECONDS` | Ollama request timeout; default `8` |

Never commit `.env`. Docker Compose overrides the database host to `db` inside the
container while `.env` URLs remain useful for local commands.

Optional additional aliases can be provided as a JSON object in
`MEDICINE_ALIASES`. Targets must be canonical catalogue names:

```dotenv
MEDICINE_ALIASES={"dolo":"PARACETAMOL","क्रोसिन":"CROCIN"}
```

Built-in demo aliases are defined in `app/core/medicine_aliases.py`.

## 7. Docker Start

```bash
docker compose up --build -d
```

This starts:

- `db`: PostgreSQL 16 with a named persistent volume and health check.
- `api`: FastAPI served by Uvicorn with the read-only `./models` mount and a
  persistent `model_cache` volume for the Faster-Whisper download.

The API container sets `KOKORO_MODEL_PATH=/app/models/kokoro/kokoro-v1.0.int8.onnx`
and `KOKORO_VOICES_PATH=/app/models/kokoro/voices-v1.0.bin`. The first voice request
may download the Faster-Whisper `small` model; later requests reuse the cache.

Open `http://localhost:8000/docs` by default. If host port 8000 is already in use:

```powershell
$env:API_PORT="8001"; docker compose up --build -d
```

Then use `http://localhost:8001/docs`. The container always listens on port 8000
internally; only the host mapping changes. PostgreSQL is not published to the host.

Check status and logs:

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f db
```

### 7.1 Local demo without Docker (fallback)

Docker/PostgreSQL is the supported runtime. If Docker is unavailable for a local
demo, SQLite can run the same API on port 8001. This is demo-only and does not
exercise PostgreSQL locking.

```powershell
$env:DATABASE_URL="sqlite+pysqlite:///C:/Users/SGW_05/AppData/Local/Temp/opencode/medical_ai_demo.db"
$env:VOICE_PRELOAD_MODELS="true"
.\.venv\Scripts\python.exe -m app.scripts.import_medicines data\demo_medicines.csv
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Then start the dashboard from `frontend` with `npm run dev` (http://127.0.0.1:5173).
Keep port 8000 untouched; do not point the demo at the other project.

## 8. Database Schema

Phase 1:

- `medicines`: catalogue rows, active flag, source traceability, UTC timestamps.

Phase 2:

- `order_sessions`: public session ID, customer context, status, expiry,
  confirmation token hash, draft digest, and confirmation timestamp.
- `order_items`: draft lines with medicine FK, quantity, and unit.
- `orders`: confirmed order header with public order ID, one-per-session unique FK,
  customer snapshot, status, confirmation source, and timestamps.
- `order_line_items`: confirmed medicine snapshot lines.

Phase 3:

- `voice_sessions`: public voice-session ID, one-to-one order-session FK, status,
  detected language, JSON conversation state, last intent, turn count, expiry, and
  end timestamp.

Statuses:

- Session: `ACTIVE`, `CONFIRMED`, `CANCELLED`, `EXPIRED`.
- Voice session: `ACTIVE`, `CONFIRMED`, `CANCELLED`, `EXPIRED`, `ENDED`.
- Order: `PENDING_PHARMACY_REVIEW`.
- Unit: `TABLET`, `STRIP`, `BOTTLE`, `BOX`, `UNIT`.

Constraints include positive quantities, allowed statuses/units, unique session
medicine/unit draft lines, unique order line medicine/unit, and unique order per
session. Schema creation is non-destructive through SQLAlchemy `create_all`; no
Alembic migration tool is configured yet.

## 9. Demo Catalogue and Import

The demo CSV is at `data/demo_medicines.csv` and contains 20 verified records:

- Source workbook: `Book1.xlsx`, worksheet `Sheet1`.
- Columns: `sno`, `item`, `descriptio`.
- Selected rows: `3-7`, `9-17`, and `19-24`.
- Stable IDs: `MED-<source_row>`, for example `MED-00013`.

Regenerate the CSV from the workbook:

```bash
python -m app.scripts.prepare_demo_csv --source /path/to/Book1.xlsx
```

Import or re-import:

```bash
docker compose exec api python -m app.scripts.import_medicines data/demo_medicines.csv
```

The import is validated and all-or-nothing. Re-running it does not create
duplicates; changed rows with the same `medicine_id` are updated, unchanged rows are
skipped, and existing medicines are not silently deleted.

## 10. API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Database-aware health check |
| GET | `/medicines` | Paginated catalogue list |
| GET | `/medicines/count` | Active catalogue count |
| GET | `/medicines/{medicine_id}` | Single catalogue record |
| GET | `/medicines/lookup/exact?name=BRUFEN` | Exact normalized name lookup |
| POST | `/medicine-matching/search` | Deterministic matching and clarification |
| POST | `/order-sessions` | Create an empty draft session |
| GET | `/order-sessions/{session_id}` | Session with draft items |
| POST | `/order-sessions/{session_id}/cancel` | Cancel an active session |
| POST | `/order-sessions/{session_id}/items` | Add or accumulate a draft item |
| PATCH | `/order-sessions/{session_id}/items/{item_id}` | Change quantity/unit |
| DELETE | `/order-sessions/{session_id}/items/{item_id}` | Remove a draft item |
| GET | `/order-sessions/{session_id}/summary` | Read summary and issue token |
| POST | `/order-sessions/{session_id}/confirm` | Explicitly create the order |
| GET | `/orders/{order_id}` | Read a persisted order |
| GET | `/voice-demo` | Browser microphone and text voice demo |
| GET | `/voice/status` | Voice component configuration status |
| POST | `/voice/sessions` | Create a voice session |
| GET | `/voice/sessions/{voice_session_id}` | Read voice session and transcript |
| POST | `/voice/sessions/{voice_session_id}/turns` | Submit a text turn |
| POST | `/voice/sessions/{voice_session_id}/audio-turns` | Submit audio and receive text + speech |
| POST | `/voice/transcribe` | Transcribe audio without creating a session |
| POST | `/voice/sessions/{voice_session_id}/end` | End the voice session |

### 10.1 Medicine Matching

```http
POST /medicine-matching/search
Content-Type: application/json

{"text": "BRUFEN"}
```

Response:

```json
{
  "status": "CLARIFICATION_REQUIRED",
  "requested_text": "BRUFEN",
  "candidates": [
    {
      "medicine_id": "MED-00013",
      "medicine_name": "BRUFEN",
      "description": "200MG                             15'S",
      "match_type": "EXACT_NAME"
    },
    {
      "medicine_id": "MED-00014",
      "medicine_name": "BRUFEN",
      "description": "400MG                             15'S",
      "match_type": "EXACT_NAME"
    },
    {
      "medicine_id": "MED-00015",
      "medicine_name": "BRUFEN",
      "description": "600MG                             15'S",
      "match_type": "EXACT_NAME"
    }
  ],
  "message": "Multiple catalogue variants match. The customer must select a specific medicine_id before the item can be added."
}
```

Matching statuses:

- `EXACT_MATCH`: exactly one active catalogue row matched by normalized name or
  explicit alias.
- `CLARIFICATION_REQUIRED`: multiple variants matched; choose one `medicine_id`.
- `NOT_FOUND`: no exact match. `candidates` may contain unverified fuzzy
  suggestions marked `FUZZY_SUGGESTION`.

Text is Unicode-normalized, case-folded, and punctuation-tolerant. Devanagari
marks are preserved. Descriptions are not treated as medicine names, so
`BRUFEN 400MG` does not silently choose `MED-00014`.

### 10.2 Create a Session

```http
POST /order-sessions
Content-Type: application/json

{"customer_phone": "+919876543210", "customer_name": "Asha"}
```

Both fields are optional. Phone values must be E.164-like, for example
`+919876543210`. A session starts `ACTIVE`, contains no items, and expires after the
configured TTL.

### 10.3 Add a Draft Item

```http
POST /order-sessions/{session_id}/items
Content-Type: application/json

{"medicine_id": "MED-00013", "quantity": 2, "unit": "STRIP"}
```

The medicine must exist in the active catalogue. Adding the same medicine and unit
again accumulates the quantity. A different unit creates a separate line. Adding,
updating, or deleting an item invalidates any previously issued summary token.

Update a line:

```http
PATCH /order-sessions/{session_id}/items/{item_id}
Content-Type: application/json

{"quantity": 3, "unit": "BOX"}
```

At least one of `quantity` or `unit` is required. Changing a unit to one already
used by the same medicine in the session returns `409`.

Delete a line:

```http
DELETE /order-sessions/{session_id}/items/{item_id}
```

### 10.4 Generate the Summary

```http
GET /order-sessions/{session_id}/summary
```

When the session is active and non-empty, the response includes:

- `can_confirm: true`
- `confirmation_token`: a random server-issued token
- `summary_digest`: SHA-256 digest of the draft
- `confirmation_issued_at`
- the complete item list and customer context

The plaintext token is returned only by this response. The database stores only its
SHA-256 hash. An empty, cancelled, expired, or confirmed session receives
`can_confirm: false` and no token. Calling summary again for an active non-empty
session issues a fresh token and invalidates the previous one.

### 10.5 Confirm the Order

```http
POST /order-sessions/{session_id}/confirm
Content-Type: application/json

{
  "confirmation_token": "<token from summary>",
  "confirmation_source": "DEMO_API",
  "explicit_confirmation": true
}
```

The server verifies the token hash, the current draft digest, session status, and
expiry. It also revalidates that every medicine is still active. On success it:

- creates one order with status `PENDING_PHARMACY_REVIEW`;
- copies medicine name and description into immutable order lines;
- marks the session `CONFIRMED`;
- returns HTTP `201`.

Repeating the same confirmation with the same token returns HTTP `200`, the same
order, and `idempotent_retry: true`. A malformed, stale, or different token returns
`409`.

Read the order:

```http
GET /orders/{order_id}
```

### 10.6 Cancel a Session

```http
POST /order-sessions/{session_id}/cancel
```

An active session becomes `CANCELLED`, confirmation credentials are cleared, and
further item mutations or confirmation attempts return `409`. Repeated cancellation
is idempotent.

### 10.7 Browser Voice Demo

Open:

```text
http://localhost:8000/voice-demo
```

The demo records a microphone clip or accepts typed text, sends it to the voice API,
displays the assistant reply and order summary, and plays the returned WAV. Browser
microphone access requires `localhost` or HTTPS.

Create a voice session:

```http
POST /voice/sessions
Content-Type: application/json

{"language": "hi", "customer_name": "Asha"}
```

Example text turn:

```http
POST /voice/sessions/{voice_session_id}/turns
Content-Type: application/json

{"text": "do strip brufen 200 add karo", "language": "hi", "speak": true}
```

Example audio turn:

```bash
curl -X POST "http://localhost:8000/voice/sessions/{voice_session_id}/audio-turns" \
  -F "file=@recording.webm" \
  -F "language=hi" \
  -F "speak=true"
```

The first audio request may take longer while Faster-Whisper initializes. The
response contains the transcript, intent, assistant text, optional order summary or
confirmed order, candidate variants, and base64 WAV audio. `tts_error` is populated
instead of failing the turn when speech synthesis is unavailable.

Voice support commands include yes/no confirmations, summary requests, quantity
changes, item removal, cancellation, ending the call, and help. If a medicine name
matches multiple catalogue variants, the assistant lists numbered candidates and
requires an explicit choice such as `pehla`, `200 mg`, `BRUFEN 400`, or a
`medicine_id`. Fuzzy suggestions are never added automatically.

End a voice session:

```http
POST /voice/sessions/{voice_session_id}/end
```

Read the voice-session status and turn count:

```http
GET /voice/sessions/{voice_session_id}
```

Conversation state is stored as structured JSON without raw audio. Each turn response
contains the latest user transcript and assistant text. Raw uploaded audio is
processed in a temporary file and deleted after transcription.

Check component readiness:

```http
GET /voice/status
```

This reports whether the voice layer is enabled and whether STT, TTS, and the
optional LLM are configured. Readiness does not download models.

## 11. Common Error Responses

| Status | Meaning |
|---|---|
| `400` | Empty draft, invalid explicit-confirmation flag, or quantity limit |
| `404` | Unknown session, order, draft item, or catalogue medicine |
| `409` | Expired/cancelled/confirmed session, stale token, changed draft, duplicate line, or concurrent confirmation conflict |
| `422` | Pydantic validation error, including invalid phone, quantity, or unit |
| `503` | Database unavailable; generic message only |

No error response exposes database credentials or the stored token hash.

## 12. Testing

Inside Docker against the dedicated PostgreSQL test database:

```bash
docker compose exec api python -m pytest tests/ -v
```

Locally:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

Without `TEST_DATABASE_URL`, tests use isolated in-memory SQLite and skip the
PostgreSQL-only concurrency test. Voice tests use fake STT and TTS adapters, so they
do not download models. With the Docker test database configured, the concurrency
test also runs. Tests never use the main development database.

Frontend:

```powershell
Set-Location frontend
npm test -- --reporter=dot
npm run build
```

Current verification on the development machine (2026-11-17):

- Local SQLite backend run: `160 passed, 2 skipped` in 13.1 s.
- Frontend: `31 passed`; typecheck and production build passed.
- Real Kokoro and Faster-Whisper smoke (run sequentially, cold models): English
  TTS 3.85 s / STT 5.66 s and recognized "BRUFEN"; Hindi TTS 5.29 s / STT 7.02 s
  but transcribed "दो" as "तो" and "ब्रूफेन" as "ब्रुपहेन". Real-microphone
  acceptance is still required, and demo scripts should use clear speech.
- Local API end-to-end: two-item draft, quantity correction, fresh summary,
  explicit confirmation, persisted order with two line items and no duplicates,
  confirmed-session audio rejection, and a fresh session.
- Live telephony: **not tested and not claimed** (external provider blocked).

Run the real local voice smoke test after placing the Kokoro files:

```powershell
.\.venv\Scripts\python.exe -m app.scripts.voice_smoke --language hi
.\.venv\Scripts\python.exe -m app.scripts.voice_smoke --language en
```

The script synthesizes a WAV, reports synthesis real-time factor, transcribes it with
Faster-Whisper, and prints the recognized text. Use `--text`, `--text-file`, or
`--skip-stt` for custom checks. The first run may download the STT model. Do not run
the two commands concurrently when collecting timings; they compete for CPU.

Profile each local pipeline stage and write `docs/voice_pipeline_metrics.json`:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m app.scripts.profile_voice_pipeline
```

Use `--skip-stt` for a fast TTS/DB-only profile. The profiler uses the real 20-record
demo catalogue and local models; it does not call paid APIs.

## 13. Swagger

Open:

```text
http://localhost:8000/docs
```

When using host port 8001:

```text
http://localhost:8001/docs
```

The OpenAPI JSON is available at `/openapi.json`.

## 14. Troubleshooting

### Port 8000 is already in use

Set `API_PORT=8001` when starting Compose. Do not stop unrelated projects that use
port 8000. The container still listens on port 8000 internally.

### API cannot connect to PostgreSQL

```bash
docker compose ps
docker compose logs -f db
docker compose logs -f api
```

Wait for the database health check. `/health` returns `503` until connectivity is
restored.

### Test database does not exist

Recreate the local volume if it predates `docker/initdb/01-create-test-db.sh`:

```bash
docker compose down -v
docker compose up --build -d
```

This deletes the local database volume. Use it only for development.

### Database credentials changed but the volume is old

PostgreSQL initialization scripts only run on an empty data directory. Recreate the
development volume as above, or update the role manually.

### Matching returns no candidates for a known medicine

Check that the medicine name is present and `is_active` is true. Aliases must target
a canonical catalogue name. Fuzzy matching deliberately does not return confirmed
matches; suggestions still require explicit `medicine_id` selection.

### Confirmation returns 409

Generate a new summary. A `409` means the token expired or the draft changed, or
the session is no longer active. Never bypass confirmation by writing an order
directly.

### Voice models are missing or TTS is unavailable

Check that both Kokoro files exist and that the paths in `.env` or Compose are
correct. Call `/voice/status` for a component summary. In Docker, verify the
read-only mount:

```bash
docker compose exec api ls -lh /app/models/kokoro
```

The first audio turn downloads the Faster-Whisper model into the `model_cache`
volume. Allow extra time and check API logs if transcription stalls.

### Audio upload is rejected

The browser may produce a format that the local decoder cannot read, or the file may
exceed `VOICE_MAX_AUDIO_BYTES`. Record a shorter clip and check the allowed content
type. Raw audio is temporary and is not stored in the database.

### Ollama is enabled but extraction fails

This is non-fatal: the deterministic parser remains authoritative. Verify
`OLLAMA_BASE_URL`, that the configured model is installed, and that Docker can reach
`host.docker.internal:11434`. Keep `VOICE_LLM_ENABLED=false` unless the optional
model is needed.

## 15. Limitations and Future Work

- Telephony transport, outbound dial, status callback and transcript code exist and
  are unit-tested, but **live calling is BLOCKED** without Exotel
  account/API credentials, an activated number/streaming entitlement, public WSS
  endpoint, and real handset acceptance. No live call is claimed.
- The browser voice demo is turn-based HTTP, not real-time full-duplex streaming.
  Model instances are process-wide; run one API worker per model set unless a
  separate inference service or GPU capacity is provided.
- Local CPU latency is speech-length dependent. First use loads models unless
  `VOICE_PRELOAD_MODELS=true`; Hindi ASR on synthetic audio was noisy and needs
  real-microphone validation. Re-enabling Whisper hotwords is not recommended
  without re-measuring latency and transcript quality.
- No medicine substitution, dosage guidance, or clinical advice.
- No authentication or role-based access; deploy behind a trusted gateway in later
  phases.
- `create_all` handles non-destructive schema creation, but production migrations
  should use Alembic.
- Confirmed orders are queued for pharmacy review only; no fulfilment integration.
- `call_reference_id` is reserved for a later telephony phase.
- Fuzzy matching is deterministic string similarity, not a clinical or phonetic
  model; ambiguous results always require clarification.
- Speech recognition can misrecognize medicine names. The deterministic matching
  and explicit clarification flow remain mandatory.
- Optional Ollama intent extraction is disabled by default and was not smoke-tested
  on a machine without Ollama installed.
