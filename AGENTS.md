# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Project Overview

YouDub WebUI is a video localization tool that converts YouTube/Bilibili videos into dubbed versions in another language. Core mature scenario: YouTube English -> Chinese dubbing; also supports Bilibili Chinese -> English dubbing.

## Commands

### Backend (Python 3.12, FastAPI)

```powershell
# Run backend tests (Windows PowerShell)
.\.venv\Scripts\pytest.exe backend/tests

# Run a single test file
.\.venv\Scripts\pytest.exe backend/tests/test_pipeline.py

# Run a single test by name
.\.venv\Scripts\pytest.exe backend/tests/test_pipeline.py -k "test_name"

# Start backend dev server
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend tests use lightweight CPU-only dependencies (`backend/requirements-test.txt`); heavy ML packages (torch, whisper, demucs, voxcpm, librosa, audiostretchy) are mocked or lazily imported. The CI enforces that these heavy dependencies are absent in the test environment.

### Frontend (Next.js 16, React 19, TypeScript)

```powershell
# Install dependencies
npm --prefix apps/web install

# Run frontend tests (vitest + jsdom)
npm --prefix apps/web run test

# Lint
npm --prefix apps/web run lint

# TypeScript check
npx --prefix apps/web tsc --noEmit

# Build
npm --prefix apps/web run build

# Dev server
npm --prefix apps/web run dev -- --hostname 0.0.0.0 --port 3000
```

Frontend proxies `/api/*` to backend at `http://127.0.0.1:8000` via Next.js rewrites (see `apps/web/next.config.ts`). Override with `NEXT_SERVER_API_BASE_URL` env var.

## Architecture

### Backend (`backend/app/`)

- **`main.py`**: FastAPI app with all route handlers. All API routes are prefixed with `/api/`. Auth middleware (`auth.py`) enforces session cookie + CSRF token on all non-public routes.
- **`config.py`**: Loads `.env` from repo root at import time via `python-dotenv`. Defines `WORKFOLDER`, `DB_PATH`, `MODEL_CACHE_DIR` and other runtime paths. Module import order matters — `config` is imported before other modules to set up environment.
- **`database.py`**: Raw SQLite via `sqlite3` (no ORM). Schema includes `tasks`, `task_stages`, `openai_settings`, `ytdlp_settings`, `auth_sessions`. All DB access goes through module-level functions (not a class).
- **`worker.py`**: Single-thread FIFO background worker using `threading.Thread` + `queue.Queue`. Tasks are processed one at a time. Started during FastAPI lifespan; re-enqueues any `queued` tasks on startup.
- **`pipeline.py`**: `PipelineRunner` orchestrates 9 sequential stages. Each stage is a method on the runner. Stages can be cached (skipped if already `succeeded`). Supports `auto` mode (run all) and `manual` mode (pause after each stage, resume via `/api/tasks/{id}/continue`).
- **`stages.py`**: Defines the ordered `STAGES` tuple: `download → separate → asr → asr_fix → translate → split_audio → tts → merge_audio → merge_video`.
- **`sources.py`**: `SourceConfig` maps URL patterns (youtube/bilibili/local) to ASR language, target language, proxy usage, and cookie file. `detect_source(url)` is the entry point.
- **`auth.py`**: Argon2id password hashing via `pwdlib`. HttpOnly session cookie + per-session CSRF token in `X-CSRF-Token` header. Login rate limiting. Sessions stored in SQLite.

### Pipeline Adapters (`backend/app/adapters/`)

Each adapter wraps an external tool or model. Adapters are imported lazily inside pipeline stage methods (not at module level) to keep test imports lightweight.

| Adapter | Purpose |
|---|---|
| `ytdlp.py` | Download video via yt-dlp |
| `demucs.py` | Separate vocals from BGM (Demucs submodule) |
| `whisper_asr.py` | Speech recognition with word-level timestamps |
| `asr_sentence_fixer.py` | Re-segment ASR output into sentences |
| `openai_translate.py` | Translate via OpenAI-compatible Chat Completions API (concurrent) |
| `voxcpm.py` | TTS via VoxCPM2 (ModelScope) |
| `audio.py` | Split reference vocals; merge TTS with BGM |
| `ffmpeg.py` | Burn subtitles and produce final mp4 |
| `local_video.py` | Import locally uploaded video files |
| `local_subtitles.py` | Parse uploaded `.srt` subtitles (skip Whisper+Translate) |

### Frontend (`apps/web/src/`)

- **App Router** with two pages: home (`page.tsx`) for task list/creation, task detail (`tasks/[id]/page.tsx`).
- **`lib/api.ts`**: Single API client module. All backend calls go through `request<T>()` which handles CSRF tokens, credentials, and error parsing. `ApiError` class carries HTTP status.
- **`lib/auth.tsx`**: `AuthProvider` context manages login state, listens for 401 events.
- **`lib/i18n.tsx`**: `LanguageProvider` for i18n (Chinese/English).
- **`components/settings-dialog.tsx`**: Settings UI for YouTube cookie, OpenAI config, yt-dlp proxy.
- **`components/ui/`**: shadcn/ui components (do not edit generated files directly; use `npx shadcn@latest add` to add new components).

### Data Flow

```
URL/upload → create_task → worker.enqueue → PipelineRunner.run()
  → 9 stages executed sequentially in background thread
  → each stage updates task_stages in SQLite
  → frontend polls task status via GET /api/tasks/{id}
```

### Key Conventions

- All API routes use `/api/` prefix. Next.js rewrites proxy `/api/*` to backend.
- Runtime environment is loaded from `.env` at `config.py` import time; do not call `load_dotenv` elsewhere.
- Pipeline artifacts are stored under `{WORKFOLDER}/{task_id}/` with fixed subdirectory structure: `media/`, `metadata/`, `segments/`, `tmp/`.
- Demucs is a git submodule at `submodule/demucs/`; always run `git submodule update --init --recursive` after clone.
- POSIX file permissions (`umask 0077`) are enforced on Linux/macOS via `runtime_security.py`. Windows relies on NTFS ACLs configured by the admin.
- Login validation errors are redacted to prevent user enumeration (see `redact_login_validation_error` in `main.py`).
- When adding a new pipeline stage: add to `STAGES` tuple in `stages.py`, add handler method in `PipelineRunner._stage_handlers`, add cached restore logic in `_restore_cached_stage`, and update `remove_stage_artifacts` in `stage_reset.py`.
