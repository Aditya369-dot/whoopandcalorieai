from __future__ import annotations

from datetime import date, timedelta


def _f(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _get_nested(source: dict | None, *keys, default=None):
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def next_meal_target(consumed: dict, goals: dict) -> dict:
    """
    consumed/goals are dicts like:
      {"calories": 1200, "protein_g": 90, "carbs_g": 140, "fat_g": 45}
    Returns suggested next meal macros (rule-based).
    """
    cals = _f(consumed.get("calories"))
    p = _f(consumed.get("protein_g"))
    carbs = _f(consumed.get("carbs_g"))
    fat = _f(consumed.get("fat_g"))

    g_cals = _f(goals.get("calories"))
    g_p = _f(goals.get("protein_g"))
    g_carbs = _f(goals.get("carbs_g"))
    g_fat = _f(goals.get("fat_g"))

    rem_cals = max(g_cals - cals, 0.0)
    rem_p = max(g_p - p, 0.0)
    rem_carbs = max(g_carbs - carbs, 0.0)
    rem_fat = max(g_fat - fat, 0.0)

    next_p = min(max(rem_p, 0.0), 45.0) if rem_p >= 25 else rem_p

    next_cals = 0.0
    if rem_cals > 0:
        next_cals = min(max(rem_cals * 0.30, 250.0), 650.0)

    next_carbs = min(rem_carbs, 40.0)
    next_fat = min(rem_fat, 25.0)

    return {
        "calories": round(next_cals, 1),
        "protein_g": round(next_p, 1),
        "carbs_g": round(next_carbs, 1),
        "fat_g": round(next_fat, 1),
    }


def recommend_next_meal(
    consumed: dict,
    goals: dict,
    *,
    whoop_snapshot: dict | None = None,
    insulin_resistant: bool = False,
) -> dict:
    """
    Returns a next-meal recommendation with simple WHOOP-aware adjustments
    and short human-readable reasons.
    """
    baseline = next_meal_target(consumed, goals)
    remaining = {key: max(_f(goals.get(key)) - _f(consumed.get(key)), 0.0) for key in goals}

    recommendation = dict(baseline)
    reasons: list[str] = []

    recovery_score = _f(_get_nested(whoop_snapshot, "recovery", "score", "recovery_score"))
    hrv = _f(_get_nested(whoop_snapshot, "recovery", "score", "hrv_rmssd_milli"))
    rhr = _f(_get_nested(whoop_snapshot, "recovery", "score", "resting_heart_rate"))
    cycle_strain = _f(_get_nested(whoop_snapshot, "cycle", "score", "strain"))

    workouts = whoop_snapshot.get("workouts", []) if isinstance(whoop_snapshot, dict) else []
    workout_strains = [
        _f(_get_nested(workout, "score", "strain"))
        for workout in workouts
        if isinstance(workout, dict)
    ]
    recent_training_load = max(workout_strains) if workout_strains else 0.0

    if insulin_resistant:
        recommendation["carbs_g"] = min(recommendation["carbs_g"], 25.0, remaining["carbs_g"])
        recommendation["fat_g"] = min(recommendation["fat_g"], 20.0, remaining["fat_g"])
        reasons.append(
            "Insulin resistance is enabled, so the default recommendation keeps carbs more conservative."
        )

    if recovery_score >= 67:
        recommendation["calories"] = min(
            max(recommendation["calories"], 300.0),
            max(remaining["calories"], 0.0),
        )
        reasons.append("Recovery is strong today, so the meal can support performance and replenishment.")
    elif 0 < recovery_score < 34:
        recommendation["calories"] = min(recommendation["calories"], 350.0, remaining["calories"])
        recommendation["fat_g"] = min(recommendation["fat_g"], 15.0, remaining["fat_g"])
        reasons.append("Recovery is low, so the meal is kept lighter and easier to digest.")

    if recent_training_load >= 12 or cycle_strain >= 14:
        carb_cap = 55.0 if not insulin_resistant else 35.0
        if remaining["carbs_g"] > 0:
            recommendation["carbs_g"] = min(
                max(recommendation["carbs_g"], 25.0),
                carb_cap,
                remaining["carbs_g"],
            )
        recommendation["protein_g"] = min(
            max(recommendation["protein_g"], 35.0),
            45.0,
            remaining["protein_g"],
        )
        reasons.append(
            "Training strain is elevated, so the recommendation shifts toward protein plus targeted carbs."
        )

    if remaining["protein_g"] >= 40:
        recommendation["protein_g"] = min(
            max(recommendation["protein_g"], 35.0),
            45.0,
            remaining["protein_g"],
        )
        reasons.append("Protein is still meaningfully under target, so the next meal emphasizes protein.")

    if not reasons:
        reasons.append("This recommendation is based on your remaining daily calorie and macro targets.")

    whoop_context = {
        "recovery_score": round(recovery_score, 1) if recovery_score else None,
        "hrv_rmssd_milli": round(hrv, 1) if hrv else None,
        "resting_heart_rate": round(rhr, 1) if rhr else None,
        "cycle_strain": round(cycle_strain, 1) if cycle_strain else None,
        "max_workout_strain": round(recent_training_load, 1) if recent_training_load else None,
    }

    return {
        "next_meal_target": {
            "calories": round(min(recommendation["calories"], remaining["calories"]), 1),
            "protein_g": round(min(recommendation["protein_g"], remaining["protein_g"]), 1),
            "carbs_g": round(min(recommendation["carbs_g"], remaining["carbs_g"]), 1),
            "fat_g": round(min(recommendation["fat_g"], remaining["fat_g"]), 1),
        },
        "remaining": {k: round(v, 1) for k, v in remaining.items()},
        "whoop_context": whoop_context,
        "reasons": reasons,
    }


def _previous_day_iso(day_value: str) -> str | None:
    try:
        return (date.fromisoformat(day_value) - timedelta(days=1)).isoformat()
    except ValueError:
        return None


def build_daily_brief(
    *,
    day: str,
    consumed_today: dict,
    goals: dict,
    yesterday_consumed: dict | None = None,
    whoop_snapshot: dict | None = None,
    insulin_resistant: bool = False,
) -> dict:
    yesterday_consumed = yesterday_consumed or {}
    recovery_score = _f(_get_nested(whoop_snapshot, "recovery", "score", "recovery_score"))
    hrv = _f(_get_nested(whoop_snapshot, "recovery", "score", "hrv_rmssd_milli"))
    rhr = _f(_get_nested(whoop_snapshot, "recovery", "score", "resting_heart_rate"))
    cycle_strain = _f(_get_nested(whoop_snapshot, "cycle", "score", "strain"))
    sleep_hours = (
        _f(_get_nested(whoop_snapshot, "sleep", "score", "stage_summary", "total_light_sleep_time_milli"))
        + _f(_get_nested(whoop_snapshot, "sleep", "score", "stage_summary", "total_slow_wave_sleep_time_milli"))
        + _f(_get_nested(whoop_snapshot, "sleep", "score", "stage_summary", "total_rem_sleep_time_milli"))
    ) / 3600000.0
    sleep_performance = _f(_get_nested(whoop_snapshot, "sleep", "score", "sleep_performance_percentage"))

    yesterday_calories = _f(yesterday_consumed.get("calories"))
    yesterday_protein = _f(yesterday_consumed.get("protein_g"))
    yesterday_carbs = _f(yesterday_consumed.get("carbs_g"))
    yesterday_fat = _f(yesterday_consumed.get("fat_g"))

    adjusted_goals = {
        "calories": _f(goals.get("calories")),
        "protein_g": max(_f(goals.get("protein_g")), 160.0),
        "carbs_g": _f(goals.get("carbs_g")),
        "fat_g": _f(goals.get("fat_g")),
    }

    training_focus = "Normal training day"
    day_type = "balanced"
    if recovery_score >= 67:
        training_focus = "Performance-supportive day: lift, build, or push volume if desired."
        day_type = "performance"
        adjusted_goals["calories"] += 100.0
        adjusted_goals["carbs_g"] += 20.0
    elif 0 < recovery_score < 40:
        training_focus = "Recovery-first day: walking, mobility, zone 2, and lower systemic stress."
        day_type = "recovery"
        adjusted_goals["calories"] = max(adjusted_goals["calories"] - 150.0, 1600.0)
        adjusted_goals["fat_g"] = min(adjusted_goals["fat_g"], 60.0)
    else:
        training_focus = "Controlled-performance day: productive work is fine, but avoid max-effort training."

    if insulin_resistant:
        adjusted_goals["carbs_g"] = min(adjusted_goals["carbs_g"], 130.0)
        adjusted_goals["fat_g"] = min(adjusted_goals["fat_g"], 70.0)

    if yesterday_fat >= 90:
        adjusted_goals["fat_g"] = min(adjusted_goals["fat_g"], 60.0)
    if yesterday_protein < 120:
        adjusted_goals["protein_g"] = max(adjusted_goals["protein_g"], 170.0)
    if yesterday_calories > adjusted_goals["calories"] + 150:
        adjusted_goals["calories"] = max(adjusted_goals["calories"] - 100.0, 1700.0)

    recommendation = recommend_next_meal(
        consumed_today,
        adjusted_goals,
        whoop_snapshot=whoop_snapshot,
        insulin_resistant=insulin_resistant,
    )

    observations: list[str] = []
    if sleep_hours:
        observations.append(f"Sleep delivered {sleep_hours:.1f} h with {sleep_performance:.0f}% sleep performance.")
    if recovery_score:
        observations.append(f"WHOOP recovery is {recovery_score:.0f}% with HRV {hrv:.1f} and resting HR {rhr:.0f}.")
    if yesterday_calories:
        observations.append(
            f"Yesterday landed at {yesterday_calories:.0f} kcal with {yesterday_protein:.0f}g protein, "
            f"{yesterday_carbs:.0f}g carbs, and {yesterday_fat:.0f}g fat."
        )
    if insulin_resistant:
        observations.append("Insulin resistance mode is on, so carbs stay controlled and meals should be fiber-forward.")

    priorities: list[str] = []
    if recovery_score >= 67:
        priorities.append("Front-load protein early and place most carbs around training or mid-day activity.")
    elif recovery_score > 0:
        priorities.append("Keep breakfast protein-heavy and avoid a very high-fat first meal.")
    if yesterday_fat >= 90:
        priorities.append("Yesterday was fat-heavy, so keep fats tighter today and use leaner protein sources.")
    if cycle_strain < 6:
        priorities.append("Current strain is still low, so let movement quality set the tone before adding intensity.")
    else:
        priorities.append("Use today's existing strain to decide whether you need more fuel before another hard session.")

    breakfast = {
        "calories": min(max(recommendation["next_meal_target"]["calories"], 300.0), 550.0),
        "protein_g": min(max(recommendation["next_meal_target"]["protein_g"], 35.0), 45.0),
        "carbs_g": recommendation["next_meal_target"]["carbs_g"],
        "fat_g": recommendation["next_meal_target"]["fat_g"],
    }

    breakfast_strategy = (
        "High protein breakfast, controlled carbs, and lighter fats to stabilize energy and support recovery."
        if insulin_resistant or recovery_score < 67
        else "Protein-forward breakfast with strategic carbs to support a stronger performance day."
    )

    recovery_focus = (
        "Hydrate early, get sunlight, and use a lighter training load while recovery builds."
        if recovery_score < 67
        else "Hydrate, train with intent, and use meals to reinforce performance instead of chasing calories late."
    )

    return {
        "day": day,
        "previous_day": _previous_day_iso(day),
        "day_type": day_type,
        "today_consumed": {k: round(_f(v), 1) for k, v in consumed_today.items()},
        "yesterday_consumed": {k: round(_f(v), 1) for k, v in yesterday_consumed.items()},
        "adjusted_goals": {k: round(_f(v), 1) for k, v in adjusted_goals.items()},
        "whoop_context": {
            "recovery_score": round(recovery_score, 1) if recovery_score else None,
            "sleep_hours": round(sleep_hours, 2) if sleep_hours else None,
            "sleep_performance": round(sleep_performance, 1) if sleep_performance else None,
            "hrv_rmssd_milli": round(hrv, 1) if hrv else None,
            "resting_heart_rate": round(rhr, 1) if rhr else None,
            "cycle_strain": round(cycle_strain, 1) if cycle_strain else None,
        },
        "training_focus": training_focus,
        "recovery_focus": recovery_focus,
        "breakfast_strategy": breakfast_strategy,
        "breakfast_target": breakfast,
        "next_meal_target": recommendation["next_meal_target"],
        "observations": observations,
        "priorities": priorities,
    }
