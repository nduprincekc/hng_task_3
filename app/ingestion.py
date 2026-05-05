"""
CSV ingestion — streaming, chunked, non-blocking.

CSV expected columns: name, age, gender, country_id, country_name
(country_name and optional columns are handled gracefully if missing)
"""

import csv
import io
import logging
from collections import defaultdict
from uuid6 import uuid7
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Profile
from app.cache import cache_invalidate_prefix

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
VALID_GENDERS = {"male", "female", "other"}
REQUIRED_FIELDS = {"name", "age", "gender"}

AGE_GROUP_MAP = {
    range(0, 13): "child",
    range(13, 18): "teenager",
    range(16, 25): "young",
    range(25, 60): "adult",
    range(60, 151): "senior",
}


def get_age_group(age: int) -> str:
    for r, label in AGE_GROUP_MAP.items():
        if age in r:
            return label
    return "adult"


def validate_row(row: dict) -> tuple[dict | None, str | None]:
    missing = [f for f in REQUIRED_FIELDS if not row.get(f, "").strip()]
    if missing:
        return None, "missing_fields"

    name = row["name"].strip()
    gender = row["gender"].strip().lower()

    try:
        age = int(row["age"].strip())
        if age < 0 or age > 150:
            return None, "invalid_age"
    except (ValueError, TypeError):
        return None, "invalid_age"

    if gender not in VALID_GENDERS:
        return None, "invalid_gender"

    if not name:
        return None, "missing_fields"

    country_id = row.get("country_id", "").strip().upper() or None
    country_name = row.get("country_name", "").strip().lower() or None

    return {
        "id": str(uuid7()),
        "name": name,
        "age": age,
        "age_group": row.get("age_group", "").strip() or get_age_group(age),
        "gender": gender,
        "gender_probability": float(row.get("gender_probability", 0) or 0),
        "country_id": country_id,
        "country_name": country_name,
        "country_probability": float(row.get("country_probability", 0) or 0),
    }, None


async def ingest_csv_stream(file_content: bytes, db: Session) -> dict:
    total_rows = 0
    inserted = 0
    skipped = 0
    reasons: dict[str, int] = defaultdict(int)

    try:
        text = file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return {"status": "error", "detail": "CSV is empty or has no headers",
                "total_rows": 0, "inserted": 0, "skipped": 0, "reasons": {}}

    fieldnames_lower = {f.strip().lower() for f in reader.fieldnames}
    missing_headers = REQUIRED_FIELDS - fieldnames_lower
    if missing_headers:
        return {"status": "error", "detail": f"Missing required columns: {missing_headers}",
                "total_rows": 0, "inserted": 0, "skipped": 0, "reasons": {}}

    chunk: list[dict] = []

    def flush_chunk():
        nonlocal inserted, skipped
        if not chunk:
            return
        try:
            stmt = pg_insert(Profile).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
            result = db.execute(stmt)
            db.commit()
            rows_inserted = result.rowcount
            rows_duped = len(chunk) - rows_inserted
            inserted += rows_inserted
            skipped += rows_duped
            reasons["duplicate_name"] += rows_duped
        except Exception as e:
            db.rollback()
            logger.error(f"Chunk insert failed: {e}")
            skipped += len(chunk)
            reasons["db_error"] += len(chunk)
        chunk.clear()

    for row in reader:
        total_rows += 1

        normalized = {k.strip().lower(): v for k, v in row.items() if k}

        if len(normalized) < len(REQUIRED_FIELDS):
            skipped += 1
            reasons["malformed_row"] += 1
            continue

        clean, reason = validate_row(normalized)
        if not clean:
            skipped += 1
            reasons[reason] += 1
            continue

        chunk.append(clean)
        if len(chunk) >= CHUNK_SIZE:
            flush_chunk()

    flush_chunk()
    cache_invalidate_prefix("query")

    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "reasons": dict(reasons),
    }