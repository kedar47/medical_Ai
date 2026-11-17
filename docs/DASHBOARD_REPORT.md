# React dashboard delivery report

Verified 16 November 2026. Frontend and supported API workflows implemented.
Physical microphone/human speech and real incoming telephone tests remain unverified.

## A. UI implementation summary

React 18, TypeScript, Vite, React Router, MUI v5, TanStack Query, Axios, React Hook
Form, Zod and Lucide. Ten routes share a responsive navy/teal workspace with sidebar,
mobile menu, status badges, loading skeletons, retries, empty states, dialogs and
toasts. Major pages are lazy-loaded. Server state stays in Query; only active-session
references, audio resources, ephemeral transcript and manual test notes use UI state.
No business records or analytics are fabricated. Catalogue management is read-only.

## B. Page-by-page status

| Page | Implementation / verification |
|---|---|
| Overview | Real whole-database metrics and recent orders; real browser verified |
| AI Voice Assistant | Real session/text/audio API integration, recording cleanup, playback controls, summary/confirmation, fresh sessions; real text workflow and TTS generation verified; physical microphone/audibility pending |
| Medicine Catalogue | Real 20-record catalogue, search, separate variants, pagination, drawer; real browser verified |
| Medicine Matching | Real BRUFEN clarification, explicit candidate choice and draft addition; browser verified; fuzzy/unknown behavior covered in backend tests |
| Order Sessions | Actual creation, item additions, quantity edits, review, consent/token confirmation and terminal controls; browser verified |
| Orders | Real persisted list and full details; browser verified |
| Live Calls | Disabled state and empty real logs; browser verified. Live provider calling BLOCKED |
| Call History | Protected paginated/filterable persisted metadata; real empty response verified; nonempty mock row/details component/backend tested |
| System Status | Real health/configuration flags; browser verified |
| Demo Testing | In-memory manual checklist, honest blocked handset scenarios; component tested; mock setup action disabled while provider unavailable |

## C. API integration report

| Screen | Endpoints used | Real connectivity |
|---|---|---|
| Overview | `/dashboard/stats`, `/dashboard/orders`, `/voice/status`, `/telephony/status` | Verified |
| Voice | `POST /voice/sessions`, `GET /voice/sessions/{id}`, `POST .../turns`, `POST .../audio-turns`, `POST .../end`, `GET /order-sessions/{id}` | Session/text/end/draft verified; multipart/audio path component/backend tested, no human-mic browser test |
| Catalogue | `/medicines?q=&limit=&offset=` | Verified, 20 records / 3 BRUFEN variants |
| Matching | `POST /medicine-matching/search` | Verified clarification and explicit selection |
| Sessions | `/dashboard/sessions`, Phase 2 session/item CRUD, summary and confirm | Verified creation/add/update/summary/confirm; cancellation backend/component tested and voice-end browser verified |
| Orders | `/dashboard/orders`, `/orders/{id}` | Verified saved rows/lines |
| Live calls | `/dashboard/calls?active=true`, `/telephony/status` | Verified disabled/empty; foreground polling configured |
| Call history | `/dashboard/calls`, `/dashboard/calls/{id}` | Empty real response verified; populated records tested with isolated fixtures |
| System | `/health`, `/voice/status`, `/telephony/status` | Verified |
| Testing | Existing `/telephony/mock/incoming` only when mock enabled, plus in-memory manual notes | Action blocked correctly; no live simulation was manufactured |

The real-HTTP smoke verified PostgreSQL connected, 20 medicines, three BRUFEN
records, `CLARIFICATION_REQUIRED`, protected paginated histories and disabled/
unverified telephony. It also verified direct unauthenticated `/dashboard/stats`
returns HTTP 401. It creates no records and does not use mocked HTTP responses.

## D. Files created / modified

Full absolute file manifest: [DASHBOARD_FILES.md](DASHBOARD_FILES.md).
Key sources are `frontend/src/App.tsx`, `frontend/src/api/client.ts`,
`frontend/src/pages/`, `frontend/src/components/`, `frontend/src/types/`,
`frontend/src/styles.css`, `frontend/src/tests/`, `frontend/vite.config.ts`,
`frontend/scripts/api-smoke.mjs` and `app/api/dashboard_routes.py`.

No git staging, commits or pushes were made. No unrelated application was changed.
Two clearly labelled UI-verification orders were intentionally saved in the local
development database to verify actual persistence; they remain pending pharmacy
review and do not trigger dispensing, delivery or a telephone call. Existing orders
were preserved. The fresh unconfirmed voice test was ended/cancelled.

## E. Necessary backend changes

- Added protected read-only `/dashboard/stats`, `/sessions`, `/orders`, `/calls`
  and `/calls/{id}` routes over existing tables. Pagination/filtering, complete-query
  totals, expired-session handling and phone masking have backend tests.
- Added safe-disabled `DASHBOARD_ENABLED` / `DASHBOARD_TOKEN` settings; configured
  an independent local secret privately in `.env`, with disabled example defaults.
- Vite's loopback proxy adds that token server-side. It is not a cosmetic login
  screen and is not multi-user authentication. The dashboard must remain local.
- Existing browser TTS now reuses full-speech chunk synthesis, preventing silent
  truncation of long readbacks. Existing order and correction guards remain authoritative.
- No schema/destructive migration, provider activation, CORS wildcard, paid AI API
  or unrelated Docker change. Only this API was recreated on host port 8001.

## F. Actual testing results

| Check | Result |
|---|---|
| Vitest + Testing Library | **30 passed** |
| Full local backend suite | **138 passed, 2 skipped** (PostgreSQL-only concurrency) |
| Full Docker/PostgreSQL backend suite | **140 passed** |
| Real HTTP proxy/API integration smoke | **Passed** |
| Desktop browser UI (1440 × 1000) | Inspected and exercised |
| Mobile browser UI (390 × 844) | Inspected; menu works; document width equals scroll width (no page overflow) |

Tests cover navigation, aggregates, search/clarification, phone validation, sessions,
add/update/remove, fresh token/consent, stale-token errors, confirmed-state editing,
voice language/start/text/audio transcription, permission denial, mocked playback,
fresh-order flow, summary gating, disabled and mock call labels, empty history,
system flags and manual checklist state. Component fixtures are synthetic and are
not presented as real API evidence. Existing backend tests remain passing.

Warnings: upstream React Router v7 future-flag notices in component tests and one
Starlette/AnyIO deprecation warning in backend tests. No failed tests remain.

## G. Build results

`npm run build`: TypeScript `tsc --noEmit` and Vite production compilation **passed**.
Pages are lazy-loaded; largest shared MUI chunk ~234 KB / 72 KB gzip, React ~163 KB /
54 KB gzip, Query/Axios ~96 KB / 33 KB gzip. Voice page ~11.5 KB / 4.8 KB gzip.
Lockfile is included for `npm ci`. Source is formatted with Prettier.

## H. Localhost URLs

- Dashboard: http://127.0.0.1:5173
- Backend / Swagger: http://localhost:8001/docs
- Existing browser demo: http://localhost:8001/voice-demo
- Health: http://localhost:8001/health

The dev server is loopback-only. Production preview also binds to loopback 5173
and retains the deliberate private proxy. See DASHBOARD.md to start/restart it.

## I. Browser verification and manual test instructions

Completed real browser workflows:

1. Read real overview counts and persisted existing orders.
2. Loaded 20 approved medicines, searched BRUFEN and inspected all three separate strengths.
3. Created `UI verification — local test`, explicitly selected BRUFEN 400 mg and
   CLOMIN, changed BRUFEN to three strips, reviewed every line and consented.
   Read back `order_c8bb287002484cc29135e5c9bd645806` with BRUFEN 3 STRIP and CLOMIN 2 STRIP.
4. Created `UI voice verification — local test`. English request, Hindi request
   and Hinglish quantity correction reached the actual orchestrator using typed
   inputs. The correction updated the existing BRUFEN line without adding another.
5. Tried an unknown-medicine correction then `yes confirm`; backend blocked submission.
   Resolved the correction, requested a fresh full Hindi summary and received local
   TTS audio. Explicit confirmation returned
   `order_f9bd23becf9e45d8b61d418d7e07fbd9`; audio input and editing became unavailable.
6. Start New Order created a fresh empty session with an English greeting and a
   different session ID. An English request returned an English assistant response.
   End Conversation cancelled that unfinished test draft.
7. Live calls showed not configured/unavailable and zero actual records; System
   Status reflected connected PostgreSQL, configured STT/TTS and disabled Ollama.
8. Inspected desktop/mobile presentation and mobile menu; checked DOM overflow.

Detailed repeatable acceptance instructions for all ten screens, physical microphone,
Hindi/English/Hinglish speech and playback are in [DASHBOARD.md](DASHBOARD.md).
Preview: [dashboard-preview.jpg](dashboard-preview.jpg).

## J. Pending issues and explicit limits

- **No real incoming telephone call tested.** Provider account, number, KYC,
  AgentStream entitlement and public WSS remain blocked. UI does not claim activation.
- **Physical microphone and human spoken-language accuracy remain unverified.**
  Multipart capture/transcription and permission/playback behavior have mocked
  component/backend coverage. Real text requests and TTS generation are separate evidence.
- A local TTS summary returned audio and playback was attempted. Physical audibility
  was not verified. Browser stop handling was hardened to detach old error callbacks
  before clearing audio, avoiding spurious playback errors on intentional stops.
- Backend speech latency remains approximately 27–41 seconds from Phase 4's report;
  frontend progress indicators do not make inference faster. Native inference cannot
  be rolled back by cancelling an HTTP request. Uncertain commits require retrieval.
- No current persisted call logs existed for a real nonempty history walkthrough.
  Populated mock rows/filter/detail behavior is tested with isolated fixtures.
- Loopback proxy access is for a trusted local demo, not public deployment or
  multi-user authorization. Existing general order/voice APIs remain local/demo-only.
- Ephemeral transcripts/manual checklist notes reset on reload. Persisted draft
  sessions can be reopened; old transcripts cannot be reconstructed.
- No payment, dispensing, medicine substitution, dosage recommendation or clinical
  advice was added. No further development phase was started automatically.

## Microphone visibility follow-up

The microphone flow now transcribes first using `/voice/transcribe`, displays a
prominent **You said** panel, automatically scrolls it into view, and fills an
editable request field. **Send request** submits the reviewed words to the existing
orchestrator. Recognition/recording alone does not mutate a draft; unsent speech
blocks confirmation and can be discarded. Transcript visibility no longer waits
for AI orchestration or TTS. This is after-recording transcription, not live captions.

Updated frontend verification: **31 component tests passed** and TypeScript/Vite
production build passed. Regression checks cover the original recognized text,
editing medicine/quantity before sending, no automatic order mutation, and the
existing transcription-only multipart endpoint. Physical human microphone input
and recognition accuracy remain a separate manual check. Backend code is unchanged.

## Hindi speech understanding follow-up

The backend now recognizes explicit BRUFEN Hindi spelling variants, Hindi mg
units, clear spoken strengths (two/four/six hundred with a unit), Devanagari digits,
strip/patta forms and common ASR punctuation/filler spellings. Noisy medicine
utterances with a known catalogue name return explicit variant suggestions rather
than the misleading generic catalogue-not-found reply. Unknown strengths are never
guessed. Hindi follow-up identification and existing-line quantity corrections are
covered. Browser and telephone orchestration share this implementation.

Faster-Whisper now receives active catalogue medicine names and their approved
aliases as vocabulary hints, without customer data, order quantities or packaging
strengths. Supported mechanism: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
This can aid recognition but is not proof of human-speech accuracy.

Real local-model synthetic smoke (not a human or PSTN call): browser 24 kHz returned
"मुझे ब्रूफेन, दो सा एमजी के चार पते चाहीए" in 16.26 seconds; telephone-format 8 kHz
returned "मुझे ब्रूफेन, 200, MG के 4 पत्ते चाहीए" in 4.48 seconds. Cold/warm timing
differs and is not a controlled performance comparison. Regression tests verify
that the unclear browser strength prompts clarification while the explicit 200 MG
phone-format transcription selects the correct catalogue variant with quantity 4.
No order was created by the synthetic audio probe.
