# Demo test report

Date: 2026-11-17
Scope: local software demo on `http://127.0.0.1:8001` plus dashboard build.
Telephony: outbound/status/transcript plumbing implemented and unit-tested; not tested live.

## Verdict

| Gate | Result |
|---|---|
| Backend automated tests | **PASS** — 167 passed, 2 skipped |
| Frontend automated tests | **PASS** — 33 passed |
| Frontend typecheck/build | **PASS** |
| API order workflow (SQLite) | **PASS** |
| Quantity correction persistence | **PASS** — 2 lines, 0 duplicates |
| Confirmed-session audio rejection | **PASS** — HTTP 409 |
| New session after confirmation | **PASS** |
| Real local STT/TTS smoke | **PASS with accuracy caveat** |
| Real microphone + real speakers | **NOT RUN** |
| Manual ten-screen dashboard pass | **NOT RUN** |
| Docker/PostgreSQL runtime | **NOT RUN** (Docker unavailable) |
| Live Exotel call | **BLOCKED** (no provider/account/API credentials/WSS) |

## Environment

- Windows 10 (10.0.19045), Intel 4-vCPU, CPU only.
- Python 3.12.10; Faster-Whisper `small` `int8`; Kokoro ONNX `int8`.
- Backend on host port **8001**; port 8000 intentionally untouched.
- Demo database: SQLite file used only for this local run. Production target remains
  PostgreSQL.
- Catalogue: 20 real demo records imported from `data/demo_medicines.csv`.

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Result: `167 passed, 2 skipped` (14 s range).

Skipped, because SQLite cannot exercise PostgreSQL locking:

1. `tests/test_order_concurrency.py:20` — row-lock concurrency.
2. `tests/test_telephony.py:366` — PostgreSQL advisory locking.

Frontend:

```powershell
Set-Location frontend
npm test -- --reporter=dot
npm run build
```

Result: 33 tests passed; `tsc --noEmit` and Vite production build passed.

New regression coverage:

- `tests/test_voice_order_workflow.py` drives the exact two-item draft, quantity
  correction, summary, confirmation, and direct database verification.
- Confirmation phrases `"order kar do"` and `"ऑर्डर कर दो"` are asserted to confirm,
  not to enter the correction path.
- STT hotwords are asserted to be opt-in; language fallback is asserted for browser
  audio turns.
- TTS whitespace collapsing is covered, so padded catalogue descriptions do not
  create long silent gaps.
- Exotel dial request construction is asserted without a network call: regional REST
  URL, Basic REST auth, 8 kHz WSS `StreamUrl`, `bot_call_id`, and callback URL.
- Outbound call creation reuses the same call when Exotel opens the stream, applies
  terminal provider statuses, persists caller/agent turns, and keeps the dial button
  disabled until configuration is complete.

## API end-to-end evidence

Commands were run against a real Uvicorn process on 8001 with the 20-record
catalogue. Text turns used **no TTS** except where noted.

| Step | Observed |
|---|---|
| `GET /health` | `{"status":"ok","database":"connected"}` |
| `GET /medicines?limit=5` | total 20, real catalogue |
| Create voice session (en) | HTTP 201, new voice + order session |
| `add two strips of brufen 400` | 63 ms; `MED-00014`, 2 STRIP |
| `add one strip of brufen 200` | 55 ms; two lines: `MED-00014` 2, `MED-00013` 1 |
| `Brufen 400 ki quantity 3 strips kar do` | 50 ms; `update_item`; `MED-00014` 3, `MED-00013` 1 |
| `show summary` | 50 ms; fresh summary; order still not created |
| `confirm kar do` | 80 ms; order created, `PENDING_PHARMACY_REVIEW` |
| Audio upload after confirmation | HTTP 409 |
| New voice session | distinct ID |
| Text turn with `speak=true` | WAV returned (337,296 base64 chars for that reply) |

Created order in the evidence run:
`order_bd28284095714e2caffd6daa26fdce57`.

Direct SQLite verification:

```text
MED-00013  BRUFEN  200MG 15'S  1  STRIP
MED-00014  BRUFEN  400MG 15'S  3  STRIP
orders=1  line_items=2  duplicate_keys=0
confirmation_source=VOICE_DEMO
```

## Real local voice smoke

Commands (run one at a time for meaningful timings):

```powershell
.\.venv\Scripts\python.exe -m app.scripts.voice_smoke --language hi
.\.venv\Scripts\python.exe -m app.scripts.voice_smoke --language en
```

Observed, run sequentially with cold models:

- Hindi: TTS 5.29 s for 4.22 s audio; STT 7.02 s; transcript
  `"नमस्ते मुजे तो स्ट्रिप ब्रुपहेन और एक स्ट्रिप क्लोमिन चाहिए"` — `"दो"` was
  recognized as `"तो"` and `"ब्रूफेन"` as `"ब्रुपहेन"`.
- English: TTS 3.85 s for 2.11 s audio; STT 5.66 s; transcript
  `"Hello, I need two strips of brufin."` (alias matching handles `brufin`).

Functional conclusion: local models load and transcribe, but synthetic Hindi
accuracy, especially quantity words, is not sufficient evidence for a live-microphone
demo. Real speaker testing is required; use clear speech or typed fallback.

## Performance measurements

Sequential profile: `docs/voice_pipeline_metrics.json`. Live HTTP audio probes used a
correctly generated 4.84 s Hindi TTS clip and the session language. The transcript
had an unclear strength, so the reply was a one-option clarification.

| Item | Result |
|---|---|
| Warm audio turn, `speak=false` | 4.25-4.29 s |
| Warm audio turn, `speak=true` | 9.16-9.77 s |
| Warm STT | 4.0-4.5 s |
| Warm TTS | 2.2-6.6 s depending on reply length |
| Cold STT (first model use) | 5.7-7.1 s |
| Cold TTS (first model use) | 5.3-7.9 s |
| Intent parse | ~0.7 ms |
| Medicine matching | ~1.0 ms |
| DB write/read operations | 2.7-10.2 ms each |
| Response serialization (510 KB) | 0.7-0.8 ms |

Host: 4 vCPU, CPU-only, single API worker. Times scale with speech/reply length and
CPU contention. The earlier 30-80 s turns were traced to Whisper hotwords and
language auto-detection, not to database or matching work.

## Defects found and fixed in this pass

1. `"confirm kar do"` / `"order kar do"` entered quantity correction because of the
   `"kar do"` marker. Fixed with control-intent precedence; regression tests added.
2. STT and TTS adapters could be recreated per HTTP request, reloading models every
   turn. Fixed with process-wide shared adapters; optional startup preload added.
3. Whisper hotwords caused 15-20x slower decoding and leaked into transcripts.
   Disabled by default; opt-in setting retained.
4. Auto language detection on short Hindi audio chose English and produced slow
   repetition loops. Browser audio now falls back to the session language, and
   telephony passes its session language.
5. Cross-utterance conditioning could steer short turns; disabled.
6. STT inference on the shared engine is now serialized.
7. Padded catalogue descriptions produced long TTS pauses. Whitespace is now
   collapsed before synthesis and in spoken candidate options.
8. The fuzzy-clarification prompt was too long, and spoken medicine IDs were read
   digit by digit. Prompts were shortened, and the spoken option list omits IDs;
   the structured API response still includes every medicine ID.

## Limitations / residual risk

- No real-human microphone test; synthetic TTS audio is not a substitute.
- No Docker/PostgreSQL run here; concurrency and advisory-lock tests remain skipped.
- Dashboard verified by tests/build, not by a manual click-through of all screens.
- No live telephony, handset, provider webhook, or public WSS validation.
- One API worker only; multiple workers each load their own models.
- CPU-only latency is acceptable for a turn-based demo but not for a natural
  full-duplex phone conversation.

## Go/no-go

- **Local software demo: GO**, with `VOICE_PRELOAD_MODELS=true`, real catalogue,
  typed fallback ready, and the P0 manual checks in `REMAINING_WORK.md`. Prefer clear
  English speech for the voice portion until real-microphone Hindi accuracy is
  measured; keep typed fallback available.
- **Live phone demo: NO-GO** until provider access, public WSS, and real handset
  acceptance are complete.
