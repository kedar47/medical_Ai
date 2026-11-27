# CLAUDE.md — guide for Claude Code in this repository

AI phone medicine-ordering agent: FastAPI + SQLAlchemy backend (`app/`), React
dashboard (`frontend/`), phone calls via Plivo (live number) or Exotel
(`app/api/telephony_routes.py`, `app/services/telephony/`),
Sarvam AI speech-to-text with local Faster-Whisper backup, Kokoro voice.

Full Sarvam details: `docs/SARVAM_SETUP.md`. General setup: `README.md`.

## Local setup (do this when asked to "set up the project")

1. Python 3.12 virtual env in `.venv`:
   - Windows: `python -m venv .venv` then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
2. `.env` in the project root:
   - If missing, copy `.env.example` to `.env`.
   - For a quick local run without Docker use SQLite:
     `DATABASE_URL=sqlite+pysqlite:///./medicine_agent.db`
   - Sarvam: make sure these lines exist (add them if not):
     ```
     VOICE_STT_PROVIDER=sarvam
     SARVAM_API_KEY=
     SARVAM_STT_MODEL=saarika:v2.5
     SARVAM_STT_LANGUAGE=unknown
     SARVAM_TIMEOUT_SECONDS=8
     ```
   - **Ask the user for the Sarvam API key** and write it after
     `SARVAM_API_KEY=`. Never invent one, never print it back, never write it
     anywhere except `.env`.
3. Kokoro voice files must exist at `models/kokoro/kokoro-v1.0.int8.onnx` and
   `models/kokoro/voices-v1.0.bin` (not in git). If missing, tell the user to
   copy them from the project owner; do not download random model files.
4. Load the client's demo medicine list (matches production):
   `.\.venv\Scripts\python.exe -m app.scripts.import_medicines data/client_demo_medicines.csv`
   then `.\.venv\Scripts\python.exe -m app.scripts.activate_only_csv data/client_demo_medicines.csv`
5. Start the backend:
   `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001`
   (or `docker compose up -d --build` with Docker; API on port 8000).
6. Verify: `GET http://127.0.0.1:8001/voice/status` must show
   `"stt":{"provider":"sarvam","ready":true,...}`. If it shows
   `faster-whisper`, the `.env` lines or key are missing, or the app was not
   restarted.
7. Dashboard (optional): `cd frontend && npm ci && npm run dev`.

## Rules

- Never commit `.env`, API keys, tokens or model files. `.env` is gitignored.
- Do not push to `main` or deploy to the production server unless the user
  explicitly asks. Production deploys are done by the project owner.
- Run the tests before committing: `.\.venv\Scripts\python.exe -m pytest -q`.
  Tests never call Sarvam (`tests/conftest.py` forces Whisper).
- Medicine matching is safety-critical: never add an item the caller did not
  clearly choose, keep the read-back before confirmation, and keep the tests
  in `tests/test_medicine_matching_safety.py` passing.
- When a live call shows a misheard medicine name, add the spelling to
  `app/core/medicine_aliases.py` and a test in `tests/test_client_demo.py`.

## Useful commands

| Task | Command |
|---|---|
| Tests | `.\.venv\Scripts\python.exe -m pytest -q` |
| Import a medicine CSV | `.\.venv\Scripts\python.exe -m app.scripts.import_medicines data/client_demo_medicines.csv` |
| Voice status | `curl http://127.0.0.1:8001/voice/status` |
| Docker logs | `docker compose logs -f api` |
