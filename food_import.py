from __future__ import annotations

import io
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd


def _to_float(x) -> Optional[float]:
    try:
        if pd.isna(x):
            return None
        s = str(x).strip()
        if s == "":
            return None
        s = s.replace(",", "")
        return float(s)
    except Exception:
        return None


def parse_netdiary_csv(csv_bytes: bytes, day_override: str | None = None) -> Tuple[List[Dict[str, Any]], str]:
    """
    NetDiary yearly export parser.
    Accepts raw CSV bytes (uploaded file content).
    Returns (rows, day_detected).
    """
    bio = io.BytesIO(csv_bytes)

    try:
        df = pd.read_csv(bio)
    except UnicodeDecodeError:
        bio.seek(0)
        df = pd.read_csv(bio, encoding="latin-1")

    # Exact columns from your export
    time_col = "Date & Time"
    item_col = "Name"
    cal_col = "Calories, cals"
    prot_col = "Protein, g"
    carb_col = "Total Carbs, g"
    fat_col = "Total Fat, g"

    missing = [c for c in [time_col, item_col, cal_col, prot_col, carb_col, fat_col] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing expected NetDiary columns: {missing}")

    out = pd.DataFrame()
    out["item_name"] = df[item_col].astype(str)

    out["calories"] = df[cal_col].map(_to_float)
    out["protein_g"] = df[prot_col].map(_to_float)
    out["carbs_g"] = df[carb_col].map(_to_float)
    out["fat_g"] = df[fat_col].map(_to_float)

    day_detected = ""

    if day_override:
        out["day"] = day_override
        out["eaten_at"] = None
        day_detected = day_override
    else:
        # Your sample looks like: "01 3 2026 12:00 AM"
        # This matches: "%m %d %Y %I:%M %p" (month day year hour:minute AM/PM)
        parsed = pd.to_datetime(
            df[time_col].astype(str).str.strip(),
            format="%m %d %Y %I:%M %p",
            errors="coerce",
        )

        out["eaten_at"] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
        out["day"] = parsed.dt.strftime("%Y-%m-%d")

        # Safety: drop rows where day failed to parse
        out = out[out["day"].notna()]

        if out["day"].notna().any():
            day_detected = out["day"].iloc[0]
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            out["day"] = today
            out["eaten_at"] = None
            day_detected = today

    # Drop rows where we have no usable nutrition values
    out = out.dropna(how="all", subset=["calories", "protein_g", "carbs_g", "fat_g"])

    rows = out.to_dict(orient="records")
    return rows, day_detected
