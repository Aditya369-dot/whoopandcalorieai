from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from db import (
    delete_whoop_oauth_state,
    get_conn,
    get_whoop_tokens,
    init_db,
    save_whoop_oauth_state,
    save_whoop_tokens,
)
from food_import import parse_netdiary_csv
from recommender import build_daily_brief, next_meal_target, recommend_next_meal
from whoop_client import WhoopClient, WhoopClientError


WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_DEFAULT_SCOPES = (
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


class ImportRowsRequest(BaseModel):
    rows: list[dict]
    day: str
    source_kind: Optional[str] = None
    source_path: Optional[str] = None


def _load_day_consumed(day: str) -> dict:
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

    return {
        "calories": float(row[0] or 0),
        "protein_g": float(row[1] or 0),
        "carbs_g": float(row[2] or 0),
        "fat_g": float(row[3] or 0),
    }


def _load_whoop_snapshot(day: str) -> tuple[Optional[dict], Optional[str]]:
    try:
        client = _whoop_client_from_storage()
        snapshot = client.get_daily_snapshot(day)
        return snapshot.model_dump(), None
    except HTTPException as exc:
        if exc.status_code in (400, 401):
            return None, exc.detail
        raise
    except WhoopClientError as exc:
        return None, str(exc)


def _summarize_whoop_day(snapshot: dict) -> dict:
    cycle_score = ((snapshot.get("cycle") or {}).get("score") or {}) if isinstance(snapshot, dict) else {}
    recovery_score = (((snapshot.get("recovery") or {}).get("score") or {}) if isinstance(snapshot, dict) else {})
    sleep_score = (((snapshot.get("sleep") or {}).get("score") or {}) if isinstance(snapshot, dict) else {})
    sleep_stage = (sleep_score.get("stage_summary") or {}) if isinstance(sleep_score, dict) else {}

    total_sleep_milli = (
        float(sleep_stage.get("total_light_sleep_time_milli") or 0)
        + float(sleep_stage.get("total_slow_wave_sleep_time_milli") or 0)
        + float(sleep_stage.get("total_rem_sleep_time_milli") or 0)
    )

    return {
        "strain": cycle_score.get("strain"),
        "recovery": recovery_score.get("recovery_score"),
        "sleep_performance": sleep_score.get("sleep_performance_percentage"),
        "sleep_hours": round(total_sleep_milli / 3600000.0, 2) if total_sleep_milli else None,
        "hrv_rmssd_milli": recovery_score.get("hrv_rmssd_milli"),
        "resting_heart_rate": recovery_score.get("resting_heart_rate"),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
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
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            # WHOOP's edge occasionally rejects bare default urllib signatures.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except Exception as exc:
        detail = str(exc)
        body = ""
        if hasattr(exc, "read"):
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
        message = f"WHOOP token exchange failed: {detail}"
        if body:
            message = f"{message} | response: {body}"
        raise HTTPException(status_code=502, detail=message)

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="WHOOP returned invalid token JSON") from exc


def _refresh_whoop_tokens(*, refresh_token: str, client_id: str, client_secret: str) -> dict:
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")

    request = Request(
        WHOOP_TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except Exception as exc:
        detail = str(exc)
        body = ""
        if hasattr(exc, "read"):
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
        message = f"WHOOP token refresh failed: {detail}"
        if body:
            message = f"{message} | response: {body}"
        raise HTTPException(status_code=502, detail=message)

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="WHOOP returned invalid refresh JSON") from exc


def _token_expires_at(token_row: dict) -> Optional[datetime]:
    created_at = token_row.get("created_at")
    expires_in = token_row.get("expires_in")
    if not created_at or not expires_in:
        return None
    try:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return created + timedelta(seconds=int(expires_in))
    except Exception:
        return None


def _whoop_client_from_storage() -> WhoopClient:
    token_row = get_whoop_tokens()
    access_token = None
    if token_row:
        expires_at = _token_expires_at(token_row)
        if expires_at and datetime.now(timezone.utc) >= expires_at:
            refresh_token = token_row.get("refresh_token")
            if refresh_token:
                config = _whoop_config()
                refreshed = _refresh_whoop_tokens(
                    refresh_token=refresh_token,
                    client_id=config["client_id"],
                    client_secret=config["client_secret"],
                )
                access_token = refreshed.get("access_token")
                if access_token:
                    save_whoop_tokens(
                        access_token=access_token,
                        refresh_token=refreshed.get("refresh_token") or refresh_token,
                        expires_in=refreshed.get("expires_in"),
                        scope=refreshed.get("scope"),
                        token_type=refreshed.get("token_type"),
                    )
            else:
                raise HTTPException(
                    status_code=401,
                    detail=(
                        "Stored WHOOP token has expired and no refresh token is available. "
                        "Reconnect via /whoop/connect."
                    ),
                )

        if not access_token:
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
    state = secrets.token_urlsafe(8)[:8]
    save_whoop_oauth_state(state)

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


@app.get("/whoop/connect")
def whoop_connect():
    payload = whoop_login()
    return RedirectResponse(url=payload["authorization_url"], status_code=307)


@app.get("/whoop/callback")
def whoop_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=f"WHOOP authorization failed: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required WHOOP callback parameters.")

    if not delete_whoop_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
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
    expires_at = _token_expires_at(token_row) if token_row else None
    expired = bool(expires_at and datetime.now(timezone.utc) >= expires_at)
    return {
        "configured": all(
            [
                os.getenv("WHOOP_CLIENT_ID", "").strip(),
                os.getenv("WHOOP_CLIENT_SECRET", "").strip(),
                os.getenv("WHOOP_REDIRECT_URI", "").strip(),
            ]
        ),
        "connected": token_row is not None,
        "expired": expired,
        "reauthorize_required": bool(token_row and expired and not token_row.get("refresh_token")),
        "token": {
            "scope": token_row["scope"],
            "token_type": token_row["token_type"],
            "created_at": token_row["created_at"],
            "expires_in": token_row["expires_in"],
            "expires_at": expires_at.isoformat() if expires_at else None,
            "has_refresh_token": bool(token_row.get("refresh_token")),
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


@app.get("/whoop/day")
def whoop_day(day: Optional[str] = Query(default=None, description="YYYY-MM-DD")):
    client = _whoop_client_from_storage()
    try:
        snapshot = client.get_daily_snapshot(day)
    except WhoopClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    snapshot_data = snapshot.model_dump()
    return {
        "date": snapshot_data.get("date") or day,
        "metrics": _summarize_whoop_day(snapshot_data),
        "snapshot": snapshot_data,
    }


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


@app.post("/import/netdiary/rows", response_model=ImportResponse)
def import_netdiary_rows(payload: ImportRowsRequest):
    if not payload.rows:
        raise HTTPException(status_code=400, detail="No parsed rows were provided.")

    target_day = payload.day
    normalized_rows = []
    for row in payload.rows:
        next_row = dict(row)
        next_row["day"] = target_day
        next_row["source"] = "netdiary_manual"
        normalized_rows.append(next_row)

    inserted = 0
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM food_logs WHERE day=? AND source=?", (target_day, "netdiary_manual"))
    for row in normalized_rows:
        cur.execute(
            """
            INSERT INTO food_logs (source, eaten_at, day, item_name, calories, protein_g, carbs_g, fat_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("source", "netdiary_manual"),
                row.get("eaten_at"),
                row.get("day"),
                row.get("item_name"),
                row.get("calories"),
                row.get("protein_g"),
                row.get("carbs_g"),
                row.get("fat_g"),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()

    from db import save_import_status

    save_import_status(
        import_name="mynetdiary_auto",
        status="success",
        target_day=target_day,
        detected_day=target_day,
        source_path=payload.source_path or "manual_upload",
        source_kind=payload.source_kind or "upload",
        rows_found=len(normalized_rows),
        rows_inserted=inserted,
        message="Imported MyNetDiary data from Streamlit upload.",
        succeeded=True,
    )

    return {"inserted_rows": inserted, "day_detected": target_day}


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
    consumed = _load_day_consumed(day)

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


@app.get("/recommendation/day")
def recommendation_day(
    day: str = Query(..., description="YYYY-MM-DD"),
    calories: float = Query(2000, ge=0),
    protein_g: float = Query(160, ge=0),
    carbs_g: float = Query(180, ge=0),
    fat_g: float = Query(70, ge=0),
    insulin_resistant: bool = Query(False),
    include_whoop: bool = Query(True),
):
    consumed = _load_day_consumed(day)
    goals = {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }

    whoop_snapshot = None
    whoop_warning = None
    if include_whoop:
        whoop_snapshot, whoop_warning = _load_whoop_snapshot(day)

    recommendation = recommend_next_meal(
        consumed,
        goals,
        whoop_snapshot=whoop_snapshot,
        insulin_resistant=insulin_resistant,
    )

    return {
        "day": day,
        "consumed": consumed,
        "goals": goals,
        "insulin_resistant": insulin_resistant,
        "whoop_snapshot_available": whoop_snapshot is not None,
        "whoop_warning": whoop_warning,
        **recommendation,
    }


@app.get("/brief/day")
def brief_day(
    day: str = Query(..., description="YYYY-MM-DD"),
    calories: float = Query(2000, ge=0),
    protein_g: float = Query(160, ge=0),
    carbs_g: float = Query(180, ge=0),
    fat_g: float = Query(70, ge=0),
    insulin_resistant: bool = Query(False),
    include_whoop: bool = Query(True),
):
    consumed_today = _load_day_consumed(day)
    goals = {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }

    whoop_snapshot = None
    whoop_warning = None
    if include_whoop:
        whoop_snapshot, whoop_warning = _load_whoop_snapshot(day)

    try:
        previous_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Day must be YYYY-MM-DD.")

    yesterday_consumed = _load_day_consumed(previous_day)
    brief = build_daily_brief(
        day=day,
        consumed_today=consumed_today,
        goals=goals,
        yesterday_consumed=yesterday_consumed,
        whoop_snapshot=whoop_snapshot,
        insulin_resistant=insulin_resistant,
    )

    return {
        "whoop_snapshot_available": whoop_snapshot is not None,
        "whoop_warning": whoop_warning,
        **brief,
    }

