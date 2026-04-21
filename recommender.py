from __future__ import annotations


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
