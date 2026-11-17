# Phase 4 — incoming telephone integration

Status: implemented and tested with simulated provider events. **Live PSTN calling
is BLOCKED.** The owner cannot add a provider account. No number, KYC status,
AgentStream entitlement, public WSS endpoint, or real call has been verified.
This is not the completed live Phase 4 acceptance milestone. Phase 5 has not begun.

## Architecture and preserved safeguards

```text
Exotel incoming number → Voicebot applet → authenticated WSS /telephony/media
 → persistent CallLog → VoiceSession → OrderSession
 → PCM endpointing → Faster-Whisper → existing VoiceService
 → deterministic matching / corrections / summary / explicit confirmation
 → PostgreSQL → existing Kokoro TTS → PCM16 8 kHz mono → Exotel playback
```

The browser routes remain available with telephony disabled. No paid AI service
was added. The 20-record catalogue, order digest/token validation and one-order
constraint remain authoritative. The correction helper and persisted confirmation
guard are now integrated in VoiceService: failed corrections block summary and
confirmation, a successful correction clears the previous summary, and ambiguous
or unknown medicine references cannot silently update the last line. Completed
audio sessions are rejected before STT. The browser disables completed sessions
and offers a fresh order; starting over ends an existing active draft.

Telephone readbacks synthesize the **entire** assistant reply in bounded chunks;
they do not truncate long summaries to the browser's 600-character TTS limit.
Interrupted readbacks invalidate the summary guard. Deterministic order tokens
remain server-side. Speech recognition remains fallible: this system cannot
substitute medicines, suggest dosage, or offer medical advice.

## Provider evaluation — checked 16 November 2026

Exotel is the implemented provider, using its **Voicebot** applet (bidirectional),
not the one-way Stream applet. Its documented WSS handshake supports Basic auth;
the adapter uses this mechanism plus an expected Account SID. Call creation
occurs on the authenticated `start` event. A separate HTTP callback/resolver is
unnecessary for a static WSS URL and is deliberately absent. There is no fabricated
signature scheme. `TELEPHONY_WEBHOOK_SECRET` is reserved and unused.

The [official Voicebot protocol](https://docs.exotel.com/exotel-agentstream/voicebot-applet)
describes start/media/mark/clear/stop events and raw PCM. This adapter deliberately
supports the default 8 kHz, mono, signed little-endian PCM16 configuration only.
Other rates/codecs are rejected. Outgoing frames are 100 ms / 1600 bytes with
real-time pacing. Playback marks identify completed replies; clear cancels queued
audio. Field/codec compatibility and actual interruption behavior still require
verification against the activated account. Synthetic barge-in tests are not live
audio verification.

The [AgentStream setup guide](https://support.exotel.com/support/solutions/articles/3000132268-quick-guide-to-get-started-with-exotel-streaming-services)
requires KYC and streaming enablement. Ask Exotel support to enable Stream/Voicebot
for the account SID and confirm the correct regional instance. Account entitlement
has not been inspected because no account is available.

The [trial documentation](https://docs.exotel.com/business-phone-system/create-a-trial-account)
describes limited credits and a trial number/PIN; this is **not** evidence that a
particular trial includes AgentStream. Check the actual dashboard and obtain
confirmation of streaming, rental/per-minute rates, credit expiry and allowed
calling geographies before activation. Older provider help pages describe
different credit amounts. Do not assume an advertised amount applies to this
account or that the number is exclusive. No trial credit has been spent here.

No Twilio or alternative credentials are available. No alternative account was
created. A mock adapter remains available without registering or paying a provider.
An account-free mock cannot receive PSTN calls.

## Configuration

Copy the Phase 4 keys from `.env.example` into the private `.env`. Never commit
the latter. No real secret or demo number is supplied.

| Variable | Default / purpose |
|---|---|
| `API_PORT` | `8001`; never take this project's host port 8000 |
| `TELEPHONY_ENABLED` | `false`; opt-in transport |
| `TELEPHONY_PROVIDER` | `exotel`, or explicitly `mock` |
| `TELEPHONY_PUBLIC_BASE_URL` | empty; public HTTPS origin used to derive WSS and callback URLs |
| `TELEPHONY_STREAM_USERNAME` | empty; WSS Basic username chosen by operator |
| `TELEPHONY_STREAM_TOKEN` | empty; strong independent WSS password |
| `TELEPHONY_MONITOR_TOKEN` | empty; separate Bearer secret for private monitor and mock setup |
| `EXOTEL_ACCOUNT_SID` | empty; required for enabled Exotel streams and outbound dialling |
| `EXOTEL_API_KEY` | empty; Exotel REST API key for `Calls/connect` |
| `EXOTEL_API_TOKEN` | empty; Exotel REST API token for `Calls/connect` |
| `EXOTEL_API_BASE_URL` | `https://api.exotel.com`; use the regional Exotel host |
| `EXOTEL_CALLER_ID` | empty; activated ExoPhone used as outbound CallerId |
| `TELEPHONY_ALLOWED_DESTINATIONS` | empty; optional comma-separated E.164 demo allowlist |
| `TELEPHONY_DEMO_NUMBER` | empty; activated number only, never activation evidence |
| `TELEPHONY_WEBHOOK_SECRET` | empty; query secret required on `/telephony/status-callback` |
| `TELEPHONY_MAX_CALL_SECONDS` | `600`; total connection and persisted-call limit |
| `TELEPHONY_SILENCE_SECONDS` | `45`; idle caller timeout |
| `TELEPHONY_VAD_THRESHOLD` | `450`; PCM RMS endpoint threshold, tune with actual audio |

Generate each local password/secret using `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Use URL-safe credentials for the provider URL. `TELEPHONY_STREAM_USERNAME` and
`TELEPHONY_STREAM_TOKEN` are **your endpoint's** WSS Basic credentials;
`TELEPHONY_WEBHOOK_SECRET` protects the status callback. `EXOTEL_API_KEY` and
`EXOTEL_API_TOKEN` are separate provider-issued REST credentials used only to place
a real outbound call. Never put any of them in a `VITE_` variable.

Start only this API after checking the existing database is healthy:

```powershell
Set-Location C:\Users\SGW_05\MyProjects\medical_Ai
$env:API_PORT="8001"
docker compose up -d --no-deps --build api
```

Use `docker compose up -d --build` for a fresh project installation. Do not delete
database volumes. PostgreSQL has no host-published port. Models remain mounted
read-only; the existing Faster-Whisper model cache persists.

## Exact manual Exotel activation steps — BLOCKED until account access

1. Register/sign in to the appropriate Exotel regional account, verify the owner,
   and complete the provider's requested company/KYC onboarding. Do not bypass
   verification or purchase anything without the owner's approval.
2. In the actual account, check Phone Numbers / Virtual Numbers for the assigned
   trial number and PIN. Confirm allowed inbound callers, geographic coverage,
   remaining credits and rates with Exotel; do not copy a number from a help page.
3. Request **Voicebot / AgentStream bidirectional streaming** enablement for the
   Account SID using the support instructions above. Confirm whether this trial
   is eligible and whether any extra charges apply. The normal trial flow alone
   does not prove entitlement.
4. Configure private `.env` keys: enabled true, provider exotel, Account SID, REST
   API key/token, activated ExoPhone caller ID, independent Basic/monitor/webhook
   secrets, HTTPS public base and actual number. Restart only this API.
   `/telephony/status` reports configuration, never activation.
5. Publish only `/telephony/media` and `/telephony/status-callback` using the tunnel
   instructions below. Verify a valid public certificate, WSS upgrades,
   Authorization header forwarding, and an HTTPS POST to the callback URL.
6. In App Bazaar / the account's call-flow builder create an incoming flow with
   the **Voicebot applet**. Select static WSS URL, configured as
   `wss://USERNAME:PASSWORD@YOUR_HOST/telephony/media?sample-rate=8000`.
   Exotel documents translating the userinfo into a Basic Authorization header.
   Keep recording disabled. Confirm the exact 8 kHz PCM16 event schema with support.
7. Set the next applet to terminate the call (or a short goodbye followed by
   hangup). Closing our WebSocket moves Exotel to that applet; the gateway itself
   does not invoke a paid call-control API. Save and connect/install the flow on
   the number from step 2. Dashboard labels may vary by regional account.
8. For outbound demos, confirm `POST /v1/Accounts/{sid}/Calls/connect` is enabled for
   the same account and ExoPhone. The dashboard dial action calls this endpoint with
   `From`, `CallerId`, `StreamUrl`, `StreamType=bidirectional`, `TimeLimit`,
   `CustomField` (internal call ID), and `StatusCallback`. The backend derives a
   `wss://USERNAME:PASSWORD@YOUR_HOST/telephony/media?sample-rate=8000&bot_call_id=...`
   URL; no provider-applet guess is needed for this flow.
9. Confirm Exotel sends `CallSid` and `Status` callbacks to the public callback URL.
   Terminal `completed`, `failed`, `busy`, `no-answer` and `canceled` statuses are
   persisted; `answered` events mark a ringing call connected.
10. Call from an allowed verified handset, following trial PIN rules if applicable.
   Observe the private call log and inspect the saved database order. Run the live
   matrix below. Only then describe the number as connected and tested.

## Public HTTPS/WSS access

No tunnel binary or public endpoint was found/configured. Public access is BLOCKED,
and no local service was exposed. `localhost` is not reachable by Exotel.

For an operator with a Cloudflare account/domain, install official `cloudflared`,
then use a **named tunnel** with a restricted ingress file. This can use available
free infrastructure; verify account terms. No domain purchase is authorized here.
See [Cloudflare configuration documentation](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/).

```powershell
cloudflared tunnel login
cloudflared tunnel create medical-ai-demo
cloudflared tunnel route dns medical-ai-demo YOUR_HOST
# Copy docker/cloudflared.example.yml to a private config file; set tunnel UUID,
# the absolute credentials-file path emitted above, and YOUR_HOST.
cloudflared tunnel --config C:\path\to\private-config.yml ingress validate
cloudflared tunnel --config C:\path\to\private-config.yml run medical-ai-demo
```

The template forwards **only** `/telephony/media` and `/telephony/status-callback`
to `http://127.0.0.1:8001`; everything else returns 404. Leave `/docs`, catalogue/orders/voice APIs, monitor,
PostgreSQL and Ollama private. Do not put Cloudflare Access's browser login in
front of the provider socket. Keep Basic auth intact through the proxy. Stop the
tunnel when not testing. A quick tunnel pointing at the entire API would expose
existing unauthenticated order routes and is not the supplied setup.

## APIs and development simulation

| Method | Route | Access / meaning |
|---|---|---|
| GET | `/telephony/status` | Redacted config flags; `live_verified` requires a real connected call |
| WS | `/telephony/media` | Enabled provider + HTTP Basic handshake |
| POST | `/telephony/status-callback` | Public provider callback with `token` query secret |
| POST | `/dashboard/calls/outbound` | Dashboard Bearer token; places a real Exotel call |
| GET | `/dashboard/calls/{call_id}` | Dashboard Bearer token; includes masked phone and transcripts |
| POST | `/telephony/mock/incoming` | Monitor Bearer token; mock provider only |
| GET | `/telephony/calls/{call_id}` | Monitor Bearer token; phone number excluded |
| GET | `/telephony/calls` | Monitor Bearer token; latest 20 redacted logs |

For development set provider `mock`, enabled true and nonempty local Basic/monitor
secrets in `.env`. Recreate only the API so it reads new settings. The mock uses
the same authenticated JSON/audio stream shape; it is explicitly a simulation.

```powershell
# $env:TELEPHONY_MONITOR_TOKEN must be set locally, never paste it into chat.
$headers = @{Authorization="Bearer $env:TELEPHONY_MONITOR_TOKEN"}
$body = @{call_reference="simulated-demo-001"} | ConvertTo-Json
Invoke-RestMethod http://localhost:8001/telephony/mock/incoming -Method Post -Headers $headers -ContentType application/json -Body $body
```

Repeat the request to verify the same internal call ID. Open `/voice-demo`, enter
that call ID and the monitor token, and click Refresh call. Leave the ID empty to
view the most recent 20 calls. Tokens are kept only
in the page input, not localStorage or query parameters. The monitor shows status,
timestamps, order reference, error category and latest processing latency; it
does not reveal a customer phone number or raw audio.

Automated TestClient WebSocket tests exchange PCM frames and fake STT/TTS to run
complete orders without a provider or model downloads. The local-model smoke
tool separately synthesizes speech, converts it to 8 kHz and transcribes it:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/ -q
docker compose exec -T api python -m pytest tests/ -q
docker compose exec -T api python -m app.scripts.telephony_smoke
```

Tests use only the dedicated `_test` PostgreSQL database, never development orders.
The smoke script persists no order or audio. Its synthesized Hindi/English/Hinglish
audio is not human telephone-call testing. Timings include cold initialization on
the first utterance; compare warm repetitions separately.

## Lifecycle, limits and cleanup

`RINGING → CONNECTED → ACTIVE ↔ PROCESSING → CONFIRMING → COMPLETED`
with `DISCONNECTED` and `FAILED` terminal alternatives. UTC `CallLog` rows map
provider+call reference uniquely to public voice/order session IDs. PostgreSQL
advisory locking serializes duplicate incoming events across service commits;
an atomic claim prevents simultaneous media streams attaching to one order.
Retries reuse the mapping but **do not reopen** a connected or terminal stream.
Retrying an ended call ID cannot create another order. A new call reference creates
a separate draft. This demo policy favors isolation over mid-call reconnection.

Only one media call is accepted per API process; a second socket gets 1013. Run
the supplied single Uvicorn worker. Buffers hold at most a 12-second utterance plus
one bounded pending packet; there is one queued turn. Oversized/odd PCM, invalid
base64, wrong account/stream/rate and unordered media fail closed. The server frame
limit is 150 KB with four queued frames. Endpointing uses 800 ms silence and an
energy threshold, not a trained real-time VAD. Silence and duration limits end the
stream. DTMF never confirms an order.

Caller speech can issue `clear` and cancel local playback. An interrupted reply
invalidates its summary; new recognition runs only after the current worker has
finished. Speech arriving while a recognition turn is still being processed can
exceed the deliberately small queue and terminate the demo call. Full-duplex
overlapping conversation and live acoustic barge-in remain unverified.

Before confirmation, stop/disconnect/failure ends the voice session and cancels
the draft. After a committed order, cleanup recovers and preserves that order,
even if TTS or call logging subsequently fails. Disconnect during STT sets a
cancellation checkpoint before the transcript can mutate/confirm the draft.
If explicit confirmation was already processed and committed when a hangup is
detected, the confirmed order is preserved. Audio is never stored by the gateway;
the existing STT temporary WAV is deleted by its adapter.

Inference runs in a worker thread so packet reception remains responsive. Python
cannot forcibly cancel an active native STT/TTS inference safely; cleanup waits
for that worker/checkpoint. A hung native engine can therefore exceed the intended
cleanup duration. This is a CPU demo, not a production isolation/process manager.

After a process crash or restored database outage, run:

```powershell
docker compose exec -T api python -m app.scripts.reconcile_calls
```

This closes logs older than maximum call duration plus five minutes. It cancels
unfinished drafts and recovers already committed orders, never creates orders.
Ordinary calls clean up automatically. Logging failures report only internal call
IDs and generic categories. Existing `create_all` adds the new table without
deleting data; production migrations remain future work.

## Hardware, latency and limitations

Use the existing CPU/int8 Whisper-small and Kokoro models. A practical starting
allocation is four CPU cores and 8 GB RAM for one demo call; this is a sizing
estimate, not a throughput guarantee. Keep approximately 2 GB disk for models and
cache in addition to Docker images. Cold loads, long summaries and CPU contention
can make the caller wait. `last_latency_ms` measures STT + orchestration + complete
TTS generation; it excludes caller speech, 800 ms endpointing and playback/network.
See `PHASE4_REPORT.md` for actual measured timings and hardware.

If measured warm latency is unsuitable, first evaluate a smaller local Whisper
model, lower beam size, warm model startup, and a better local CPU/GPU host. Re-run
medicine-name/clarification regressions. Do not silently add cloud inference or
paid resources. No realtime service-level claim is made.

## Live acceptance matrix — all BLOCKED / not run

| Test | Required evidence |
|---|---|
| Automatic answer | Activated incoming number answers and plays greeting |
| Hindi medicine | Actual handset order reaches correct catalogue row |
| English medicine | Same, in English |
| Hinglish medicine | Same, in Hinglish |
| Multiple medicines | All intended lines preserved |
| Unclear pronunciation | Spoken clarification; no guessed variant |
| Unknown medicine | No invented/substituted item |
| Quantity correction | Actual call changes draft and repeats full summary |
| Explicit confirmation | Exactly one persisted PostgreSQL order |
| Disconnect before confirm | No final order |
| Disconnect after confirm | Saved order preserved with no duplicate |

Also verify echo/noise, barge-in clear behavior, silence timeout, trial restrictions
and actual per-minute charges. Record call reference, UTC timestamps, order ID and
observed latency privately. Never mark these rows passed from simulated results.

## Troubleshooting and remaining client dependencies

- 1008 / unauthorized: ensure telephony enabled, nonempty Basic settings and Exotel
  Account SID; verify provider forwards Authorization. Never log the header.
- Protocol failure: confirm 8 kHz PCM16, field names, account and stream IDs. Other
  negotiated formats require an explicitly tested adapter extension.
- 1013: another media connection holds the single-call slot; end it and retry with
  a new call reference. Do not increase workers to hide CPU latency.
- No speech: tune RMS threshold with real noise; check PCM endianness and rate.
- Slow/no reply: check model files/cache and CPU, then run the smoke tool. Missing
  STT/TTS closes an unconfirmed call safely instead of announcing success.
- Live number/public endpoint: BLOCKED until the owner can register/activate a
  provider and publish restricted WSS. No account-free alternative can attach a
  temporary incoming PSTN number without someone supplying telephony service.
- Provider dashboard/API activation, KYC, trial approval and handset tests remain
  manual client dependencies. They were not bypassed or completed here.
