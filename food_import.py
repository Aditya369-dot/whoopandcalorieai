from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
from pypdf import PdfReader


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


def _extract_pdf_day(text: str, filename: str | None = None) -> str:
    if filename:
        match = re.search(r"from (\d{2})_(\d{2})_(\d{2}) to", filename)
        if match:
            month, day, year = match.groups()
            return f"20{year}-{month}-{day}"

    match = re.search(r"Summary for \w{3}, ([A-Za-z]{3}) (\d{1,2})", text)
    if match:
        month_name, day = match.groups()
        month = datetime.strptime(month_name, "%b").month
        year = datetime.now().year
        return f"{year:04d}-{month:02d}-{int(day):02d}"

    raise ValueError("Unable to determine report day from PDF.")


def parse_netdiary_summary_pdf(pdf_bytes: bytes, filename: str | None = None) -> Tuple[List[Dict[str, Any]], str]:
    """
    Parse a MyNetDiary daily summary PDF into meal-level rows.
    This is intentionally conservative: it extracts meal totals rather than
    trying to reconstruct every individual food entry from the PDF layout.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not text.strip():
        raise ValueError("PDF contained no extractable text.")

    day_detected = _extract_pdf_day(text, filename=filename)

    meal_rows: list[dict[str, Any]] = []
    meal_pattern = re.compile(
        r"^(Breakfast|Lunch|Dinner|Snacks?)\s+([\d,]+)cals(\d+)g(\d+)g(\d+)g",
        re.MULTILINE,
    )
    for meal_name, calories, fat_g, carbs_g, protein_g in meal_pattern.findall(text):
        meal_rows.append(
            {
                "source": "netdiary_pdf_auto",
                "eaten_at": None,
                "day": day_detected,
                "item_name": f"{meal_name} total",
                "calories": float(calories.replace(",", "")),
                "protein_g": float(protein_g),
                "carbs_g": float(carbs_g),
                "fat_g": float(fat_g),
            }
        )

    if not meal_rows:
        raise ValueError("No meal totals were detected in the MyNetDiary PDF.")

    return meal_rows, day_detected
