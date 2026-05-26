# Talk Confident — Backend

A FastAPI backend that powers an Android app helping senior professionals build confident
spoken English. It uses **Gemini 2.5 Flash** for native audio understanding (no separate
STT), a respectful executive-coach persona, and a context-budget manager that keeps
long-term usage affordable via summarization + Gemini's Explicit Context Caching.

## Highlights

- **Audio stays on the device.** The server never persists raw audio. Uploads stream
  through an in-memory buffer to Gemini and are dropped immediately after evaluation.
- **Google Sign-In only.** No passwords, no email/SMTP. The Android client posts a
  Google ID token to `/auth/google`; the server verifies it via the official
  `google-auth` library and issues its own JWT.
- **Best / Worst surfacing.** Every attempt is scored, a `composite_score` is computed,
  and the attempt is classified `best`, `worst`, or `neutral` against the user's
  rolling distribution. The Android app uses this to mark keepers in the local audio list.
- **Meeting Prep Mode.** Generates BSNL-flavored scenarios (fiber-cut restoration,
  vendor escalation, downtime briefings) for spontaneous speaking practice.
- **Long-term context safe.** When per-user token budget crosses a threshold,
  history is compressed by Gemini and re-cached via Explicit Context Caching.

## Quick start

```bash
cp .env.example .env
# Fill in GEMINI_API_KEY, GOOGLE_OAUTH_CLIENT_ID, JWT_SECRET.

docker compose up -d postgres
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

Or run everything in containers:

```bash
docker compose up --build
```

Open `http://localhost:8000/docs` for the OpenAPI UI.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/google` | Exchange a Google ID token for our JWT. |
| `GET`  | `/auth/me` | Current user. |
| `GET`  | `/curriculum/next` | Today's vocab + practice sentences (and optional meeting seed). |
| `POST` | `/practice/evaluate` | Multipart audio + `target_text` + `client_audio_ref`. |
| `POST` | `/practice/meeting-prep/start` | Open a meeting-prep session. |
| `POST` | `/practice/meeting-prep/turn` | Submit a spoken turn inside a meeting-prep session. |
| `GET`  | `/practice/attempts` | List attempts (filter by `band=best|worst|all`). |
| `GET`  | `/user/progress` | Aggregated stats + AI level-up recommendation. |

## Project layout

```
app/
├── config.py            # Settings (pydantic-settings)
├── db/
│   ├── base.py          # Async engine + SessionLocal
│   └── models.py        # SQLAlchemy 2.x models
├── deps.py              # FastAPI dependencies
├── main.py              # App factory, lifespan, routers
├── prompts/             # Persona, curriculum, evaluation, summarization prompts + schemas
├── routers/             # auth, curriculum, practice, progress
├── schemas/             # Pydantic v2 DTOs
├── security.py          # JWT issuing + Google ID-token verification
├── services/
│   ├── ai_service.py    # Gemini orchestration
│   ├── audio_service.py # In-memory audio packaging (inline vs Files API)
│   ├── cache_service.py # Explicit Context Caching
│   └── progress_service.py
└── utils/
    └── tokens.py
alembic/                 # Migrations
tests/                   # pytest + httpx.AsyncClient
```

## Notes for the Android client

- Record audio in a Gemini-supported MIME: `audio/wav`, `audio/mp4`, `audio/aac`,
  `audio/ogg`, or `audio/mpeg`. WAV at 16 kHz mono is fine and small.
- Generate a stable, opaque `client_audio_ref` per local file (e.g., the file's UUID).
  Send it with every evaluation. The server stores only this string; the audio lives
  in your app-private storage.
- After receiving an evaluation, the response includes `quality_band` (`best` /
  `worst` / `neutral`). Use it to highlight keepers in the local list view.
- Use `GET /practice/attempts?band=best` (or `worst`) to lazily reconcile your local
  audio library with the server's quality verdicts.
