# React dashboard — local operator guide

The React/TypeScript app in `frontend/` integrates the four existing backend
phases. It uses MUI v5, Vite, React Router, TanStack Query, Axios, React Hook Form,
Zod and Lucide. There is no fake business-data fallback and no paid frontend or AI
service. Charts were intentionally omitted: no reliable historical series API exists.

## Run locally

```powershell
Set-Location C:\Users\SGW_05\MyProjects\medical_Ai
$env:API_PORT="8001"
docker compose up -d --no-deps --build api
Set-Location frontend
npm ci
npm run dev
```

Frontend: `http://127.0.0.1:5173`. Backend: `http://localhost:8001`.
Legacy browser demo remains at `http://localhost:8001/voice-demo`; Swagger `/docs`.
Leave the existing PostgreSQL container and unrelated applications intact.

For the compiled app: `npm run build`, stop the dev server, then `npm run preview`.
Both servers bind to loopback, use strict port 5173 and proxy `/api` to port 8001.
Do not run dev and preview on that same port concurrently. No CORS wildcard was added.

## Private operator access

New `/dashboard/*` read endpoints require `DASHBOARD_ENABLED=true` and a strong
`DASHBOARD_TOKEN` in the **project root private `.env`**. The implementation configured
an independent random local token there without displaying it. A fresh installation
must generate one locally, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Never commit `.env` or put the token in any `VITE_` variable.

Vite loads that root setting server-side and attaches it only to dashboard API
requests. It strips browser Authorization headers and never sends the operator
secret to the client bundle. The browser uses `VITE_API_BASE_URL=/api` (the default).
The server-side target can be overridden with `DASHBOARD_API_TARGET` in the root
`.env`; its default is `http://127.0.0.1:8001`. Restart Vite and recreate only the API
after changing root settings. Loading `.env` changes is not a credential login UI.

This is a **local operator dashboard**, not multi-user authentication. Anyone with
access to the local frontend server can operate the demo. Do not expose 5173, the
full FastAPI app, or the dashboard proxy through a tunnel. Existing Phase 2/3 routes
are still demo APIs. The Phase 4 tunnel template must continue to expose only
authenticated `/telephony/media`; PostgreSQL and Ollama remain private.

## Ten screens and real API contracts

| Screen | Real APIs | Behavior |
|---|---|---|
| Overview | `/dashboard/stats`, `/dashboard/orders`, `/voice/status`, `/telephony/status` | Whole-database counts; recent persisted orders, accurate config flags |
| AI Voice Assistant | `/voice/sessions`, `/{id}/turns`, `/{id}/audio-turns`, `/{id}/end`, order-session GET | Actual orchestrator, local recording/playback and current draft |
| Medicine Catalogue | `/medicines?q=&limit=&offset=` | Read-only table, debounce, pagination, original row and packaging drawer |
| Medicine Matching | `POST /medicine-matching/search` | Explicit choice and variant clarification; fuzzy suggestions never auto-add |
| Order Sessions | `/dashboard/sessions`, Phase 2 session/item/summary/confirm APIs | Create/cancel, positive quantities, stale-token handling and consent dialog |
| Orders | `/dashboard/orders`, `/orders/{order_id}` | Persisted order history, immutable details, masked phones |
| Live Calls | `/dashboard/calls?active=true`, `/telephony/status` | Five-second foreground polling; no provider media socket opened |
| Call History | `/dashboard/calls`, `/{call_id}` | Paginated/filterable metadata, masked phones, errors, durations and order IDs |
| System Status | `/health`, `/voice/status`, `/telephony/status` | Database health and configuration, not fabricated live verification |
| Demo Testing | in-memory operator notes; existing `/telephony/mock/incoming` when enabled | Manual evidence separated from automation and PSTN tests |

`/dashboard/stats`, `/dashboard/sessions`, `/dashboard/orders`,
`/dashboard/calls` and `/dashboard/calls/{call_id}` are new protected **read-only**
endpoints. Limits are 1–100, offsets nonnegative; totals count complete filtered
queries, never a partial page. Expired drafts are excluded from active counts
without modifying them. Call histories include explicit provider labels. Missing
caller IDs stay unavailable. Call totals include mock records, labelled as such.

## Ordering and safety

1. Create a manual session or start a voice conversation. Optional customer phone
   accepts only E.164; empty is allowed and no sample number is submitted.
2. Match a medicine. Explicitly choose its medicine ID and packaging. Enter positive
   integer quantity/unit; the backend remains authoritative for maximum quantity.
3. Add more medicines, edit quantities or confirm removal in a dialog.
4. Manual sessions retrieve a fresh summary/token. Read every line and check the
   explicit consent box. Any mutation discards that summary. A stale/expired token
   is discarded on error and requires a new review; mutations are never auto-retried.
5. Voice-associated sessions route manual fallback changes through VoiceService.
   Review/confirmation uses the voice orchestrator, rather than bypassing its
   persisted failed-correction guard with a direct Phase 2 confirmation.
6. Review the complete voice summary, then explicitly confirm in the dialog or by
   typed/spoken approval. The backend handles its current token/digest. Confirmation
   controls remain disabled until a `show_summary` response.
7. Display success only when an actual order is returned. Confirmed orders disable
   input/editing. Start New Order ends the old voice session and creates fresh voice
   and order IDs; no confirmed draft is reused.

Browser TTS now uses the existing full-speech chunk helper, so long summaries are
not silently truncated at 600 characters. This is a minimal integration fix; no
business workflow was rebuilt. Quantity helper/guard integration remains tested.

## Voice controls and limits

Start Conversation, Start/Stop Recording, Play AI response, Stop playback, End
Conversation and Start New Order are wired to real state. Hindi maps to `hi`,
English to `en`, Hinglish to the backend's Hindi setting; Auto uses a Hindi greeting
and omits the turn language override, allowing existing STT/text language detection.

Microphone permission is requested only on Record. Capture chooses a browser-supported
WebM/Opus, Ogg/Opus or MP4 format and posts multipart `file`, `language` when selected,
and `speak`. A recording is capped at 60 seconds / 10 MB; empty/oversized recordings
produce an error. Tracks, timers and audio handlers are cleaned on stop/unmount.
Autoplay denial or decode failure has a visible text fallback and replay control.
No transcript or raw audio is persisted by React. Timeline/checklist state is memory
only; reload clears it. Persisted draft sessions can be reopened via Order Sessions.

Queries support AbortSignal and cancel when unused. **Order/voice mutations are not
aborted or retried automatically:** cancelling an HTTP request cannot roll back a
database commit. End Conversation is available after processing completes. Navigating
away stops capture/playback; a pending server mutation may still finish. Retrieve the
session/order before retrying uncertain submissions. The frontend does not fix the
previously measured 27–41 second backend inference latency.

## Telephony and mock limitations

Telephony remains disabled, provider unconfigured, `live_verified=false`. The screen
states that incoming calls are unavailable. No fabricated number/ringing activity
exists. `/voice/status` readiness means configured dependencies, not an inference
probe or proof of a real call. Public WSS reachability remains unverified.

The optional mock incoming tool is disabled unless the backend explicitly enables
provider `mock`. It uses the existing private `TELEPHONY_MONITOR_TOKEN` through the
server-side proxy and creates only a simulated RINGING call mapping. It does not
stream speech, call a handset or bypass provider onboarding. Missing monitor
credentials return an honest error. Automated Phase 4 mock audio tests remain separate.

## Automated checks

```powershell
# From frontend/, with backend and dev server running for the integration smoke:
npm test
npm run build
npm run test:integration
# From project root:
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
docker compose exec -T api python -m pytest tests/ -q
```

Vitest/Testing Library tests mock API/audio responses; those are not real API proof.
`test:integration` makes real HTTP requests through the local proxy, verifies database
health, catalogue total/variants, matching, protected reads and truthful telephony
flags. It does not create orders. Backend tests use the dedicated test database.
See `DASHBOARD_REPORT.md` for actual results, browser observations and remaining checks.

## Manual localhost acceptance steps

1. **Overview:** verify catalogue count is 20. Compare order/call/draft totals to
   history endpoints; zero is valid. Confirm provider-disabled status.
2. **Catalogue:** browse both pages; search BRUFEN and see three separate strengths.
   Open details for source row/packaging; catalogue editing is unavailable.
3. **Matching:** search BRUFEN, inspect clarification. Test an unknown name; no
   automatic selection should occur. Choose an exact medicine only with an active draft.
4. **Sessions:** create a clearly labelled test customer with no phone. Add BRUFEN
   400 mg and CLOMIN via Matching, change quantity, test removal/cancel dialogs,
   review every line, check consent and confirm. Retrieve the actual order in Orders.
5. **Voice:** start a labelled test session. First use typed English, Hindi and
   Hinglish requests. Test `Brufen 400 ki quantity 3 strips kar do`; verify the
   existing line changes. Try a correction for an unknown medicine then `yes confirm`;
   confirmation must remain blocked. Resolve the correction, request a fresh summary,
   confirm and check persisted order details.
6. **Microphone/audio:** on a physical supported browser, grant Record permission
   yourself. Say equivalent English/Hindi/Hinglish requests, stop and inspect actual
   transcription. Listen to the complete summary, test replay/stop, confirm and
   verify recording is unavailable afterward. Start New Order and verify empty lines
   and fresh IDs. Test permission denial with typed fallback. This requires real
   human speech; component mocks do not verify recognition quality.
7. **Calls:** disabled/empty is expected. Inspect an existing real or mock log when
   available; verify mock labels and masked callers. Never use a simulated record
   as live-call evidence.
8. **System:** check connected database, configured local voice components, disabled
   optional Ollama, and unverified telephony/WSS.
9. **Demo Testing:** select manual outcomes; verify the explanatory label and reset
   on reload. Phone-specific tests remain blocked.
10. **Responsive/accessibility:** test 1440 px desktop and 390 px mobile. Use the
    hamburger menu, keyboard Tab/Enter, visible focus, labelled dialogs and tables
    within their own horizontal scroll containers. Test a real microphone on HTTPS
    or localhost, never an insecure remote origin.

## Troubleshooting

- Operator authentication / 503: ensure the root private `.env` has both dashboard
  settings; recreate API and restart Vite. Never paste the secret into the UI.
- API unavailable: verify port 8001 and Docker health; leave other projects alone.
- Stale summary: review again. Do not reuse old confirmation tokens.
- Confirmed/expired session: start fresh; never keep submitting audio to it.
- Microphone unavailable/denied: use a supported localhost/HTTPS browser or typed
  fallback. No browser privacy settings are changed by the app.
- Slow speech: wait for processing and inspect model status. This is unchanged
  backend latency, not a UI freeze. No paid inference fallback is added.
- No call history: no provider or simulation records may exist. The UI must show empty.
- Public deployment: unsupported until proper multi-user authentication and
  authorization are designed. Loopback proxy access is intentionally local only.

## Microphone transcription review update

After Stop Recording, the UI sends the clip to the existing `/voice/transcribe`
endpoint and shows **You said** with the recognized words. The same words populate
the editable request field. Correct a medicine name or quantity if recognition is
wrong, then press **Send request** to use the existing voice orchestrator. The AI
reply and persisted draft reflect the submitted text. Recording/transcribing alone
does not change an order. Discard recording clears an unsent clip's text. Complete
order review/confirmation is unavailable while recognized speech awaits submission.
This is post-recording local transcription, not live word-by-word recognition.
No browser cloud speech-recognition service or paid API was added.
