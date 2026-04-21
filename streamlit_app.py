from __future__ import annotations

from datetime import date
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import streamlit as st

from db import get_conn, init_db
from food_import import parse_netdiary_csv
from recommender import next_meal_target
from whoop_client import WhoopClient, WhoopClientError


API_BASE_URL = "http://127.0.0.1:8000"


def load_day_summary(day: str) -> Dict[str, float]:
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


def insert_food_rows(rows: list[dict]) -> int:
    conn = get_conn()
    cur = conn.cursor()

    inserted = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO food_logs (source, eaten_at, day, item_name, calories, protein_g, carbs_g, fat_g)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("source", "netdiary"),
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
    return inserted


def fetch_api_json(path: str, query: dict | None = None) -> dict:
    url = f"{API_BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    try:
        with urlopen(url, timeout=20) as response:
            return __import__("json").loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Unable to reach backend API: {exc.reason}") from exc


def render_privacy_policy() -> None:
    st.title("Privacy Policy")
    st.caption("Last updated: April 20, 2026")

    st.markdown(
        """
This app is a personal nutrition and recovery tool. It helps the user combine food-log data,
wearable data, and optional health inputs to generate meal recommendations.

Information this app may process:
- nutrition and meal-log uploads
- WHOOP data the user explicitly authorizes
- optional health profile or lab data the user chooses to enter

How the data is used:
- to calculate daily calorie and macro summaries
- to evaluate recovery and training context
- to generate personalized meal recommendations and explanations

Data sharing:
- this app is not intended to sell personal data
- authorized WHOOP data is used only to support the app's functionality

User control:
- the user chooses what files to upload and what integrations to connect
- local development versions of the app may store data in a local SQLite database

Contact:
- replace this section with your preferred contact email before publishing
        """
    )


def render_whoop_status() -> None:
    st.subheader("WHOOP Connection")
    st.caption("This checks whether a WHOOP access token is available locally.")

    try:
        client = WhoopClient.from_env()
        profile = client.get_user_profile()
    except ValueError:
        st.info("No WHOOP token found yet. Set `WHOOP_ACCESS_TOKEN` to enable live WHOOP data.")
        return
    except WhoopClientError as exc:
        st.warning(f"WHOOP credentials were found, but the API call failed: {exc}")
        return

    st.success(f"Connected to WHOOP as user `{profile.user_id}`.")

    try:
        recovery = client.get_current_recovery()
    except WhoopClientError as exc:
        st.warning(f"Connected, but current recovery could not be fetched: {exc}")
        return

    if not recovery or not recovery.score:
        st.info("WHOOP connection works, but no scored recovery is available yet.")
        return

    cols = st.columns(3)
    cols[0].metric("Recovery", f"{recovery.score.recovery_score or 0}%")
    cols[1].metric("HRV", f"{recovery.score.hrv_rmssd_milli or 0:.1f}")
    cols[2].metric("RHR", f"{recovery.score.resting_heart_rate or 0} bpm")


def render_whoop_day_overview(day_str: str) -> None:
    st.subheader("WHOOP Day")
    st.caption("Core WHOOP metrics for the selected day.")

    try:
        payload = fetch_api_json("/whoop/day", {"day": day_str})
    except RuntimeError as exc:
        st.info(f"WHOOP day metrics are not available yet. {exc}")
        return

    metrics = payload.get("metrics") or {}
    cols = st.columns(3)
    cols[0].metric("Strain", f"{float(metrics.get('strain') or 0):.1f}")
    cols[1].metric("Recovery", f"{int(metrics.get('recovery') or 0)}%")
    cols[2].metric("Sleep", f"{int(metrics.get('sleep_performance') or 0)}%")

    extra = st.columns(3)
    extra[0].metric("Hours Slept", f"{float(metrics.get('sleep_hours') or 0):.1f} h")
    extra[1].metric("HRV", f"{float(metrics.get('hrv_rmssd_milli') or 0):.1f}")
    extra[2].metric("RHR", f"{int(metrics.get('resting_heart_rate') or 0)} bpm")


def render_dashboard() -> None:
    st.title("Whoop Meal AI")
    st.caption("Recovery-aware meal guidance from food logs and wearable context.")

    with st.sidebar:
        st.header("Daily Targets")
        target_day = st.date_input("Day", value=date.today())
        calories = st.number_input("Calories", min_value=0.0, value=2000.0, step=50.0)
        protein_g = st.number_input("Protein (g)", min_value=0.0, value=160.0, step=5.0)
        carbs_g = st.number_input("Carbs (g)", min_value=0.0, value=180.0, step=5.0)
        fat_g = st.number_input("Fat (g)", min_value=0.0, value=70.0, step=5.0)
        insulin_resistant = st.checkbox("Insulin resistant", value=False)
        include_whoop = st.checkbox("Use WHOOP context when available", value=True)

    st.subheader("Upload NetDiary CSV")
    uploaded = st.file_uploader("Choose a NetDiary export", type=["csv"])
    override_day = st.checkbox("Override imported day with selected day", value=False)

    if uploaded is not None and st.button("Import Food Log"):
        raw = uploaded.read()
        try:
            rows, day_detected = parse_netdiary_csv(
                raw,
                day_override=target_day.isoformat() if override_day else None,
            )
            inserted = insert_food_rows(rows)
        except Exception as exc:
            st.error(f"Import failed: {exc}")
        else:
            st.success(f"Imported {inserted} rows for {day_detected or target_day.isoformat()}.")

    day_str = target_day.isoformat()
    render_whoop_day_overview(day_str)

    consumed = load_day_summary(day_str)
    goals = {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }
    remaining = {key: max(goals[key] - consumed[key], 0.0) for key in goals}

    recommendation = None
    recommendation_error = None
    try:
        recommendation = fetch_api_json(
            "/recommendation/day",
            {
                "day": day_str,
                "calories": calories,
                "protein_g": protein_g,
                "carbs_g": carbs_g,
                "fat_g": fat_g,
                "insulin_resistant": str(insulin_resistant).lower(),
                "include_whoop": str(include_whoop).lower(),
            },
        )
    except RuntimeError as exc:
        recommendation_error = str(exc)

    next_meal = (
        recommendation.get("next_meal_target")
        if isinstance(recommendation, dict) and recommendation.get("next_meal_target")
        else next_meal_target(consumed, goals)
    )

    st.subheader("Daily Summary")
    top = st.columns(4)
    top[0].metric("Calories", f"{consumed['calories']:.0f}", f"{remaining['calories']:.0f} left")
    top[1].metric("Protein", f"{consumed['protein_g']:.0f} g", f"{remaining['protein_g']:.0f} g left")
    top[2].metric("Carbs", f"{consumed['carbs_g']:.0f} g", f"{remaining['carbs_g']:.0f} g left")
    top[3].metric("Fat", f"{consumed['fat_g']:.0f} g", f"{remaining['fat_g']:.0f} g left")

    st.subheader("Next Meal Target")
    meal_cols = st.columns(4)
    meal_cols[0].metric("Calories", f"{next_meal['calories']:.0f}")
    meal_cols[1].metric("Protein", f"{next_meal['protein_g']:.0f} g")
    meal_cols[2].metric("Carbs", f"{next_meal['carbs_g']:.0f} g")
    meal_cols[3].metric("Fat", f"{next_meal['fat_g']:.0f} g")

    if recommendation_error:
        st.warning(
            "The backend recommendation endpoint could not be reached, "
            "so the UI is showing the local fallback recommendation.\n\n"
            f"{recommendation_error}"
        )

    if recommendation:
        st.subheader("Why This Meal")
        for reason in recommendation.get("reasons", []):
            st.write(f"- {reason}")

        whoop_context = recommendation.get("whoop_context") or {}
        if any(value is not None for value in whoop_context.values()):
            st.subheader("WHOOP Context Used")
            whoop_cols = st.columns(4)
            whoop_cols[0].metric("Recovery", f"{whoop_context.get('recovery_score') or 0}%")
            whoop_cols[1].metric("HRV", f"{whoop_context.get('hrv_rmssd_milli') or 0}")
            whoop_cols[2].metric("RHR", f"{whoop_context.get('resting_heart_rate') or 0} bpm")
            whoop_cols[3].metric("Strain", f"{whoop_context.get('cycle_strain') or 0}")

        if recommendation.get("whoop_warning"):
            st.info(f"WHOOP note: {recommendation['whoop_warning']}")

    render_whoop_status()


def main() -> None:
    st.set_page_config(page_title="Whoop Meal AI", page_icon="🥗", layout="wide")
    init_db()

    page = st.sidebar.radio("Page", ["Dashboard", "Privacy Policy"])
    if page == "Privacy Policy":
        render_privacy_policy()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
