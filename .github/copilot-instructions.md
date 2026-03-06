# Copilot Instructions for backend-ohack.dev

## Project Overview
This is the Python/Flask backend API for [Opportunity Hack](https://www.ohack.dev) — a nonprofit hackathon platform. It serves the frontend at [ohack.dev](https://www.ohack.dev) and is accessible at [api.ohack.dev](https://api.ohack.dev).

## Tech Stack
- **Language**: Python 3.9.13
- **Framework**: Flask
- **Database**: Google Cloud Firestore (Firebase)
- **Authentication**: PropelAuth (`propelauth_flask`)
- **Caching**: Redis (optional locally)
- **Deployment**: Fly.io
- **Testing**: pytest
- **Linting**: pylint

## Project Structure
```
api/                  # Flask blueprints (one subdirectory per feature)
  certificates/       # Certificate generation
  contact/            # Contact form
  github/             # GitHub integration
  hearts/             # Kudos/hearts feature
  judging/            # Hackathon judging
  leaderboard/        # Leaderboard endpoints
  llm/                # LLM/AI features
  messages/           # Messaging endpoints
  newsletters/        # Newsletter management
  problemstatements/  # Nonprofit problem statements
  slack/              # Slack integration
  teams/              # Team management
  users/              # User profile endpoints
  validate/           # Validation endpoints
  volunteers/         # Volunteer management
  __init__.py         # App factory (create_app)
common/               # Shared utilities
  auth.py             # PropelAuth setup and helpers
  exceptions.py       # Custom exception classes
  utils/              # Utility helpers
db/                   # Database layer
  db.py               # Main DB module
  firestore.py        # Firestore client wrapper
  interface.py        # DB interface/abstraction
  mem.py              # In-memory caching
model/                # Data models (dataclasses)
services/             # Business logic services
test/                 # Integration / service tests
```

## Development Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (development)
flask run

# Run tests
pytest

# Run a single test
pytest path/to/test_file.py::test_function_name

# Run linter
pylint api/ common/ db/ model/ services/
pylint -E api/*.py   # errors only (used in CI)
```

## Environment Setup
Copy `.env.example` to `.env` and fill in real values (obtain secrets from the team Slack channel):
```bash
cp .env.example .env
```

Key environment variables:
- `CLIENT_ORIGIN_URL` — allowed CORS origins (comma-separated or `*` for dev)
- `FIREBASE_CERT_CONFIG` — Firebase service account JSON
- `PROPEL_AUTH_KEY` / `PROPEL_AUTH_URL` — PropelAuth credentials
- `OPENAI_API_KEY` — OpenAI key for LLM features
- `SLACK_BOT_TOKEN` / `SLACK_WEBHOOK` — Slack integration
- `ENC_DEC_KEY` — encryption/decryption key
- `REDIS_URL` — Redis URL (optional for local development)

## Code Style Guidelines
- **Python version**: 3.9.13
- **Naming**: `snake_case` for variables and functions; `PascalCase` for classes
- **Imports**: standard library → third-party → local (one blank line between groups)
- **Type hints**: use `from typing import *` and annotate function parameters and return types
- **Error handling**: use `try/except` with specific exception types; avoid bare `except`
- **Docstrings**: recommended for complex logic, not required for every function
- **Linting**: pylint with `.pylintrc` disabling `missing-module-docstring` (C0114), `missing-function-docstring` (C0116), and `too-few-public-methods` (R0903)

## Architecture Patterns
- **App factory**: `api/__init__.py` exports `create_app()` which registers all Flask blueprints
- **Blueprints**: each feature lives in its own subdirectory under `api/` and registers a Flask `Blueprint`
- **Service layer**: business logic lives in `services/` or in `*_service.py` files inside the blueprint directory, not in view files
- **DB abstraction**: all Firestore access goes through `db/` — do not call Firestore directly from views
- **Auth**: use `common/auth.py`'s `auth` object and `auth_user` / `getOrgId()` helpers for authentication; do not import PropelAuth directly in views
- **Utilities**: use `common/utils/safe_get_env_var()` to read environment variables (never `os.environ[]` directly in business logic)

## Testing Guidelines
- Tests live alongside the code they test inside a `tests/` subdirectory of each blueprint, or in the top-level `test/` directory for integration tests
- Use `pytest` fixtures defined in `conftest.py`
- Mock external services (Firestore, PropelAuth, Slack, OpenAI) in unit tests
- Run the full suite with `pytest` before opening a PR

## CI / CD
- GitHub Actions workflow (`.github/workflows/main.yml`) runs pylint and tests on every push and PR
- Merges to `develop` deploy to the staging environment; merges to `main` deploy to production via Fly.io
