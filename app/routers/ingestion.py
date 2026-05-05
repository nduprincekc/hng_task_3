from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_
from typing import Optional

from app.database import get_db
from app.models import Profile
from app.normalizer import normalize_query, filters_to_sql_conditions
from app.cache import make_cache_key, cache_get, cache_set

router = APIRouter(prefix="/api", tags=["query"])


@router.get("/profiles")
def list_profiles(
    q: Optional[str] = Query(None, description="e.g. 'young males in Nigeria'"),
    gender: Optional[str] = Query(None),
    country_id: Optional[str] = Query(None, description="ISO code e.g. NG"),
    country_name: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None, description="young | adult | senior | teenager | child"),
    age_min: Optional[int] = Query(None),
    age_max: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # Build normalized filter dict
    if q:
        filters = normalize_query(q)
        # Explicit params override parsed values
        if gender:
            filters["gender"] = gender.lower().strip()
        if country_id:
            filters["country_id"] = country_id.upper().strip()
        if country_name:
            filters["country_name"] = country_name.lower().strip()
        if age_group:
            filters["age_group"] = age_group.lower().strip()
        if age_min is not None:
            filters["age_min"] = age_min
        if age_max is not None:
            filters["age_max"] = age_max
    else:
        filters = {}
        if gender:
            filters["gender"] = gender.lower().strip()
        if country_id:
            filters["country_id"] = country_id.upper().strip()
        if country_name:
            filters["country_name"] = country_name.lower().strip()
        if age_group:
            filters["age_group"] = age_group.lower().strip()
        if age_min is not None:
            filters["age_min"] = age_min
        if age_max is not None:
            filters["age_max"] = age_max

    cache_data = {**filters, "page": page, "page_size": page_size}
    cache_key = make_cache_key("query", cache_data)

    cached = cache_get(cache_key)
    if cached:
        cached["cache"] = "hit"
        return cached

    conditions = filters_to_sql_conditions(filters)
    base_query = select(Profile)
    count_query = select(func.count()).select_from(Profile)

    if conditions:
        base_query = base_query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    total = db.execute(count_query).scalar()
    offset = (page - 1) * page_size
    profiles = db.execute(
        base_query.order_by(Profile.created_at.desc()).offset(offset).limit(page_size)
    ).scalars().all()

    result = {
        "cache": "miss",
        "total": total,
        "page": page,
        "page_size": page_size,
        "filters_applied": filters,
        "data": [
            {
                "id": str(p.id),
                "name": p.name,
                "age": p.age,
                "age_group": p.age_group,
                "gender": p.gender,
                "gender_probability": p.gender_probability,
                "country_id": p.country_id,
                "country_name": p.country_name,
                "country_probability": p.country_probability,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in profiles
        ],
    }

    cache_set(cache_key, result)
    return result


@router.get("/profiles/stats")
def profile_stats(
    country_id: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    filters = {}
    if country_id:
        filters["country_id"] = country_id.upper().strip()
    if gender:
        filters["gender"] = gender.lower().strip()

    cache_key = make_cache_key("stats", filters)
    cached = cache_get(cache_key)
    if cached:
        return cached

    conditions = filters_to_sql_conditions(filters)
    base = select(Profile)
    if conditions:
        base = base.where(and_(*conditions))
    subq = base.subquery()

    stats = db.execute(
        select(
            func.count(subq.c.id).label("total"),
            func.avg(subq.c.age).label("avg_age"),
            func.min(subq.c.age).label("min_age"),
            func.max(subq.c.age).label("max_age"),
        )
    ).first()

    gender_rows = db.execute(
        select(subq.c.gender, func.count(subq.c.id).label("count"))
        .group_by(subq.c.gender)
        .order_by(func.count(subq.c.id).desc())
    ).all()

    country_rows = db.execute(
        select(subq.c.country_name, func.count(subq.c.id).label("count"))
        .group_by(subq.c.country_name)
        .order_by(func.count(subq.c.id).desc())
        .limit(10)
    ).all()

    age_group_rows = db.execute(
        select(subq.c.age_group, func.count(subq.c.id).label("count"))
        .group_by(subq.c.age_group)
        .order_by(func.count(subq.c.id).desc())
    ).all()

    result = {
        "total": stats.total,
        "avg_age": round(float(stats.avg_age), 1) if stats.avg_age else None,
        "min_age": stats.min_age,
        "max_age": stats.max_age,
        "filters_applied": filters,
        "by_gender": [{"gender": r.gender, "count": r.count} for r in gender_rows],
        "by_age_group": [{"age_group": r.age_group, "count": r.count} for r in age_group_rows],
        "top_countries": [{"country": r.country_name, "count": r.count} for r in country_rows],
    }

    cache_set(cache_key, result, ttl=600)
    return result