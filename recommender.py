from __future__ import annotations

def _f(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0

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

    # Simple next-meal rules
    # Protein: aim 35–45g if you still need it
    next_p = min(max(rem_p, 0.0), 45.0) if rem_p >= 25 else rem_p

    # Calories: ~30% of remaining, bounded
    next_cals = 0.0
    if rem_cals > 0:
        next_cals = min(max(rem_cals * 0.30, 250.0), 650.0)

    # Carbs/fat caps
    next_carbs = min(rem_carbs, 40.0)
    next_fat = min(rem_fat, 25.0)

    return {
        "calories": round(next_cals, 1),
        "protein_g": round(next_p, 1),
        "carbs_g": round(next_carbs, 1),
        "fat_g": round(next_fat, 1),
    }
