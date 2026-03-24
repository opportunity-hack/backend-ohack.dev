# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- Install dependencies: `pip install -r requirements.txt`
- Run the app: `flask run` (or `gunicorn api.wsgi:app --log-file=- --log-level debug --preload --workers 1`)
- Run tests: `pytest`
- Run a single test: `pytest path/to/test_file.py::test_function_name`
- Run linting: `pylint api/ common/ db/ model/ services/`
- Python environment: Miniconda with `conda activate py39_ohack_backend` (Python 3.9.13)

## Architecture

### Flask app with Blueprint-based API modules
The app is created via `api/__init__.py:create_app()`. Each API domain is a Blueprint registered there. The WSGI entrypoint is `api/wsgi.py`.

### API module pattern
Each API domain lives in `api/<domain>/` with a consistent structure:
- `<domain>_views.py` — Flask Blueprint with route definitions. Routes use `@cross_origin()` and PropelAuth's `@auth.require_user` for authentication.
- `<domain>_service.py` — Business logic called by views. Some older services live in `services/` at the top level (hearts, users, volunteers, etc.).
- `tests/` — Tests for that domain.

API domains: messages, certificates, contact, github, hearts, judging, leaderboard, llm, newsletters, problemstatements, slack, store, teams, users, validate, volunteers.

### Database layer (`db/`)
- `db/interface.py` — Abstract `DatabaseInterface` base class.
- `db/firestore.py` — Production implementation using Firebase Firestore.
- `db/mem.py` — In-memory implementation (enabled via `IN_MEMORY_DATABASE=True` env var).
- `db/db.py` — Singleton that selects the implementation and re-exports all DB operations as module-level functions. All service code imports from `db.db`.

### Models (`model/`)
Data classes (User, Hackathon, Nonprofit, ProblemStatement, JudgeAssignment, JudgeScore, JudgePanel, etc.) with `serialize()`/`deserialize()` methods for Firestore document conversion.

### Auth
PropelAuth via `common/auth.py`. Routes get the authenticated user from `@auth.require_user` and org context from the `X-Org-Id` header.

### Caching
`common/utils/redis_cache.py` — Redis with TTLCache fallback. Used via `@cached_with_key()` decorator.

### Logging
`common/log.py` — Structured JSON logging with `get_logger()`, `info()`, `debug()`, `warning()`, `error()`, `exception()` helpers. Supports colored console output.

### Key environment variables
`FLASK_APP=api`, `FLASK_RUN_PORT=6060`, `CLIENT_ORIGIN_URL`, `FIREBASE_CERT_CONFIG`, `OPENAI_API_KEY`, `PROPEL_AUTH_KEY`, `PROPEL_AUTH_URL`, `REDIS_URL` (optional), `IN_MEMORY_DATABASE` (optional), `ENVIRONMENT=test` (for MockFirestore).

## Deployment
Deployed to Fly.io (`fly.toml`, app: `backend-ohack`, region: `sjc`). Uses gunicorn (`Procfile`). Port 6060.

## Testing notes
- Tests live in `api/<domain>/tests/` or `test/` at the repo root.
- The app has heavy external dependencies (Firestore, OpenAI, PropelAuth, Slack, PIL). Tests must pre-mock these modules in `sys.modules` before importing service code.
- `ENVIRONMENT=test` enables MockFirestore in the DB layer.

## Code Style Guidelines
- Python 3.9.13 (Flask backend)
- Imports: Group standard library, then third-party, then local imports
- Types: Use type hints for function parameters and return values
- Naming: snake_case for variables/functions, PascalCase for classes
- Error handling: try/except with specific exceptions
- Linting: pylint (`.pylintrc` disables missing-module-docstring, missing-function-docstring, too-few-public-methods)
