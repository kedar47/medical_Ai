# Current development status

Date: 2026-11-17
Repository: `C:\Users\SGW_05\MyProjects\medical_Ai`

## Verdict

| Area | Status | Evidence |
|---|---|---|
| Backend API and SQLite-capable local runtime | Ready for local demo | 167 passed, 2 skipped; live API order flow passed |
| React dashboard | Build/test ready; manual screen pass pending | 31 tests passed; `tsc --noEmit && vite build` passed |
| Browser voice (text + audio API) | Ready with caveats | 4.8 s Hindi audio turn ~4.3 s without TTS, ~9.2-9.8 s with a clarification reply |
| PostgreSQL/Docker paths | Unverified in this session | Docker Desktop unavailable; PostgreSQL tests skipped |
| Live telephony (Exotel) | **BLOCKED** | dial/status/transcript plumbing implemented; no account, API credentials, number, streaming entitlement, or public WSS endpoint |
| Production readiness | Not ready | no migrations, auth/roles, load test, or real call-centre validation |

## Environment audited

- Windows 10, Intel 4-vCPU CPU-only host, Python 3.12.10, Node/Vite frontend.
- Local `.venv` with Faster-Whisper `small` CPU `int8`, Kokoro ONNX `int8`, 20-record
  demo catalogue in `data/demo_medicines.csv`.
- Docker Desktop not running (`dockerDesktopLinuxEngine` pipe unavailable).
- API must use host port **8001**. Port 8000 belongs to an unrelated project and was
  not touched.
- Exotel/telephony settings are absent or disabled; outbound/status plumbing is unit-tested, but live calling was not attempted.

## What exists and was reviewed

| Component | Location | State |
|---|---|---|
| FastAPI app, lifespan, error handlers | `app/main.py` | Reviewed; optional voice preload added |
| Catalogue, matching, order sessions, confirmation | `app/api/`, `app/services/` | Reviewed; tests pass |
| Voice orchestrator and state guard | `app/services/voice_service.py` | Reviewed; correction/confirmation precedence fixed |
| Quantity correction detection | `app/services/voice/quantity_correction.py` | Reviewed; multi-item integration test added |
| STT/TTS adapters | `app/services/voice/stt.py`, `tts.py` | Shared engines, inference lock, hotwords disabled |
| Telephony transport and mocks | `app/services/telephony/`, `app/api/telephony_routes.py` | Mock tests pass; live provider blocked |
| Dashboard pages and API client | `frontend/src/` | 10 pages routed; frontend tests/build pass |
| Docs | `README.md`, `docs/` | Updated with this status, remaining work, and demo report |

## Changes made in this pass

1. **Control phrases are no longer misread as corrections.** Hindi/English
   confirmation phrases such as `"confirm kar do"` and `"order kar do"` contain
   the correction marker `"kar do"`. The orchestrator now parses intent first and
   skips the correction branch for explicit confirm/cancel/summary/end/help.
2. **STT/TTS engines are process-wide.** `build_stt`/`build_tts` reuse one adapter
   per worker process. Previously `VoiceService` was constructed per request, so a
   per-instance adapter could reload Whisper on every browser turn. A test asserts
   identity reuse.
3. **Optional startup preload.** `VOICE_PRELOAD_MODELS=true` warms Whisper and
   Kokoro in a background thread so the first demo turn is not a cold load.
4. **Whisper hotwords disabled by default.** Local measurement showed the hint made
   short turns 15-20x slower (3 s to 45-80 s) and sometimes copied the hint into the
   transcript. They are now opt-in via `VOICE_STT_HOTWORDS_ENABLED=false`.
5. **No cross-utterance conditioning.** `condition_on_previous_text=False` prevents
   unrelated previous text from steering a short medicine utterance.
6. **Session language is used when audio has no explicit language.** Short Hindi
   clips were auto-detected as English and produced slow repetition loops
   (`"Weee! Weee!"`). Browser audio turns now fall back to the session language;
   telephony passes the voice-session language too.
7. **STT vocabulary handling hardened.** Inference is serialized on the shared
   model, and unchanged vocabulary is not rewritten.
8. **TTS no longer reads catalogue padding.** Whitespace is collapsed before
   synthesis and in spoken candidate options, removing long artificial pauses.
9. **Clarification replies are shorter.** Verbose prompts were condensed and spoken
   options omit medicine IDs (the structured response still includes them), cutting
   the tested audio turn from ~14 s to ~9-10 s with TTS.
10. **Regression coverage and profiling added.** New `tests/test_voice_order_workflow.py`,
    two STT hint tests, a TTS whitespace test, and
    `app/scripts/profile_voice_pipeline.py` with `docs/voice_pipeline_metrics.json`.

## Verification evidence

### Automated

- Backend: `167 passed, 2 skipped` (14 s range)
  - Skips: SQLite cannot exercise PostgreSQL row-lock/advisory-lock tests.
- Frontend: `33 passed`; production build succeeded.
- New regression test covers the exact demo sequence:
  `BRUFEN 400` x2 + `BRUFEN 200` x1 + `"Brufen 400 ki quantity 3 strips kar do"`,
  then fresh summary and confirmation. Database rows are checked directly for
  exactly two line items and no duplicates.

### Local API end-to-end (SQLite on 8001)

- Health: `ok`, database connected; 20 catalogue records.
- Text turns without TTS: 50-80 ms each.
- Add `BRUFEN 400` x2 → `MED-00014`, 2 STRIP.
- Add `BRUFEN 200` x1 → `MED-00013`, 1 STRIP.
- Correction → `MED-00014` 3 STRIP, `MED-00013` 1 STRIP; still two lines.
- `"show summary"` then `"confirm kar do"` → order
  `PENDING_PHARMACY_REVIEW`, `VOICE_DEMO`.
- Persisted database: 1 order, 2 line items, 0 duplicate keys.
- Audio post to the confirmed session → HTTP 409; new session has a distinct ID.
- Text turn with TTS returned a WAV payload (337,296 base64 chars for the tested reply).

### Performance (local CPU, no paid API)

Source: `docs/voice_pipeline_metrics.json` (sequential run) plus live HTTP probes.

| Stage | Measured |
|---|---|
| WAV decode | 0.1 ms |
| Energy endpointing (generated PCM) | 2.5-4.3 ms |
| Telephony WAV framing | 9-13 ms (49 frames) |
| Intent parse | ~0.7 ms |
| Medicine matching | ~0.95 ms |
| DB create/add/update/summary/confirm | 2.7-11.1 ms each |
| Response serialization (510 KB JSON) | 0.8 ms |
| TTS first call | 5.3-7.9 s (includes engine load) |
| TTS warm | 2.2-6.6 s depending on reply length |
| STT first call | 5.7-7.1 s (includes model load) |
| STT warm | 4.0-4.5 s for 4.8 s audio |
| Warm browser audio turn, no TTS | 4.3 s |
| Warm browser audio turn, with TTS | 9.2-9.8 s (clarification reply) |

Caveats: one 4-vCPU Windows host, CPU-only, single worker. Longer speech and longer
assistant replies increase both STT and TTS time. The synthetic Hindi loopback
transcript was noisy (`"दो"` → `"तो"`, `"ब्रूफेन"` → `"ब्रुपहेन"`); real microphone
acceptance is a remaining manual task.

## Not verified / must not be claimed

- No live Exotel call, handset call, or public WSS connection.
- No Docker/PostgreSQL run in this session; the two PostgreSQL-only tests remain
  skipped.
- No real-human microphone test of Hindi/English/Hinglish and no manual click-through
  of all ten dashboard screens.
- No load test, failover test, GPU test, or multi-worker model serving.
- No production migration, authentication, retention, or PII review.
