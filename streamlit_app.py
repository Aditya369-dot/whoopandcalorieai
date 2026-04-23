from __future__ import annotations

from datetime import date
import os
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import streamlit as st

from db import get_conn, get_import_status, init_db
from food_import import parse_netdiary_csv
from recommender import next_meal_target


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


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


def render_gauge_card(label: str, value: float, max_value: float, color: str, suffix: str = "") -> None:
    safe_max = max(max_value, 1.0)
    percent = max(0.0, min(value / safe_max, 1.0)) * 100.0
    display_value = f"{value:.1f}{suffix}" if isinstance(value, float) and not value.is_integer() else f"{int(value)}{suffix}"

    st.markdown(
        f"""
        <div style="background:#111827;border:1px solid #1f2937;border-radius:20px;padding:20px;text-align:center;">
            <div style="font-size:0.95rem;color:#9ca3af;margin-bottom:14px;">{label}</div>
            <div style="
                width:120px;
                height:120px;
                margin:0 auto 12px auto;
                border-radius:50%;
                background:
                    radial-gradient(closest-side, #111827 72%, transparent 73% 100%),
                    conic-gradient({color} {percent:.1f}%, #253041 0);
                display:flex;
                align-items:center;
                justify-content:center;
                box-shadow: inset 0 0 0 1px #374151;
            ">
                <div style="font-size:1.4rem;font-weight:700;color:white;">{display_value}</div>
            </div>
            <div style="font-size:0.85rem;color:#9ca3af;">Target scale: 0-{max_value:g}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_card(label: str, value: str, detail: str = "", accent: str = "#334155") -> None:
    st.markdown(
        f"""
        <div style="
            background:linear-gradient(180deg, #111827 0%, #0f172a 100%);
            border:1px solid #1f2937;
            border-left:4px solid {accent};
            border-radius:18px;
            padding:16px 18px;
            min-height:108px;
        ">
            <div style="font-size:0.82rem;letter-spacing:0.08em;text-transform:uppercase;color:#94a3b8;">{label}</div>
            <div style="font-size:1.7rem;font-weight:700;color:white;margin-top:10px;line-height:1.1;">{value}</div>
            <div style="font-size:0.88rem;color:#94a3b8;margin-top:10px;">{detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_banner(title: str, subtitle: str, accent: str) -> None:
    st.markdown(
        f"""
        <div style="
            background:linear-gradient(135deg, {accent}22 0%, #0f172a 65%);
            border:1px solid #1f2937;
            border-radius:22px;
            padding:18px 20px;
            margin:8px 0 16px 0;
        ">
            <div style="font-size:0.78rem;letter-spacing:0.12em;text-transform:uppercase;color:#cbd5e1;">WHOOP Snapshot</div>
            <div style="font-size:1.45rem;font-weight:700;color:white;margin-top:6px;">{title}</div>
            <div style="font-size:0.95rem;color:#94a3b8;margin-top:6px;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    st.caption("This checks WHOOP connectivity through the backend API.")
    connect_url = f"{API_BASE_URL}/whoop/connect"

    try:
        status = fetch_api_json("/whoop/status")
    except RuntimeError as exc:
        st.warning(f"Unable to reach WHOOP backend status. {exc}")
        return

    if not status.get("configured"):
        st.info("WHOOP OAuth is not configured on the backend yet.")
        st.markdown(f"[Open WHOOP Connect]({connect_url})")
        return

    if not status.get("connected"):
        st.info("WHOOP is configured, but no stored connection is available yet.")
        st.markdown(f"[Connect WHOOP]({connect_url})")
        return

    if status.get("reauthorize_required") or status.get("expired"):
        st.warning("Your WHOOP session has expired. Reconnect to load live recovery and day metrics.")
        st.markdown(f"[Reconnect WHOOP]({connect_url})")
        return

    profile = None
    try:
        profile = fetch_api_json("/whoop/profile")
    except RuntimeError as exc:
        st.warning(f"WHOOP is connected, but the profile lookup failed. {exc}")

    token = status.get("token") or {}
    user_label = profile.get("user_id") if isinstance(profile, dict) else None
    if user_label is not None:
        st.success(f"Connected to WHOOP as user `{user_label}`.")
    else:
        st.success("WHOOP is connected and the access token is stored.")

    meta_cols = st.columns(3)
    meta_cols[0].caption(f"Scope: {token.get('scope') or 'unknown'}")
    meta_cols[1].caption(f"Stored at: {token.get('created_at') or 'unknown'}")
    meta_cols[2].caption(
        f"Expires: {token.get('expires_at') or 'unknown'}"
    )
    st.caption("If WHOOP data stops loading later, use reconnect below to refresh the session.")
    st.markdown(f"[Reconnect WHOOP]({connect_url})")

    try:
        recovery = fetch_api_json("/whoop/recovery/current")
    except RuntimeError as exc:
        st.warning(f"Connected, but current recovery could not be fetched. {exc}")
        return

    if not recovery or recovery.get("status") == "no_recovery_available":
        st.info("WHOOP connection works, but no scored recovery is available yet.")
        return

    score = (recovery.get("score") or {}) if isinstance(recovery, dict) else {}
    cols = st.columns(3)
    cols[0].metric("Recovery", f"{int(score.get('recovery_score') or 0)}%")
    cols[1].metric("HRV", f"{float(score.get('hrv_rmssd_milli') or 0):.1f}")
    cols[2].metric("RHR", f"{int(score.get('resting_heart_rate') or 0)} bpm")


def render_import_status_panel(day_str: str) -> None:
    st.subheader("Nutrition Import Status")
    st.caption("This shows whether yesterday's intake was imported successfully for the current brief.")

    status = get_import_status("mynetdiary_auto")
    expected_day = (date.fromisoformat(day_str) if day_str else date.today()).replace()
    expected_day = expected_day.fromordinal(expected_day.toordinal() - 1).isoformat()

    if not status:
        st.info("No automated MyNetDiary import has run yet.")
        return

    state = status.get("status") or "unknown"
    target_day = status.get("target_day") or "unknown"
    detected_day = status.get("detected_day") or "unknown"
    source_kind = (status.get("source_kind") or "unknown").upper()
    attempted = status.get("last_attempted_at") or "unknown"
    succeeded = status.get("last_succeeded_at") or "not yet"
    rows = int(status.get("rows_inserted") or 0)

    if state == "success" and target_day == expected_day:
        st.success(f"Yesterday's nutrition is fresh. Imported {rows} rows for {target_day}.")
    elif state == "stale":
        st.warning(f"Latest import file is stale. Expected {expected_day}, but the newest report was for {detected_day}.")
    elif state == "missing":
        st.warning(f"No rows were imported for {target_day}.")
    else:
        st.info(f"Last import status: {state}.")

    cols = st.columns(4)
    cols[0].metric("Expected Day", expected_day)
    cols[1].metric("Imported Day", target_day)
    cols[2].metric("Source", source_kind)
    cols[3].metric("Rows", str(rows))

    st.caption(f"Last attempted: {attempted}")
    st.caption(f"Last succeeded: {succeeded}")
    if status.get("message"):
        st.caption(str(status["message"]))


def render_whoop_day_overview(day_str: str) -> None:
    st.subheader("WHOOP Day")
    st.caption("Recovery, strain, and sleep details pulled from WHOOP for the selected date.")
    connect_url = f"{API_BASE_URL}/whoop/connect"

    try:
        status = fetch_api_json("/whoop/status")
    except RuntimeError:
        status = {}

    if status.get("reauthorize_required") or status.get("expired"):
        st.warning("WHOOP session expired. Reconnect to restore live daily metrics.")
        st.markdown(f"[Reconnect WHOOP]({connect_url})")
        return

    try:
        payload = fetch_api_json("/whoop/day", {"day": day_str})
    except RuntimeError as exc:
        st.info(f"WHOOP day metrics are not available yet. {exc}")
        return

    metrics = payload.get("metrics") or {}
    snapshot = payload.get("snapshot") or {}
    cycle = snapshot.get("cycle") or {}
    cycle_score = cycle.get("score") or {}
    recovery = snapshot.get("recovery") or {}
    recovery_score = recovery.get("score") or {}
    sleep = snapshot.get("sleep") or {}
    sleep_score = sleep.get("score") or {}
    sleep_stage = sleep_score.get("stage_summary") or {}
    sleep_needed = sleep_score.get("sleep_needed") or {}
    workouts = snapshot.get("workouts") or []

    strain = float(metrics.get("strain") or 0)
    recovery_pct = float(metrics.get("recovery") or 0)
    sleep_pct = float(metrics.get("sleep_performance") or 0)

    status_bits = []
    if recovery_pct >= 67:
        status_bits.append("high recovery")
    elif recovery_pct > 0:
        status_bits.append("lower recovery")
    if strain:
        status_bits.append(f"{strain:.1f} day strain")
    if sleep_pct:
        status_bits.append(f"{int(sleep_pct)}% sleep performance")
    subtitle = " | ".join(status_bits) if status_bits else "WHOOP data is available for this day."
    render_section_banner(payload.get("date") or day_str, subtitle, "#22c55e")

    gauge_cols = st.columns(3)
    with gauge_cols[0]:
        render_gauge_card("Strain", strain, 21.0, "#f97316")
    with gauge_cols[1]:
        render_gauge_card("Recovery", recovery_pct, 100.0, "#22c55e", "%")
    with gauge_cols[2]:
        render_gauge_card("Sleep", sleep_pct, 100.0, "#38bdf8", "%")

    headline = st.columns(4)
    with headline[0]:
        render_stat_card(
            "Sleep Duration",
            f"{float(metrics.get('sleep_hours') or 0):.1f} h",
            f"Needed: {round((float(sleep_needed.get('baseline_milli') or 0) / 3600000.0), 1):.1f} h baseline"
            if sleep_needed.get("baseline_milli")
            else "Based on scored WHOOP sleep",
            "#38bdf8",
        )
    with headline[1]:
        render_stat_card(
            "Heart Rate Variability",
            f"{float(metrics.get('hrv_rmssd_milli') or 0):.1f}",
            "RMSSD from recovery score",
            "#22c55e",
        )
    with headline[2]:
        render_stat_card(
            "Resting Heart Rate",
            f"{int(metrics.get('resting_heart_rate') or 0)} bpm",
            f"Avg HR: {int(cycle_score.get('average_heart_rate') or 0)} bpm",
            "#f97316",
        )
    with headline[3]:
        render_stat_card(
            "Respiratory Rate",
            f"{float(sleep_score.get('respiratory_rate') or 0):.1f}",
            f"Sleep efficiency: {float(sleep_score.get('sleep_efficiency_percentage') or 0):.1f}%",
            "#a78bfa",
        )

    st.markdown("##### Sleep Breakdown")
    sleep_cols = st.columns(4)
    with sleep_cols[0]:
        render_stat_card(
            "Light Sleep",
            f"{round(float(sleep_stage.get('total_light_sleep_time_milli') or 0) / 3600000.0, 1):.1f} h",
            "Lighter restorative sleep",
            "#38bdf8",
        )
    with sleep_cols[1]:
        render_stat_card(
            "Deep Sleep",
            f"{round(float(sleep_stage.get('total_slow_wave_sleep_time_milli') or 0) / 3600000.0, 1):.1f} h",
            "Slow wave sleep",
            "#6366f1",
        )
    with sleep_cols[2]:
        render_stat_card(
            "REM Sleep",
            f"{round(float(sleep_stage.get('total_rem_sleep_time_milli') or 0) / 3600000.0, 1):.1f} h",
            f"{int(sleep_stage.get('sleep_cycle_count') or 0)} sleep cycles",
            "#ec4899",
        )
    with sleep_cols[3]:
        render_stat_card(
            "Disturbances",
            f"{int(sleep_stage.get('disturbance_count') or 0)}",
            f"{round(float(sleep_stage.get('total_awake_time_milli') or 0) / 60000.0):.0f} min awake",
            "#f59e0b",
        )

    st.markdown("##### Daily Load")
    load_cols = st.columns(4)
    with load_cols[0]:
        render_stat_card(
            "Average Heart Rate",
            f"{int(cycle_score.get('average_heart_rate') or 0)} bpm",
            "Across the daily cycle",
            "#f97316",
        )
    with load_cols[1]:
        render_stat_card(
            "Max Heart Rate",
            f"{int(cycle_score.get('max_heart_rate') or 0)} bpm",
            "Peak seen in cycle score",
            "#ef4444",
        )
    with load_cols[2]:
        render_stat_card(
            "Energy",
            f"{float(cycle_score.get('kilojoule') or 0):.0f} kJ",
            "WHOOP cycle energy output",
            "#14b8a6",
        )
    with load_cols[3]:
        render_stat_card(
            "Workouts Logged",
            str(len(workouts)),
            "Detected in WHOOP for the day",
            "#22c55e",
        )

    if workouts:
        st.markdown("##### Workouts")
        for workout in workouts:
            workout_score = workout.get("score") or {}
            sport_name = workout.get("sport_name") or "Workout"
            detail_parts = [
                f"strain {float(workout_score.get('strain') or 0):.1f}",
                f"avg HR {int(workout_score.get('average_heart_rate') or 0)} bpm",
                f"max HR {int(workout_score.get('max_heart_rate') or 0)} bpm",
            ]
            st.markdown(
                f"- **{sport_name}**: " + " | ".join(detail_parts)
            )


def render_morning_brief(
    day_str: str,
    *,
    calories: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    insulin_resistant: bool,
    include_whoop: bool,
) -> None:
    st.subheader("Morning Performance Brief")
    st.caption("Daily coaching built from yesterday's intake and today's WHOOP readiness.")
    connect_url = f"{API_BASE_URL}/whoop/connect"

    try:
        brief = fetch_api_json(
            "/brief/day",
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
        st.info(f"Morning brief is not available yet. {exc}")
        return

    if brief.get("whoop_warning"):
        st.warning(f"WHOOP data unavailable, using nutrition-first fallback. {brief['whoop_warning']}")
        st.markdown(f"[Reconnect WHOOP]({connect_url})")

    whoop_context = brief.get("whoop_context") or {}
    subtitle_bits = []
    if whoop_context.get("recovery_score") is not None:
        subtitle_bits.append(f"Recovery {int(whoop_context['recovery_score'])}%")
    if whoop_context.get("sleep_hours") is not None:
        subtitle_bits.append(f"{float(whoop_context['sleep_hours']):.1f} h sleep")
    if whoop_context.get("cycle_strain") is not None:
        subtitle_bits.append(f"{float(whoop_context['cycle_strain']):.1f} strain")
    subtitle = " | ".join(subtitle_bits) if subtitle_bits else "Nutrition-first daily plan"
    accent = "#22c55e" if brief.get("day_type") == "performance" else "#f59e0b"
    render_section_banner(f"{day_str} Plan", subtitle, accent)

    top = st.columns(3)
    with top[0]:
        render_stat_card("Training Focus", brief.get("day_type", "balanced").replace("_", " ").title(), brief.get("training_focus", ""), accent)
    with top[1]:
        adjusted = brief.get("adjusted_goals") or {}
        render_stat_card(
            "Nutrition Target",
            f"{int(float(adjusted.get('calories') or 0))} kcal",
            f"P {int(float(adjusted.get('protein_g') or 0))}g | C {int(float(adjusted.get('carbs_g') or 0))}g | F {int(float(adjusted.get('fat_g') or 0))}g",
            "#38bdf8",
        )
    with top[2]:
        breakfast = brief.get("breakfast_target") or {}
        render_stat_card(
            "Breakfast Target",
            f"{int(float(breakfast.get('calories') or 0))} kcal",
            f"P {int(float(breakfast.get('protein_g') or 0))}g | C {int(float(breakfast.get('carbs_g') or 0))}g | F {int(float(breakfast.get('fat_g') or 0))}g",
            "#ec4899",
        )

    st.markdown("##### Coach Readout")
    st.write(brief.get("breakfast_strategy") or "No breakfast strategy available.")
    st.write(brief.get("recovery_focus") or "No recovery focus available.")

    left, right = st.columns(2)
    with left:
        st.markdown("##### Observations")
        for item in brief.get("observations", []):
            st.write(f"- {item}")
    with right:
        st.markdown("##### Priorities")
        for item in brief.get("priorities", []):
            st.write(f"- {item}")

    context_cols = st.columns(2)
    yesterday = brief.get("yesterday_consumed") or {}
    today = brief.get("today_consumed") or {}
    with context_cols[0]:
        render_stat_card(
            f"Yesterday ({brief.get('previous_day') or 'prior'})",
            f"{int(float(yesterday.get('calories') or 0))} kcal",
            f"P {int(float(yesterday.get('protein_g') or 0))}g | C {int(float(yesterday.get('carbs_g') or 0))}g | F {int(float(yesterday.get('fat_g') or 0))}g",
            "#f97316",
        )
    with context_cols[1]:
        render_stat_card(
            "Consumed So Far",
            f"{int(float(today.get('calories') or 0))} kcal",
            f"P {int(float(today.get('protein_g') or 0))}g | C {int(float(today.get('carbs_g') or 0))}g | F {int(float(today.get('fat_g') or 0))}g",
            "#14b8a6",
        )


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
    render_import_status_panel(day_str)
    render_morning_brief(
        day_str,
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        insulin_resistant=insulin_resistant,
        include_whoop=include_whoop,
    )
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
