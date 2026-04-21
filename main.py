from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import get_conn, get_whoop_tokens, init_db, save_whoop_tokens
from food_import import parse_netdiary_csv
from recommender import next_meal_target
from whoop_client import WhoopClient, WhoopClientError


WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_DEFAULT_SCOPES = (
    "offline "
    "read:recovery "
    "read:cycles "
    "read:workout "
    "read:sleep "
    "read:profile "
    "read:body_measurement"
)


class ImportResponse(BaseModel):
    inserted_rows: int
    day_detected: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    app.state.oauth_states = set()
    print("Database initialized")
    yield
    # Shutdown (nothing to clean up for sqlite here)
    print("Shutting down...")


app = FastAPI(title="Whoop Meal AI", version="0.1.0", lifespan=lifespan)


@app.get("/")
def home():
    return {"status": "ok", "message": "Whoop Meal AI running"}


def _whoop_config() -> dict:
    client_id = os.getenv("WHOOP_CLIENT_ID", "").strip()
    client_secret = os.getenv("WHOOP_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("WHOOP_REDIRECT_URI", "").strip()
    scope = os.getenv("WHOOP_SCOPE", WHOOP_DEFAULT_SCOPES).strip() or WHOOP_DEFAULT_SCOPES

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail=(
                "WHOOP OAuth is not configured. Set WHOOP_CLIENT_ID, "
                "WHOOP_CLIENT_SECRET, and WHOOP_REDIRECT_URI."
            ),
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }


def _exchange_whoop_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")

    request = Request(
        WHOOP_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WHOOP token exchange failed: {exc}")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="WHOOP returned invalid token JSON") from exc


def _whoop_client_from_storage() -> WhoopClient:
    token_row = get_whoop_tokens()
    access_token = None
    if token_row:
        access_token = token_row.get("access_token")
    if not access_token:
        access_token = os.getenv("WHOOP_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="No WHOOP access token is stored yet. Complete /whoop/login first.",
        )
    return WhoopClient(access_token=access_token)


@app.get("/whoop/login")
def whoop_login():
    config = _whoop_config()
    state = secrets.token_urlsafe(12)[:12]
    app.state.oauth_states.add(state)

    query = urlencode(
        {
            "response_type": "code",
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "scope": config["scope"],
            "state": state,
        }
    )
    auth_url = f"{WHOOP_AUTH_URL}?{query}"

    return {
        "authorization_url": auth_url,
        "redirect_uri": config["redirect_uri"],
        "scope": config["scope"],
        "state": state,
    }


@app.get("/whoop/callback")
def whoop_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=f"WHOOP authorization failed: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required WHOOP callback parameters.")

    if state not in app.state.oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    app.state.oauth_states.discard(state)
    config = _whoop_config()
    tokens = _exchange_whoop_code_for_tokens(
        code=code,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=config["redirect_uri"],
    )

    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail=f"WHOOP token response missing access_token: {tokens}")

    save_whoop_tokens(
        access_token=access_token,
        refresh_token=tokens.get("refresh_token"),
        expires_in=tokens.get("expires_in"),
        scope=tokens.get("scope"),
        token_type=tokens.get("token_type"),
    )

    return JSONResponse(
        {
            "status": "connected",
            "message": "WHOOP authorization completed and tokens were stored locally.",
            "scope": tokens.get("scope"),
            "expires_in": tokens.get("expires_in"),
        }
    )


@app.get("/whoop/status")
def whoop_status():
    token_row = get_whoop_tokens()
    return {
        "configured": all(
            [
                os.getenv("WHOOP_CLIENT_ID", "").strip(),
                os.getenv("WHOOP_CLIENT_SECRET", "").strip(),
                os.getenv("WHOOP_REDIRECT_URI", "").strip(),
            ]
        ),
        "connected": token_row is not None,
        "token": {
            "scope": token_row["scope"],
            "token_type": token_row["token_type"],
            "created_at": token_row["created_at"],
        }
        if token_row
        else None,
    }


@app.get("/whoop/profile")
def whoop_profile():
    client = _whoop_client_from_storage()
    try:
        profile = client.get_user_profile()
    except WhoopClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return profile.model_dump()


@app.get("/whoop/recovery/current")
def whoop_recovery_current():
    client = _whoop_client_from_storage()
    try:
        recovery = client.get_current_recovery()
    except WhoopClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if recovery is None:
        return {"status": "no_recovery_available"}
    return recovery.model_dump()


@app.post("/import/netdiary", response_model=ImportResponse)
async def import_netdiary(
    file: UploadFile = File(...),
    day: Optional[str] = Query(
        default=None,
        description="Override day YYYY-MM-DD if CSV has no date column",
    ),
):
    # basic file validation
    filename = (file.filename or "").lower()
    if not (filename.endswith(".csv") or file.content_type in ("text/csv", "application/vnd.ms-excel")):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")

    raw = await file.read()

    # parse CSV -> list of dict rows
    try:
        rows, day_detected = parse_netdiary_csv(raw, day_override=day)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {e}")

    if not rows:
        return {"inserted_rows": 0, "day_detected": day_detected or (day or "")}

    # insert into sqlite
    conn = get_conn()
    cur = conn.cursor()

    inserted = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO food_logs (source, eaten_at, day, item_name, calories, protein_g, carbs_g, fat_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r.get("source", "netdiary"),
                r.get("eaten_at"),
                r.get("day"),
                r.get("item_name"),
                r.get("calories"),
                r.get("protein_g"),
                r.get("carbs_g"),
                r.get("fat_g"),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()

    return {"inserted_rows": inserted, "day_detected": day_detected or (day or "")}


@app.get("/summary/day")
def summary_day(
    day: str = Query(..., description="YYYY-MM-DD"),
    calories: float = Query(2000, ge=0),
    protein_g: float = Query(160, ge=0),
    carbs_g: float = Query(180, ge=0),
    fat_g: float = Query(70, ge=0),
):
    """
    Summarize consumed macros for a given day and return a next-meal target.
    Goals are passed as query params (no GET body).
    """
    conn = get_conn()
    cur = conn.cursor()

    row = cur.execute(
        """
        SELECT
            COALESCE(SUM(calories), 0),
            COALESCE(SUM(protein_g), 0),
            COALESCE(SUM(carbs_g), 0),
            COALESCE(SUM(fat_g), 0)
        FROM food_logs
        WHERE day=?
        """,
        (day,),
    ).fetchone()

    conn.close()

    consumed = {
        "calories": float(row[0] or 0),
        "protein_g": float(row[1] or 0),
        "carbs_g": float(row[2] or 0),
        "fat_g": float(row[3] or 0),
    }

    goals = {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }

    remaining = {k: max(goals[k] - consumed[k], 0) for k in goals}

    # recommender returns suggested next meal macros
    next_meal = next_meal_target(consumed, goals)

    return {
        "day": day,
        "consumed": consumed,
        "goals": goals,
        "remaining": remaining,
        "next_meal_target": next_meal,
    }

