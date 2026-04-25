from __future__ import annotations

from datetime import date, timedelta
import json
import os
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

from db import init_db
from food_import import parse_netdiary_csv, parse_netdiary_summary_pdf
from lab_import import parse_lab_results_csv
from recommender import next_meal_target


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_CALORIES = 2000.0
DEFAULT_PROTEIN_G = 160.0
DEFAULT_CARBS_G = 180.0
DEFAULT_FAT_G = 70.0


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


def post_api_json(path: str, payload: dict) -> dict:
    url = f"{API_BASE_URL}{path}"
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
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


def render_macro_donut_chart(consumed: dict, goals: dict) -> None:
    st.markdown("##### Macro Split")

    protein = float(consumed.get("protein_g") or 0)
    carbs = float(consumed.get("carbs_g") or 0)
    fat = float(consumed.get("fat_g") or 0)
    total = protein + carbs + fat

    if total <= 0:
        st.info("Import a food log to see the macro split for this day.")
        return

    protein_pct = (protein / total) * 100.0
    carbs_pct = (carbs / total) * 100.0
    fat_pct = (fat / total) * 100.0
    carbs_end = protein_pct + carbs_pct

    left, right = st.columns([1, 1])
    with left:
        st.markdown(
            f"""
            <div style="
                background:linear-gradient(180deg, #0f172a 0%, #111827 100%);
                border:1px solid #1f2937;
                border-radius:24px;
                padding:24px 16px 20px 16px;
                text-align:center;
                box-shadow: inset 0 -10px 20px rgba(0,0,0,0.22);
            ">
                <div style="
                    width:210px;
                    height:210px;
                    margin:0 auto;
                    border-radius:50%;
                    background:
                        radial-gradient(circle at 50% 35%, rgba(255,255,255,0.18), transparent 28%),
                        radial-gradient(circle at 50% 50%, #0f172a 42%, transparent 43%),
                        conic-gradient(
                            #22c55e 0% {protein_pct:.2f}%,
                            #38bdf8 {protein_pct:.2f}% {carbs_end:.2f}%,
                            #f97316 {carbs_end:.2f}% 100%
                        );
                    box-shadow:
                        inset 0 -14px 20px rgba(0,0,0,0.28),
                        0 16px 28px rgba(0,0,0,0.25);
                    position:relative;
                ">
                    <div style="
                        position:absolute;
                        inset:39px;
                        border-radius:50%;
                        background:linear-gradient(180deg, #0b1220 0%, #111827 100%);
                        display:flex;
                        align-items:center;
                        justify-content:center;
                        flex-direction:column;
                        color:white;
                        box-shadow: inset 0 8px 18px rgba(255,255,255,0.04);
                    ">
                        <div style="font-size:0.8rem;letter-spacing:0.08em;text-transform:uppercase;color:#94a3b8;">Consumed</div>
                        <div style="font-size:2.1rem;font-weight:700;line-height:1.0;">{int(total)}g</div>
                        <div style="font-size:0.9rem;color:#94a3b8;margin-top:4px;">Macros today</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        render_stat_card(
            "Protein",
            f"{protein:.0f} g",
            f"{protein_pct:.0f}% of total | goal {float(goals.get('protein_g') or 0):.0f} g",
            "#22c55e",
        )
        render_stat_card(
            "Carbs",
            f"{carbs:.0f} g",
            f"{carbs_pct:.0f}% of total | goal {float(goals.get('carbs_g') or 0):.0f} g",
            "#38bdf8",
        )
        render_stat_card(
            "Fat",
            f"{fat:.0f} g",
            f"{fat_pct:.0f}% of total | goal {float(goals.get('fat_g') or 0):.0f} g",
            "#f97316",
        )


def render_top_whoop_strip(day_str: str) -> None:
    try:
        payload = fetch_api_json("/whoop/day", {"day": day_str})
    except RuntimeError:
        return

    metrics = payload.get("metrics") or {}
    st.markdown("##### WHOOP Core Metrics")
    cols = st.columns(3)
    with cols[0]:
        render_gauge_card("Recovery", float(metrics.get("recovery") or 0), 100.0, "#22c55e", "%")
    with cols[1]:
        render_gauge_card("Sleep", float(metrics.get("sleep_performance") or 0), 100.0, "#38bdf8", "%")
    with cols[2]:
        render_gauge_card("Strain", float(metrics.get("strain") or 0), 21.0, "#f97316")


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
        st.link_button("Open WHOOP Connect", connect_url, type="primary", use_container_width=True)
        return

    if not status.get("connected"):
        st.info("WHOOP is configured, but no stored connection is available yet.")
        st.link_button("Connect WHOOP", connect_url, type="primary", use_container_width=True)
        return

    if status.get("reauthorize_required") or status.get("expired"):
        st.warning("Your WHOOP session has expired. Reconnect to load live recovery and day metrics.")
        st.link_button("Reconnect WHOOP", connect_url, type="primary", use_container_width=True)
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
    st.caption("If WHOOP data stops loading later, reconnect from the top of the dashboard.")

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

    try:
        payload = fetch_api_json("/import/status", {"name": "mynetdiary_auto"})
    except RuntimeError as exc:
        st.info(f"Nutrition import status is not available yet. {exc}")
        return

    status = payload.get("status") if isinstance(payload, dict) else None
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


def resolve_nutrition_context_day(day_str: str) -> tuple[str, str]:
    expected_day = (date.fromisoformat(day_str) - timedelta(days=1)).isoformat()
    summary_payload = fetch_api_json(
        "/summary/day",
        {
            "day": expected_day,
            "calories": 0,
            "protein_g": 0,
            "carbs_g": 0,
            "fat_g": 0,
        },
    )
    consumed = summary_payload.get("consumed") or {}
    has_expected_data = any(float(consumed.get(key) or 0) > 0 for key in ("calories", "protein_g", "carbs_g", "fat_g"))
    if has_expected_data:
        return expected_day, "Yesterday's nutrition is available and matched to today's WHOOP context."

    try:
        payload = fetch_api_json("/import/status", {"name": "mynetdiary_auto"})
        status = payload.get("status") if isinstance(payload, dict) else None
    except RuntimeError:
        status = None

    imported_day = (status or {}).get("target_day")
    if imported_day:
        return imported_day, (
            f"Yesterday's nutrition is missing, so the chart is showing the latest imported nutrition day: {imported_day}."
        )

    return expected_day, "No imported nutrition day is available yet."


def get_latest_imported_nutrition_day() -> str | None:
    try:
        payload = fetch_api_json("/import/status", {"name": "mynetdiary_auto"})
    except RuntimeError:
        return None
    status = payload.get("status") if isinstance(payload, dict) else None
    if not status:
        return None
    return status.get("target_day")


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
    try:
        whoop_status = fetch_api_json("/whoop/status")
    except RuntimeError:
        whoop_status = {}

    button_label = "Connect WHOOP"
    if whoop_status.get("connected") or whoop_status.get("expired") or whoop_status.get("reauthorize_required"):
        button_label = "Reconnect WHOOP"
    top_button_col, _ = st.columns([1, 5])
    with top_button_col:
        st.link_button(button_label, f"{API_BASE_URL}/whoop/connect", type="primary")

    with st.sidebar:
        st.header("Daily View")
        target_day = st.date_input("Day", value=date.today())
        include_whoop = st.checkbox("Use WHOOP context when available", value=True)
        st.divider()
        st.subheader("Upload MyNetDiary Report")
        uploaded = st.file_uploader("Choose a MyNetDiary CSV or daily PDF", type=["csv", "pdf"])
        override_day = st.checkbox("Override imported day with selected day", value=False)
        st.divider()
        st.subheader("Upload Lab Results")
        lab_uploaded = st.file_uploader("Choose a lab CSV", type=["csv"], key="lab_upload")

    if uploaded is not None and st.button("Import Food Log"):
        raw = uploaded.read()
        try:
            if (uploaded.name or "").lower().endswith(".pdf"):
                rows, day_detected = parse_netdiary_summary_pdf(
                    raw,
                    filename=uploaded.name,
                )
                if override_day:
                    for row in rows:
                        row["day"] = target_day.isoformat()
            else:
                rows, day_detected = parse_netdiary_csv(
                    raw,
                    day_override=target_day.isoformat() if override_day else None,
                )
            import_result = post_api_json(
                "/import/netdiary/rows",
                {
                    "rows": rows,
                    "day": target_day.isoformat() if override_day else day_detected,
                    "source_kind": "pdf" if (uploaded.name or "").lower().endswith(".pdf") else "csv",
                    "source_path": uploaded.name or "streamlit_upload",
                },
            )
            inserted = int(import_result.get("inserted_rows") or 0)
            day_detected = import_result.get("day_detected") or day_detected
        except Exception as exc:
            st.error(f"Import failed: {exc}")
        else:
            st.success(f"Imported {inserted} rows for {day_detected or target_day.isoformat()}.")

    if lab_uploaded is not None and st.sidebar.button("Import Lab Results"):
        raw = lab_uploaded.read()
        try:
            lab_rows = parse_lab_results_csv(raw, source_default="lab_upload")
            lab_result = post_api_json(
                "/import/labs/rows",
                {
                    "rows": lab_rows,
                    "source_kind": "csv",
                    "source_path": lab_uploaded.name or "lab_upload",
                },
            )
            inserted = int(lab_result.get("inserted_rows") or 0)
        except Exception as exc:
            st.sidebar.error(f"Lab import failed: {exc}")
        else:
            st.sidebar.success(f"Imported {inserted} lab rows.")

    day_str = target_day.isoformat()
    nutrition_day, nutrition_context_note = resolve_nutrition_context_day(day_str)
    calories = DEFAULT_CALORIES
    protein_g = DEFAULT_PROTEIN_G
    carbs_g = DEFAULT_CARBS_G
    fat_g = DEFAULT_FAT_G
    render_top_whoop_strip(day_str)
    render_import_status_panel(day_str)

    summary_payload = fetch_api_json(
        "/summary/day",
        {
            "day": nutrition_day,
            "calories": calories,
            "protein_g": protein_g,
            "carbs_g": carbs_g,
            "fat_g": fat_g,
        },
    )
    consumed = summary_payload.get("consumed") or {
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
    }
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
                "insulin_resistant": "false",
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

    top_left, top_right = st.columns([1.2, 1])
    with top_left:
        render_morning_brief(
            day_str,
            calories=calories,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            insulin_resistant=False,
            include_whoop=include_whoop,
        )
    with top_right:
        st.subheader("Nutrition Context")
        st.caption(f"WHOOP day: {day_str} | Nutrition day: {nutrition_day}")
        st.caption(nutrition_context_note)
        top = st.columns(4)
        top[0].metric("Calories", f"{consumed['calories']:.0f}", f"{remaining['calories']:.0f} left")
        top[1].metric("Protein", f"{consumed['protein_g']:.0f} g", f"{remaining['protein_g']:.0f} g left")
        top[2].metric("Carbs", f"{consumed['carbs_g']:.0f} g", f"{remaining['carbs_g']:.0f} g left")
        top[3].metric("Fat", f"{consumed['fat_g']:.0f} g", f"{remaining['fat_g']:.0f} g left")
        render_macro_donut_chart(consumed, goals)

    latest_imported_day = get_latest_imported_nutrition_day()
    if latest_imported_day and latest_imported_day != nutrition_day:
        latest_payload = fetch_api_json(
            "/summary/day",
            {
                "day": latest_imported_day,
                "calories": calories,
                "protein_g": protein_g,
                "carbs_g": carbs_g,
                "fat_g": fat_g,
            },
        )
        latest_consumed = latest_payload.get("consumed") or {
            "calories": 0.0,
            "protein_g": 0.0,
            "carbs_g": 0.0,
            "fat_g": 0.0,
        }
        st.subheader("Last Imported Nutrition Day")
        st.caption(f"This visual always shows the latest uploaded nutrition file: {latest_imported_day}")
        latest_top = st.columns(4)
        latest_top[0].metric("Calories", f"{latest_consumed['calories']:.0f}")
        latest_top[1].metric("Protein", f"{latest_consumed['protein_g']:.0f} g")
        latest_top[2].metric("Carbs", f"{latest_consumed['carbs_g']:.0f} g")
        latest_top[3].metric("Fat", f"{latest_consumed['fat_g']:.0f} g")
        render_macro_donut_chart(latest_consumed, goals)

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
