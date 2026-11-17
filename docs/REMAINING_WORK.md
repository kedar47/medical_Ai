# Remaining work

Date: 2026-11-17
Status: local software demo ready; live telephony blocked.

## P0 — required before the client demo

- [ ] **Run the demo path on the actual demo machine.**
  - Start the API on **8001** and dashboard on **5173**. Do not touch the unrelated
    project on port 8000.
  - Use PostgreSQL via Docker if available; otherwise use the documented SQLite
    fallback in `README.md` §7.1.
  - Set `VOICE_PRELOAD_MODELS=true` before the first turn and wait for
    `Voice models preloaded` in the log.
  - Import the real demo catalogue: 20 records from `data/demo_medicines.csv`.
- [ ] **Manual microphone test with real speakers**, not TTS loopback:
  - Hindi, English, and Hinglish; clear and noisy room.
  - `BRUFEN 400` vs `BRUFEN 200` variant choice.
  - Quantity correction: “Brufen 400 ki quantity 3 strips kar do”.
  - Fresh summary requirement and explicit confirmation.
  - Confirm that the mic is disabled after confirmation and “Start New Order”
    creates a new session.
  - Record the browser, OS, microphone, and network used. Do not claim success from
    typed input alone.
- [ ] **Manual all-screen dashboard pass** at 1366×768 and 1920×1080:
  Dashboard, Voice Assistant, Catalogue, Matching, Sessions, Draft, Orders, Calls,
  System Status, Demo Testing. Check keyboard use, empty/error/loading states, and
  token-protected reads through the Vite proxy.
- [ ] **Rehearse fallback paths** before the demo:
  typed voice turn, clarification with multiple BRUFEN variants, TTS failure
  (`tts_error` shown, text still actionable), and API restart/new session.
- [ ] **Confirm data hygiene**: no fake medicines/orders, original 20-record catalogue
  unchanged, no confidential data on screen, dashboard proxy not exposed beyond
  loopback.

Acceptance: a timed end-to-end run with a real microphone produces exactly two
persisted BRUFEN line items (400MG × 3 STRIP, 200MG × 1 STRIP) with no duplicate
order and no manual database edits.

## P1 — telephony (externally blocked)

Implemented plumbing (unit-tested only, not live): outbound Exotel `Calls/connect`
client, authenticated `/dashboard/calls/outbound`, `/telephony/status-callback`,
public-URL derivation, `bot_call_id` call reuse, call-turn transcript persistence,
dashboard dial form/duration/transcript drawer, and readiness reporting.

- [ ] Obtain an Exotel account with Voicebot/streaming entitlement, REST API access,
  and an ExoPhone approved for the target region.
- [ ] Provide a public HTTPS/WSS endpoint (TLS, stable hostname, reachable from the
  provider). Expose only `/telephony/media` and `/telephony/status-callback`.
- [ ] Configure the private `.env`: `TELEPHONY_ENABLED=true`,
  `TELEPHONY_PROVIDER=exotel`, `TELEPHONY_PUBLIC_BASE_URL`, stream username/token,
  webhook secret, `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`,
  `EXOTEL_CALLER_ID`, and optional `TELEPHONY_ALLOWED_DESTINATIONS`.
- [ ] Confirm the provider’s actual callback payload and WSS event contract against
  `ExotelAdapter`/`status_callback`; do not synthesize call records.
- [ ] Dial from Live Calls and run a real handset acceptance: greeting,
  barge-in/interruption behaviour, silence timeout, disconnect, correction, fresh
  summary, explicit confirmation, transcript/database persistence, terminal status
  callback, duplicate/reconnect rejection, and call-log reconciliation.
- [ ] Capture provider-side logs and latency; test network loss and reconnect.
- [ ] Keep `TELEPHONY_ENABLED=false` in all public demos until this passes.

Acceptance: a real two-way phone call from the dashboard produces a provider call
reference, connected media, recognized turns in the call detail drawer, one persisted
order after explicit confirmation, one terminal callback update, and no duplicate
call/session. Mock calls never satisfy this gate.

## P1 — speech quality and latency

- [ ] Measure real-microphone Hindi/English word accuracy, especially numbers and
  strengths (`200`, `400`, `600`, `चार सौ`, `दो सौ`). Keep clarification mandatory
  wherever confidence is insufficient.
- [ ] Evaluate `VOICE_STT_MODEL=medium` or a GPU host only with measured latency and
  accuracy; do not enable Whisper hotwords without re-testing the 15-20x regression.
- [ ] Decide whether to stream TTS to the browser instead of waiting for the complete
  WAV. Current warm audio turns are ~4.3 s without TTS and ~9-10 s with a
  clarification reply.
- [ ] Consider pre-rendering fixed prompts (greeting, confirmation instructions) to
  cut repeated TTS cost. Any cache must be keyed by exact text/language/voice.
- [ ] Measure and document first-turn cold-start with and without
  `VOICE_PRELOAD_MODELS` on the actual demo laptop.
- [ ] If scaling beyond one API worker, move STT/TTS to a dedicated inference service
  or accept one loaded model set per worker.

## P2 — production hardening

- [ ] Add Alembic migrations; replace `create_all` for production upgrades.
- [ ] Add authentication/roles and restrict dashboard/monitor access; keep secrets
  out of `VITE_` variables.
- [ ] Define PII retention/deletion for customer name, phone, transcripts, audio,
  call logs, and orders.
- [ ] Add request-level rate limits and bounded concurrency for CPU-heavy audio.
- [ ] Add structured metrics/alerts: STT latency, TTS latency, turn failures, order
  confirmation rate, call cleanup failures.
- [ ] Run PostgreSQL concurrency, backup/restore, and load tests; unskip the two
  PostgreSQL-only tests in CI.
- [ ] Add CI for backend tests, frontend tests/build, and lint/format checks.
- [ ] Pin and audit all runtime dependencies; document the supported Node/Python
  versions.
- [ ] Verify the Docker image has a warm/model-cache strategy for offline demos.
- [ ] Review telephony webhook authentication and replay protection with the provider.

## Explicitly out of scope for the demo

- Clinical advice, dosage guidance, medicine substitution, or fulfilment integration.
- Claiming live calling works without a real provider call.
- Paid AI APIs or fabricated test data.
