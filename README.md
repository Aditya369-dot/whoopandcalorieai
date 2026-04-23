# Whoop Meal AI

Whoop Meal AI is a local-first performance nutrition app that combines:

- WHOOP recovery, sleep, HRV, RHR, and strain context
- MyNetDiary food intake exports
- insulin-resistance-aware meal guidance
- a morning performance brief built from yesterday's intake plus today's readiness

## What The App Does

The app is meant to answer four daily questions:

- How much have I eaten so far?
- How should I bias food choices if I am managing insulin resistance?
- What should my meals and training focus look like today based on WHOOP readiness and recent intake?
- Is my intake trending in a direction that supports better recovery and body-composition goals?

The current MVP focuses on daily execution:

- importing MyNetDiary CSV or daily PDF reports
- storing nutrition data locally in SQLite
- connecting WHOOP through OAuth
- generating a morning performance brief and next-meal guidance
- showing whether nutrition input is fresh, stale, or missing

## Current MVP Flow

1. A MyNetDiary daily PDF or CSV lands locally in `Downloads`
2. A scheduled local import task tries to ingest yesterday's report
3. WHOOP provides daily readiness metrics
4. Streamlit shows:
   - nutrition import status
   - WHOOP daily metrics
   - a morning performance brief
   - next-meal guidance

## Data Sources

Current sources:

- WHOOP OAuth + day metrics
- MyNetDiary yearly CSV exports
- MyNetDiary daily PDF summaries

Planned sources:

- lab CSV / PDF ingestion
- weight or body-composition trend inputs
- historical daily context for smarter reasoning

## Storage

The local SQLite database is stored at:

- `C:\Users\<you>\AppData\Local\WhoopMealAI\app.db`

This avoids SQLite write issues caused by keeping the database inside OneDrive-synced folders.

## Local Run

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Frontend:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8501
```

## Local URLs

- FastAPI docs: `http://127.0.0.1:8000/docs`
- Streamlit app: `http://localhost:8501`

## WHOOP Local Env Vars

Set these in the same shell that starts FastAPI:

```powershell
$env:WHOOP_CLIENT_ID="your_client_id"
$env:WHOOP_CLIENT_SECRET="your_client_secret"
$env:WHOOP_REDIRECT_URI="http://127.0.0.1:8000/whoop/callback"
```

OAuth flow:

1. Start FastAPI
2. Open `http://127.0.0.1:8000/whoop/connect` or use the connect/reconnect link inside Streamlit
3. Approve access in WHOOP
4. Verify `http://127.0.0.1:8000/whoop/status`

Notes:

- WHOOP may return only a short-lived access token
- if no refresh token is provided, reconnect may be required later
- the Streamlit dashboard now surfaces this clearly and provides reconnect links

## MyNetDiary Automation

Daily food import is currently zero-cost and local-first:

- the app watches for the newest `MyNetDiary*.csv` or `MyNetDiary*.pdf` file in `Downloads`
- a scheduled Windows task runs every morning
- the importer only accepts the report if it matches the expected target day
- stale files are skipped instead of silently reusing old nutrition data

Manual runner:

```powershell
.\.venv\Scripts\python.exe auto_import_mynetdiary.py --search-dir "$env:USERPROFILE\Downloads"
```

Scheduled runner files in this repo:

- `daily_import_mynetdiary.ps1`
- `daily_import_mynetdiary.cmd`

## Deployment Plan

Recommended production split:

- `api.snapbiz.ai` -> FastAPI backend
- `app.snapbiz.ai` -> Streamlit frontend

WHOOP app settings:

- Privacy Policy URL: `https://app.snapbiz.ai/?page=Privacy+Policy`
- Redirect URL: `https://api.snapbiz.ai/whoop/callback`

## Render

This repo includes `render.yaml` that defines:

- `whoopmealai-api` for FastAPI
- `whoopmealai-app` for Streamlit

Set these Render env vars on the API service:

- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_REDIRECT_URI=https://api.snapbiz.ai/whoop/callback`

Set this Render env var on the Streamlit service:

- `API_BASE_URL=https://api.snapbiz.ai`

## Current Product Scope

- MyNetDiary CSV and PDF ingestion
- automated daily import task
- daily macro summaries
- nutrition freshness / import status tracking
- WHOOP day metrics and reconnect UX
- morning performance brief
- deterministic next-meal recommendation

## Near-Term Roadmap

- graceful daily operation when WHOOP is unavailable
- richer lab ingestion and normalization
- daily context modeling across intake + WHOOP + labs
- trend analysis over recovery, intake, and weight
- later: LLM-generated analyst-style reasoning on top of the structured daily context
