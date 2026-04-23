from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from db import init_db, replace_food_logs_for_day, save_import_status
from food_import import parse_netdiary_csv, parse_netdiary_summary_pdf


DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"
IMPORT_NAME = "mynetdiary_auto"


def _default_target_day() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _find_latest_export(search_dir: Path) -> Path:
    candidates = sorted(
        list(search_dir.glob("MyNetDiary*.csv")) + list(search_dir.glob("MyNetDiary*.pdf")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No MyNetDiary CSV or PDF files found in {search_dir}")
    return candidates[0]


def _filter_rows_for_day(rows: list[dict], target_day: str) -> list[dict]:
    filtered: list[dict] = []
    for row in rows:
        if row.get("day") == target_day:
            next_row = dict(row)
            next_row["source"] = "netdiary_auto"
            filtered.append(next_row)
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import yesterday's MyNetDiary rows from the newest CSV export."
    )
    parser.add_argument(
        "--day",
        default=_default_target_day(),
        help="Target day to import in YYYY-MM-DD format. Defaults to yesterday.",
    )
    parser.add_argument(
        "--search-dir",
        default=str(DEFAULT_DOWNLOADS_DIR),
        help="Folder to search for MyNetDiary CSV/PDF exports. Defaults to ~/Downloads.",
    )
    parser.add_argument(
        "--file",
        default="",
        help="Optional explicit MyNetDiary CSV path. Overrides --search-dir when provided.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be imported without changing the database.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow importing the newest file even if its detected report day does not match the target day.",
    )
    args = parser.parse_args()

    target_day = args.day
    export_path = Path(args.file) if args.file else _find_latest_export(Path(args.search_dir).expanduser())
    raw = export_path.read_bytes()
    detected_day = ""
    if export_path.suffix.lower() == ".pdf":
        rows, detected_day = parse_netdiary_summary_pdf(raw, filename=export_path.name)
    else:
        rows, detected_day = parse_netdiary_csv(raw)
    day_rows = _filter_rows_for_day(rows, target_day)

    totals = {
        "calories": round(sum(float(row.get("calories") or 0) for row in day_rows), 1),
        "protein_g": round(sum(float(row.get("protein_g") or 0) for row in day_rows), 1),
        "carbs_g": round(sum(float(row.get("carbs_g") or 0) for row in day_rows), 1),
        "fat_g": round(sum(float(row.get("fat_g") or 0) for row in day_rows), 1),
    }

    result = {
        "file": str(export_path),
        "target_day": target_day,
        "detected_day": detected_day or "",
        "rows_found": len(day_rows),
        "totals": totals,
        "updated": False,
    }

    if detected_day and detected_day != target_day and not args.allow_stale:
        message = (
            f"Newest file appears to be for {detected_day}, but the target day is {target_day}. "
            "No database changes were made."
        )
        result["warning"] = message
        init_db()
        save_import_status(
            import_name=IMPORT_NAME,
            status="stale",
            target_day=target_day,
            detected_day=detected_day,
            source_path=str(export_path),
            source_kind=export_path.suffix.lower().lstrip("."),
            rows_found=len(day_rows),
            rows_inserted=0,
            message=message,
            succeeded=False,
        )
        print(json.dumps(result, indent=2))
        return 1

    if not day_rows:
        message = (
            "No rows for the requested day were found in the CSV. "
            "No database changes were made."
        )
        result["warning"] = message
        init_db()
        save_import_status(
            import_name=IMPORT_NAME,
            status="missing",
            target_day=target_day,
            detected_day=detected_day,
            source_path=str(export_path),
            source_kind=export_path.suffix.lower().lstrip("."),
            rows_found=0,
            rows_inserted=0,
            message=message,
            succeeded=False,
        )
        print(json.dumps(result, indent=2))
        return 1

    if args.dry_run:
        result["warning"] = "Dry run only. No database changes were made."
        print(json.dumps(result, indent=2))
        return 0

    init_db()
    inserted = replace_food_logs_for_day(target_day, day_rows, source="netdiary_auto")
    result["updated"] = True
    result["rows_inserted"] = inserted
    save_import_status(
        import_name=IMPORT_NAME,
        status="success",
        target_day=target_day,
        detected_day=detected_day,
        source_path=str(export_path),
        source_kind=export_path.suffix.lower().lstrip("."),
        rows_found=len(day_rows),
        rows_inserted=inserted,
        message="Imported MyNetDiary data successfully.",
        succeeded=True,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
