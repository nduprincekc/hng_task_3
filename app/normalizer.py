"""
Query normalization — converts free-form keyword queries into a canonical
filter dict. Two queries expressing the same intent produce the same dict,
and therefore the same cache key.

No AI, fully deterministic, rule-based.
"""

import re
from typing import Optional


GENDER_ALIASES: dict[str, str] = {
    "male": "male", "males": "male", "man": "male", "men": "male",
    "boy": "male", "boys": "male", "m": "male",
    "female": "female", "females": "female", "woman": "female", "women": "female",
    "girl": "female", "girls": "female", "f": "female",
    "other": "other", "others": "other", "nonbinary": "other", "non-binary": "other",
}

# Maps age-group keywords to (age_min, age_max)
AGE_RANGE_ALIASES: dict[str, tuple[int, int]] = {
    "young": (16, 24),       # matches Stage 3 age_group: "young"
    "youth": (16, 24),
    "adult": (25, 59),       # matches Stage 3 age_group: "adult"
    "adults": (25, 59),
    "senior": (60, 120),     # matches Stage 3 age_group: "senior"
    "seniors": (60, 120),
    "elderly": (60, 120),
    "teenager": (13, 17),
    "teen": (13, 17),
    "child": (0, 12),
    "children": (0, 12),
    "middle-aged": (35, 55),
    "middle aged": (35, 55),
}

# Maps age-group keywords to the age_group string stored in DB
AGE_GROUP_LABEL: dict[str, str] = {
    "young": "young", "youth": "young",
    "adult": "adult", "adults": "adult",
    "senior": "senior", "seniors": "senior", "elderly": "senior",
    "teenager": "teenager", "teen": "teenager",
    "child": "child", "children": "child",
}

# Country name → ISO code mapping (common ones)
COUNTRY_TO_ISO: dict[str, str] = {
    "nigeria": "NG", "ghana": "GH", "kenya": "KE", "south africa": "ZA",
    "egypt": "EG", "ethiopia": "ET", "uganda": "UG", "tanzania": "TZ",
    "rwanda": "RW", "cameroon": "CM", "senegal": "SN", "ivory coast": "CI",
    "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB",
    "france": "FR", "germany": "DE", "india": "IN", "china": "CN",
    "brazil": "BR", "canada": "CA", "australia": "AU",
}

# Demonyms → canonical country name
COUNTRY_ALIASES: dict[str, str] = {
    "nigerian": "nigeria", "nigerians": "nigeria",
    "ghanaian": "ghana", "ghanaians": "ghana",
    "kenyan": "kenya", "kenyans": "kenya",
    "south african": "south africa", "south africans": "south africa",
    "egyptian": "egypt", "egyptians": "egypt",
    "american": "united states", "americans": "united states",
    "british": "united kingdom", "english": "united kingdom",
    "french": "france", "german": "germany", "germans": "germany",
    "indian": "india", "indians": "india",
    "chinese": "china", "brazilian": "brazil", "brazilians": "brazil",
    "ethiopian": "ethiopia", "ugandan": "uganda",
    "tanzanian": "tanzania", "rwandan": "rwanda",
}


def parse_age_range(text: str) -> Optional[tuple[Optional[int], Optional[int]]]:
    text = text.lower()

    # "between ages X and Y" / "between X and Y"
    m = re.search(r'between\s+(?:ages?\s+)?(\d+)\s+and\s+(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # "aged X-Y" / "X–Y"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # "over X" / "above X" / "older than X"
    m = re.search(r'(?:over|above|older than|>\s*)(\d+)', text)
    if m:
        return int(m.group(1)), None

    # "under X" / "below X" / "younger than X"
    m = re.search(r'(?:under|below|younger than|less than|<\s*)(\d+)', text)
    if m:
        return None, int(m.group(1))

    # "age X" / "aged X"
    m = re.search(r'age[d]?\s+(\d+)', text)
    if m:
        age = int(m.group(1))
        return age, age

    return None


def normalize_query(raw: str) -> dict:
    """
    Convert a raw keyword query into a canonical filter dict.

    "Nigerian females between ages 20 and 45"
    → {"gender": "female", "country_name": "nigeria", "country_id": "NG", "age_min": 20, "age_max": 45}

    "Women aged 20–45 living in Nigeria"
    → same output → same cache key
    """
    result: dict = {}
    text = raw.strip()
    lower = text.lower()

    # --- Gender ---
    for alias, canonical in GENDER_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', lower):
            result["gender"] = canonical
            break

    # --- Age group label (young, adult, senior) ---
    for alias, label in AGE_GROUP_LABEL.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', lower):
            result["age_group"] = label
            break

    # --- Age group range override ---
    for alias, (mn, mx) in AGE_RANGE_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', lower):
            result["age_min"] = mn
            result["age_max"] = mx
            break

    # --- Numeric age range (always overrides keyword range) ---
    age_range = parse_age_range(text)
    if age_range:
        mn, mx = age_range
        if mn is not None:
            result["age_min"] = mn
        if mx is not None:
            result["age_max"] = mx
        # Clear age_group if explicit numeric range given
        result.pop("age_group", None)

    # --- Country ---
    matched_country = None

    # Check demonyms first (multi-word first)
    for alias, canonical in sorted(COUNTRY_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in lower:
            matched_country = canonical
            break

    # Check plain country names
    if not matched_country:
        for name in sorted(COUNTRY_TO_ISO.keys(), key=lambda x: -len(x)):
            if name in lower:
                matched_country = name
                break

    # Extract from "in/from <Country>" pattern
    if not matched_country:
        m = re.search(
            r'(?:in|from|living in|based in)\s+([A-Za-z][a-zA-Z\s]+?)(?:\s*(?:between|aged|over|under|age\b)|$|,)',
            text
        )
        if m:
            matched_country = m.group(1).strip().lower()

    if matched_country:
        result["country_name"] = matched_country
        iso = COUNTRY_TO_ISO.get(matched_country)
        if iso:
            result["country_id"] = iso

    return result


def filters_to_sql_conditions(filters: dict):
    """Convert normalized filter dict to SQLAlchemy WHERE conditions."""
    from app.models import Profile
    conditions = []

    if "gender" in filters:
        conditions.append(Profile.gender == filters["gender"])

    if "age_group" in filters:
        conditions.append(Profile.age_group == filters["age_group"])

    if "age_min" in filters:
        conditions.append(Profile.age >= filters["age_min"])

    if "age_max" in filters:
        conditions.append(Profile.age <= filters["age_max"])

    # Prefer country_id (indexed, exact) over country_name
    if "country_id" in filters:
        conditions.append(Profile.country_id == filters["country_id"])
    elif "country_name" in filters:
        conditions.append(Profile.country_name.ilike(filters["country_name"]))

    return conditions