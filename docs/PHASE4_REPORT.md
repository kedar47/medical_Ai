# Phase 4 implementation and verification report

16 November 2026. **Independent software implemented; live acceptance BLOCKED.**

## A. Implementation summary

Exotel Voicebot and explicit mock adapters share an authenticated WSS gateway,
bounded PCM endpointing, local STT/TTS and the existing order orchestrator.
Persistent call/voice/order mapping handles retries. Call logs retain UTC lifecycle
metadata without raw audio. Quantity corrections and persisted confirmation guards
are now integrated in VoiceService. Telephone summaries are synthesized completely.
The browser disables completed sessions and supports fresh orders.

## B. Files created / modified

Created: `app/services/telephony/` (base, Exotel, mock, audio bridge, call manager,
schemas and full-speech synthesis), `app/api/telephony_routes.py`,
`app/scripts/telephony_smoke.py`, `app/scripts/reconcile_calls.py`,
`tests/test_telephony.py`, `docker/cloudflared.example.yml`, `docs/PHASE4.md`
and this report.

Modified: `app/core/config.py`, `app/database/models.py`, `app/main.py`,
`app/services/voice/types.py`, `app/services/voice_service.py`,
`app/static/voice_demo.html`, `tests/conftest.py`, `.env.example`,
`requirements.txt`, `Dockerfile`, `docker-compose.yml`, `README.md`.

No real `.env` secrets changed. No git staging, commits or pushes. Only this
project's API container was rebuilt/recreated; the existing database volume and
unrelated containers were preserved. Tests use the dedicated test database.

## C. Telephony provider selected

Exotel Voicebot with documented Basic-authenticated WSS and 8 kHz PCM16. Its
adapter isolates provider JSON from medicine services. Mock transport needs no
provider account. No alternative-provider credentials are present. Actual account
capabilities/protocol compatibility remain unverified.

## D. Provider setup and manual dependencies

The owner reports they cannot add a provider account. KYC, regional account setup,
Voicebot entitlement, approved trial rates/geographies, number activation and
restricted public WSS remain blocked. [PHASE4.md](PHASE4.md) provides manual steps,
environment variables, tunnel configuration, provider evaluation and official sources.
No account was registered, no paid service added and no trial credits spent.

## E. Phone number status

**BLOCKED: no activated demo number or public endpoint available/verified.**
The running API defaults to disabled/unconfigured with `live_verified: false`.
No phone number was invented or configured as activated.

## F. New APIs and WebSocket endpoints

| Route | Access |
|---|---|
| `GET /telephony/status` | Public redacted configuration flags |
| `WS /telephony/media` | Enabled provider, Basic auth, expected Exotel account |
| `POST /telephony/mock/incoming` | Private monitor Bearer token; mock only |
| `GET /telephony/calls` | Private latest 20 redacted logs |
| `GET /telephony/calls/{call_id}` | Private metadata/order reference; phone excluded |

Authenticated WSS `start` is the incoming event. No HTTP resolver or invented
webhook-signature validator is needed. The tunnel template exposes only media;
local API stays on **8001**.

## G. Call lifecycle implementation

RINGING, CONNECTED, ACTIVE, PROCESSING, CONFIRMING, COMPLETED, DISCONNECTED, FAILED.
One call per API process, one pending turn, 12-second utterance cap, 800 ms
endpointing, total/silence timeouts and bounded frame sizes. Duplicate incoming
events reuse the mapping but cannot reopen an active/terminal stream. Restricted
or invalid caller IDs remain unavailable; country codes are never guessed.

Disconnect cancels an unconfirmed draft. Committed orders survive transport/TTS
failure and repeated cleanup. PostgreSQL advisory/row locks and unique constraints
protect creation. Interrupted playback clears queued audio and invalidates the
summary. Live acoustic barge-in remains unverified. Stale logs can be reconciled
after process/database failure using the supplied script.

## H. Automated test results

- Original baseline: **92 passed, 1 skipped** locally.
- Final SQLite suite: **128 passed, 2 skipped** (PostgreSQL-only concurrency).
- Docker / dedicated PostgreSQL suite: **130 passed**.
- One upstream Starlette/AnyIO deprecation warning; no failing tests.

Coverage: auth, incoming mapping/retries, private monitoring, packet rejection,
audio encoding/decoding/resampling, bounded buffers, bidirectional mock audio,
Hindi/English/Hinglish mocked transcripts, clarification, unknown medicine,
multiple lines, quantity correction, failed correction blocking confirmation,
full summaries, explicit confirmation, persistence, deduplication, disconnect
before/after confirmation, STT/TTS/database failure, timeout, interruption controls,
cleanup, browser API and UI-asset regressions. None are actual PSTN calls.

HTTP runtime smoke on localhost:8001: health connected, `/voice-demo` HTTP 200,
telephony disabled/unconfigured as intended. Manual browser microphone/playback
was not exercised in this implementation session.

## I. Real telephone test results

**All 11 mandatory handset tests: BLOCKED / NOT RUN. No live call occurred.**
The full test matrix is in PHASE4.md. No real answering, handset readback,
quantity correction or interruption behavior is claimed.

## J. Recorded latency measurements

Actual local Kokoro and Faster-Whisper-small CPU/int8 on Docker's **4 CPUs and
8,288,509,952 bytes RAM (~7.7 GiB)**. Synthesized utterances were downsampled to
8 kHz mono PCM16 and transcribed. No audio or order was persisted.

| Synthetic utterance | Audio | TTS | Bridge | STT | Total compute |
|---|---:|---:|---:|---:|---:|
| English request, first/cold | 2.2 s | 13.891 s | 0.008 s | 26.609 s | 40.508 s |
| Hindi request | 2.9 s | 8.794 s | 0.009 s | 18.129 s | 26.932 s |
| Hindi/Hinglish correction | 2.8 s | 5.244 s | 0.010 s | 23.728 s | 28.982 s |

These totals measure synthetic generation plus recognition, not a live conversation
round trip. They exclude caller speech, network, endpointing and response playback.
First row includes cold model loads; subsequent rows reuse models. The PostgreSQL
suite ran alongside the smoke, so contention may affect timing; no isolated
warm-call benchmark exists. Per-call `last_latency_ms` records STT, orchestration
and full TTS, excluding network/endpointing/playback.

Recognition produced `brufin` for BRUFEN and imperfect Hindi words. This is not an
accuracy pass. Measured performance **does not demonstrate responsive live calling**.
Evaluate smaller local STT models, warm startup and better local hardware, then
repeat medicine-name/clarification tests and live measurements. No paid fallback
was introduced.

## K. Known issues and blockers

- Provider account/number/KYC/streaming entitlement/public endpoint and live tests blocked.
- High model latency and transcription errors require further validation before
  accepting a responsive live demo. No production realtime claim.
- Native inference cannot safely be killed mid-thread; cleanup waits for a
  cancellation checkpoint. Hung native inference can exceed cleanup time limits.
- Energy endpointing, echo/noise, provider fields and real barge-in require actual
  audio verification. Rapid overlapping turns can fail closed. Single-worker demo.
- Existing general voice/order APIs lack authentication; expose only media publicly.
- Schema uses additive `create_all`; production migrations remain future work.

## L. Phase 5 readiness

**Not ready for live handoff.** Software can be reviewed and simulated locally.
Finish account activation, verify actual provider messages, resolve responsiveness
and recognition limitations, and pass all handset tests before declaring Phase 4
complete. Phase 5 was not started.
