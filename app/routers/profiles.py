import io
import csv
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc
from pydantic import BaseModel

from app.database import get_db
from app.models import Profile, User
from app.middleware.auth_middleware import get_current_user, require_roles
from app.middleware.api_version import require_api_version

router = APIRouter()

VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
VALID_ORDERS = {"asc", "desc"}
VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}
VALID_GENDERS = {"male", "female"}

COUNTRY_MAP = {
    "nigeria": "NG", "ghana": "GH", "kenya": "KE", "ethiopia": "ET",
    "tanzania": "TZ", "uganda": "UG", "senegal": "SN", "mali": "ML",
    "niger": "NE", "burkina faso": "BF", "guinea": "GN", "benin": "BJ",
    "togo": "TG", "sierra leone": "SL", "liberia": "LR", "ivory coast": "CI",
    "cote d'ivoire": "CI", "cameroon": "CM", "angola": "AO", "mozambique": "MZ",
    "zambia": "ZM", "zimbabwe": "ZW", "malawi": "MW", "rwanda": "RW",
    "burundi": "BI", "somalia": "SO", "sudan": "SD", "south sudan": "SS",
    "chad": "TD", "central african republic": "CF", "democratic republic of congo": "CD",
    "congo": "CG", "gabon": "GA", "equatorial guinea": "GQ", "namibia": "NA",
    "botswana": "BW", "lesotho": "LS", "swaziland": "SZ", "eswatini": "SZ",
    "madagascar": "MG", "mauritius": "MU", "seychelles": "SC", "comoros": "KM",
    "djibouti": "DJ", "eritrea": "ER", "egypt": "EG", "libya": "LY",
    "tunisia": "TN", "algeria": "DZ", "morocco": "MA", "mauritania": "MR",
    "gambia": "GM", "guinea-bissau": "GW", "cape verde": "CV", "sao tome": "ST",
    "south africa": "ZA", "haiti": "HT", "jamaica": "JM",
    "trinidad": "TT", "barbados": "BB", "usa": "US", "united states": "US",
    "uk": "GB", "united kingdom": "GB", "canada": "CA", "australia": "AU",
    "france": "FR", "germany": "DE", "italy": "IT", "spain": "ES",
    "brazil": "BR", "india": "IN", "china": "CN", "japan": "JP",
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def classify_age_group(age: int) -> str:
    if age < 13:
        return "child"
    elif age < 18:
        return "teenager"
    elif age < 60:
        return "adult"
    else:
        return "senior"


def format_profile(p: Profile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "gender": p.gender,
        "gender_probability": p.gender_probability,
        "age": p.age,
        "age_group": p.age_group,
        "country_id": p.country_id,
        "country_name": p.country_name,
        "country_probability": p.country_probability,
        "created_at": p.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if p.created_at else None,
    }


def build_pagination_links(request: Request, page: int, limit: int, total: int) -> dict:
    base = str(request.url).split("?")[0]
    params = dict(request.query_params)
    params.pop("page", None)
    params.pop("limit", None)
    base_params = "&".join(f"{k}={v}" for k, v in params.items())
    sep = "&" if base_params else ""
    total_pages = (total + limit - 1) // limit

    def make_url(p):
        return f"{base}?{base_params}{sep}page={p}&limit={limit}"

    return {
        "self": make_url(page),
        "next": make_url(page + 1) if page < total_pages else None,
        "prev": make_url(page - 1) if page > 1 else None,
    }


def apply_filters(query, gender, age_group, country_id, min_age, max_age,
                  min_gender_probability, min_country_probability):
    if gender:
        query = query.filter(Profile.gender == gender)
    if age_group:
        query = query.filter(Profile.age_group == age_group)
    if country_id:
        query = query.filter(Profile.country_id == country_id.upper())
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    if min_gender_probability is not None:
        query = query.filter(Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(Profile.country_probability >= min_country_probability)
    return query


def parse_natural_language(q: str) -> Optional[dict]:
    import re
    q_lower = q.lower().strip()
    if not q_lower:
        return None

    filters = {}
    matched_something = False

    if "male and female" in q_lower or "female and male" in q_lower:
        matched_something = True
    elif "female" in q_lower or "woman" in q_lower or "women" in q_lower or "girls" in q_lower:
        filters["gender"] = "female"
        matched_something = True
    elif "male" in q_lower or "man" in q_lower or "men" in q_lower or "boys" in q_lower:
        filters["gender"] = "male"
        matched_something = True

    if "teenager" in q_lower or "teen" in q_lower:
        filters["age_group"] = "teenager"
        matched_something = True
    elif "child" in q_lower or "children" in q_lower or "kids" in q_lower:
        filters["age_group"] = "child"
        matched_something = True
    elif "senior" in q_lower or "elderly" in q_lower or "old people" in q_lower:
        filters["age_group"] = "senior"
        matched_something = True
    elif "adult" in q_lower or "adults" in q_lower:
        filters["age_group"] = "adult"
        matched_something = True
    elif "young" in q_lower or "youth" in q_lower:
        filters["min_age"] = 16
        filters["max_age"] = 24
        matched_something = True

    above_match = re.search(r"(?:above|over|older than)\s+(\d+)", q_lower)
    if above_match:
        filters["min_age"] = int(above_match.group(1))
        matched_something = True

    below_match = re.search(r"(?:below|under|younger than)\s+(\d+)", q_lower)
    if below_match:
        filters["max_age"] = int(below_match.group(1))
        matched_something = True

    between_match = re.search(r"between\s+(\d+)\s+and\s+(\d+)", q_lower)
    if between_match:
        filters["min_age"] = int(between_match.group(1))
        filters["max_age"] = int(between_match.group(2))
        matched_something = True

    country_match = re.search(
        r"(?:from|in)\s+([a-z\s\-']+?)(?:\s+(?:above|below|over|under|between|aged|who|with|and)|$)",
        q_lower
    )
    if country_match:
        country_str = country_match.group(1).strip()
        if country_str in COUNTRY_MAP:
            filters["country_id"] = COUNTRY_MAP[country_str]
            matched_something = True
        else:
            for country_name, code in COUNTRY_MAP.items():
                if country_name in country_str or country_str in country_name:
                    filters["country_id"] = code
                    matched_something = True
                    break

    return filters if matched_something else None


async def fetch_all(name: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        results = await asyncio.gather(
            client.get("https://api.genderize.io/", params={"name": name}),
            client.get("https://api.agify.io/", params={"name": name}),
            client.get("https://api.nationalize.io/", params={"name": name}),
            return_exceptions=True,
        )
    return results


COUNTRY_CODE_TO_NAME = {v: k.title() for k, v in COUNTRY_MAP.items()}


# ── GET /api/users/me ──
@router.get("/users/me", dependencies=[Depends(require_api_version)])
async def get_me(
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    return JSONResponse(status_code=200, content={
        "status": "success",
        "data": {
            "id": current_user.id,
            "github_id": current_user.github_id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if current_user.created_at else None,
        }
    })


# ── POST /api/profiles (admin only) ──
class CreateProfileRequest(BaseModel):
    name: str


@router.post("/profiles", dependencies=[Depends(require_api_version)])
async def create_profile(
    body: CreateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    existing = db.query(Profile).filter(Profile.name == name).first()
    if existing:
        return JSONResponse(status_code=200, content={
            "status": "success",
            "message": "Profile already exists",
            "data": format_profile(existing),
        })

    results = await fetch_all(name)

    for r in results:
        if isinstance(r, Exception):
            raise HTTPException(status_code=502, detail="Failed to reach external API")

    gender_res, age_res, nation_res = results

    try:
        gender_data = gender_res.json()
        age_data = age_res.json()
        nation_data = nation_res.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid response from external API")

    if gender_data.get("gender") is None or gender_data.get("count", 0) == 0:
        raise HTTPException(status_code=422, detail="Insufficient gender data for this name")

    if age_data.get("age") is None:
        raise HTTPException(status_code=422, detail="Insufficient age data for this name")

    countries = nation_data.get("country", [])
    if not countries:
        raise HTTPException(status_code=422, detail="Insufficient nationality data for this name")

    gender = gender_data["gender"]
    gender_probability = gender_data["probability"]
    age = age_data["age"]
    age_group = classify_age_group(age)
    top_country = max(countries, key=lambda c: c["probability"])
    country_id = top_country["country_id"]
    country_probability = top_country["probability"]
    country_name = COUNTRY_CODE_TO_NAME.get(country_id, country_id)

    profile = Profile(
        id=str(uuid.uuid4()),
        name=name,
        gender=gender,
        gender_probability=gender_probability,
        age=age,
        age_group=age_group,
        country_id=country_id,
        country_name=country_name,
        country_probability=country_probability,
        created_at=utcnow(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return JSONResponse(status_code=201, content={
        "status": "success",
        "data": format_profile(profile),
    })


# ── DELETE /api/profiles/{id} (admin only) ──
@router.delete("/profiles/{profile_id}", dependencies=[Depends(require_api_version)])
def delete_profile(
    profile_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db.delete(profile)
    db.commit()

    return JSONResponse(status_code=200, content={
        "status": "success",
        "message": "Profile deleted successfully",
    })


# ── GET /api/profiles ──
@router.get("/profiles", dependencies=[Depends(require_api_version)])
def get_profiles(
    request: Request,
    gender: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    country_id: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None),
    max_age: Optional[int] = Query(None),
    min_gender_probability: Optional[float] = Query(None),
    min_country_probability: Optional[float] = Query(None),
    sort_by: Optional[str] = Query(None),
    order: Optional[str] = Query("asc"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    if gender and gender not in VALID_GENDERS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid query parameters"})
    if age_group and age_group not in VALID_AGE_GROUPS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid query parameters"})
    if sort_by and sort_by not in VALID_SORT_FIELDS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid query parameters"})
    if order and order not in VALID_ORDERS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid query parameters"})
    if min_age is not None and min_age < 0:
        return JSONResponse(status_code=422, content={"status": "error", "message": "Invalid query parameters"})
    if max_age is not None and max_age < 0:
        return JSONResponse(status_code=422, content={"status": "error", "message": "Invalid query parameters"})
    if min_age is not None and max_age is not None and min_age > max_age:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid query parameters"})

    query = db.query(Profile)
    query = apply_filters(query, gender, age_group, country_id, min_age, max_age,
                          min_gender_probability, min_country_probability)

    if sort_by:
        sort_col = getattr(Profile, sort_by)
        query = query.order_by(asc(sort_col) if order == "asc" else desc(sort_col))

    total = query.count()
    total_pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    profiles = query.offset(offset).limit(limit).all()

    return JSONResponse(status_code=200, content={
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_pagination_links(request, page, limit, total),
        "data": [format_profile(p) for p in profiles],
    })


# ── GET /api/profiles/search ──
@router.get("/profiles/search", dependencies=[Depends(require_api_version)])
def search_profiles(
    request: Request,
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    if not q or not q.strip():
        return JSONResponse(status_code=400, content={"status": "error", "message": "Missing or empty query"})

    filters = parse_natural_language(q)

    if filters is None:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Unable to interpret query"})

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender=filters.get("gender"),
        age_group=filters.get("age_group"),
        country_id=filters.get("country_id"),
        min_age=filters.get("min_age"),
        max_age=filters.get("max_age"),
        min_gender_probability=None,
        min_country_probability=None,
    )

    total = query.count()
    total_pages = (total + limit - 1) // limit
    offset = (page - 1) * limit
    profiles = query.offset(offset).limit(limit).all()

    return JSONResponse(status_code=200, content={
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_pagination_links(request, page, limit, total),
        "data": [format_profile(p) for p in profiles],
    })


# ── GET /api/profiles/export ──
@router.get("/profiles/export", dependencies=[Depends(require_api_version)])
def export_profiles(
    gender: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    country_id: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None),
    max_age: Optional[int] = Query(None),
    min_gender_probability: Optional[float] = Query(None),
    min_country_probability: Optional[float] = Query(None),
    sort_by: Optional[str] = Query(None),
    order: Optional[str] = Query("asc"),
    format: Optional[str] = Query("csv"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    if format != "csv":
        raise HTTPException(status_code=400, detail="Only format=csv is supported")

    query = db.query(Profile)
    query = apply_filters(query, gender, age_group, country_id, min_age, max_age,
                          min_gender_probability, min_country_probability)

    if sort_by and sort_by in VALID_SORT_FIELDS:
        sort_col = getattr(Profile, sort_by)
        query = query.order_by(asc(sort_col) if order == "asc" else desc(sort_col))

    profiles = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at",
    ])

    for p in profiles:
        writer.writerow([
            p.id, p.name, p.gender, p.gender_probability,
            p.age, p.age_group, p.country_id, p.country_name,
            p.country_probability,
            p.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if p.created_at else "",
        ])

    output.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="profiles_{timestamp}.csv"'
        },
    )


# ── GET /api/profiles/{id} ──
@router.get("/profiles/{profile_id}", dependencies=[Depends(require_api_version)])
def get_profile(
    profile_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "analyst")),
):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return JSONResponse(status_code=200, content={
        "status": "success",
        "data": format_profile(profile),
    })
