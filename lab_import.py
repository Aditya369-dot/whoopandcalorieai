from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import pandas as pd


_COLUMN_ALIASES = {
    "day": ["day", "date", "collection_date", "collected_on", "test_date"],
    "collected_at": ["collected_at", "datetime", "timestamp"],
    "biomarker": ["biomarker", "marker", "test", "lab", "analyte", "name"],
    "value": ["value", "result", "numeric_result"],
    "unit": ["unit", "units"],
    "notes": ["notes", "comment", "comments"],
    "source": ["source", "provider", "lab_provider"],
}


def _normalize_columns(columns: List[str]) -> Dict[str, str]:
    normalized = {str(col).strip().lower(): str(col) for col in columns}
    mapping: Dict[str, str] = {}

    for target, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[target] = normalized[alias]
                break

    return mapping


def _to_float(value: Any) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _coerce_day(value: Any) -> Optional[str]:
    if pd.isna(value):
        return None

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        text = str(value).strip()
        return text or None
    return parsed.strftime("%Y-%m-%d")


def _coerce_datetime(value: Any) -> Optional[str]:
    if pd.isna(value):
        return None

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        text = str(value).strip()
        return text or None
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def parse_lab_results_csv(csv_bytes: bytes, source_default: str = "csv") -> List[Dict[str, Any]]:
    """
    Parse a general-purpose lab results CSV into normalized rows.

    Required conceptual fields:
    - date/day
    - biomarker/test name
    - value/result
    """
    bio = io.BytesIO(csv_bytes)

    try:
        df = pd.read_csv(bio)
    except UnicodeDecodeError:
        bio.seek(0)
        df = pd.read_csv(bio, encoding="latin-1")

    column_map = _normalize_columns(list(df.columns))
    missing = [key for key in ("day", "biomarker", "value") if key not in column_map]
    if missing:
        raise ValueError(
            "Missing expected lab columns. Need equivalents for: "
            + ", ".join(missing)
        )

    out = pd.DataFrame()
    out["day"] = df[column_map["day"]].map(_coerce_day)
    out["biomarker"] = df[column_map["biomarker"]].astype(str).str.strip()
    out["value"] = df[column_map["value"]].map(_to_float)

    out["collected_at"] = (
        df[column_map["collected_at"]].map(_coerce_datetime)
        if "collected_at" in column_map
        else None
    )
    out["unit"] = (
        df[column_map["unit"]].astype(str).str.strip()
        if "unit" in column_map
        else None
    )
    out["notes"] = (
        df[column_map["notes"]].astype(str).str.strip()
        if "notes" in column_map
        else None
    )
    out["source"] = (
        df[column_map["source"]].astype(str).str.strip()
        if "source" in column_map
        else source_default
    )

    out = out[out["day"].notna()]
    out = out[out["biomarker"].notna() & (out["biomarker"] != "")]
    out = out[out["value"].notna()]

    return out.to_dict(orient="records")
