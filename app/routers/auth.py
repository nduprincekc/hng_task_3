import os
import secrets
import uuid
import json
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import User, Profile
from app.services.github_service import exchange_code_for_token, get_github_user
from app.services.token_service import (
    generate_access_token,
    generate_refresh_token,
    rotate_refresh_token,
    invalidate_refresh_token,
)
from app.middleware.auth_middleware import get_current_user, get_db
from pydantic import BaseModel
import httpx

router = APIRouter(prefix="/auth", tags=["auth"])

pending_states: dict = {}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/github")
async def redirect_to_github(
    request: Request,
    state: str = None,
    code_challenge: str = None,
    code_challenge_method: str = None,
    port: int = None,
):
    is_cli = code_challenge is not None
    oauth_state = state or secrets.token_hex(16)

    pending_states[oauth_state] = {
        "is_cli": is_cli,
        "code_challenge": code_challenge,
        "cli_port": port,
        "created_at": datetime.now(timezone.utc).timestamp(),
    }

    now = datetime.now(timezone.utc).timestamp()
    stale = [k for k, v in pending_states.items() if now - v["created_at"] > 600]
    for k in stale:
        del pending_states[k]

    if is_cli:
        redirect_uri = f"{os.getenv('CLI_REDIRECT_BASE')}:{port}/callback"
    else:
        redirect_uri = os.getenv("GITHUB_REDIRECT_URI")

    params = {
        "client_id": os.getenv("GITHUB_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": oauth_state,
    }

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    code_verifier: str = None,
    db: Session = Depends(get_db),
):
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    state_data = pending_states.get(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    del pending_states[state]

    try:
        if state_data["is_cli"]:
            redirect_uri = f"{os.getenv('CLI_REDIRECT_BASE')}:{state_data['cli_port']}/callback"
        else:
            redirect_uri = os.getenv("GITHUB_REDIRECT_URI")

        github_token = await exchange_code_for_token(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

        github_user = await get_github_user(github_token)

        user = db.query(User).filter(User.github_id == str(github_user["id"])).first()

        if user:
            user.username = github_user["login"]
            user.email = github_user.get("email")
            user.avatar_url = github_user.get("avatar_url")
            user.last_login_at = utcnow()
        else:
            user = User(
                id=str(uuid.uuid4()),
                github_id=str(github_user["id"]),
                username=github_user["login"],
                email=github_user.get("email"),
                avatar_url=github_user.get("avatar_url"),
                role="analyst",
                is_active=True,
                last_login_at=utcnow(),
            )
            db.add(user)

        db.commit()
        db.refresh(user)

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account is disabled")

        access_token = generate_access_token(user)
        refresh_token = generate_refresh_token(user.id, db)

        # CLI flow
        if state_data["is_cli"]:
            cli_redirect = (
                f"{os.getenv('CLI_REDIRECT_BASE')}:{state_data['cli_port']}/callback"
                f"?access_token={access_token}&refresh_token={refresh_token}&username={user.username}"
            )
            return RedirectResponse(cli_redirect)

        # Web flow
        frontend_url = os.getenv("FRONTEND_URL")
        return RedirectResponse(
            url=f"{frontend_url}/dashboard?access_token={access_token}&refresh_token={refresh_token}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")


class RefreshRequest(BaseModel):
    refresh_token: str = None


@router.post("/refresh")
async def refresh_tokens(
    request: Request,
    body: RefreshRequest = None,
    db: Session = Depends(get_db),
):
    raw_token = (body.refresh_token if body else None) or request.cookies.get("refresh_token")

    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    try:
        access_token, new_refresh_token, user = rotate_refresh_token(raw_token, db)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    return JSONResponse({
        "status": "success",
        "access_token": access_token,
        "refresh_token": new_refresh_token,
    })


class LogoutRequest(BaseModel):
    refresh_token: str = None


@router.post("/logout")
async def logout(
    request: Request,
    body: LogoutRequest = None,
    db: Session = Depends(get_db),
):
    raw_token = (body.refresh_token if body else None) or request.cookies.get("refresh_token")

    if raw_token:
        invalidate_refresh_token(raw_token, db)

    return JSONResponse({"status": "success", "message": "Logged out successfully"})


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "status": "success",
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
        },
    }


@router.post("/seed-db")
async def seed_database(db: Session = Depends(get_db)):
    seed_file = "seed_profiles.json"

    if not os.path.exists(seed_file):
        raise HTTPException(status_code=404, detail="Seed file not found")

    with open(seed_file, "r") as f:
        data = json.load(f)

    COUNTRY_CODE_TO_NAME = {
        "NG": "Nigeria", "GH": "Ghana", "KE": "Kenya", "ZA": "South Africa",
        "US": "United States", "GB": "United Kingdom", "FR": "France",
        "DE": "Germany", "IN": "India", "BR": "Brazil", "CA": "Canada",
        "AU": "Australia", "JP": "Japan", "CN": "China", "IT": "Italy",
        "ES": "Spain", "MX": "Mexico", "RW": "Rwanda", "UG": "Uganda",
        "TZ": "Tanzania", "ET": "Ethiopia", "EG": "Egypt", "MA": "Morocco",
        "SN": "Senegal", "CI": "Ivory Coast", "CM": "Cameroon", "MG": "Madagascar",
    }

    def classify_age_group(age):
        if age < 13: return "child"
        elif age < 18: return "teenager"
        elif age < 60: return "adult"
        else: return "senior"

    # Handle list of dicts (already full profiles)
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        count = 0
        skipped = 0
        for p in data:
            existing = db.query(Profile).filter(Profile.name == p["name"]).first()
            if existing:
                skipped += 1
                continue
            profile = Profile(
                id=p.get("id", str(uuid.uuid4())),
                name=p["name"],
                gender=p["gender"],
                gender_probability=p["gender_probability"],
                age=p["age"],
                age_group=p["age_group"],
                country_id=p["country_id"],
                country_name=p["country_name"],
                country_probability=p["country_probability"],
            )
            db.add(profile)
            count += 1
        db.commit()
        return {"status": "success", "inserted": count, "skipped": skipped}

    # Handle list of names
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], str):
        names = data
    else:
        raise HTTPException(status_code=400, detail="Unknown seed file format")

    count = 0
    skipped = 0
    errors = 0

    async def process_name(name: str):
        existing = db.query(Profile).filter(Profile.name == name).first()
        if existing:
            return "skipped"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                results = await asyncio.gather(
                    client.get("https://api.genderize.io/", params={"name": name}),
                    client.get("https://api.agify.io/", params={"name": name}),
                    client.get("https://api.nationalize.io/", params={"name": name}),
                    return_exceptions=True,
                )

            if any(isinstance(r, Exception) for r in results):
                return "error"

            gender_r, age_r, nation_r = results
            gender_data = gender_r.json()
            age_data = age_r.json()
            nation_data = nation_r.json()

            if not gender_data.get("gender") or not age_data.get("age"):
                return "error"

            countries = nation_data.get("country", [])
            if not countries:
                return "error"

            top_country = max(countries, key=lambda c: c["probability"])
            country_id = top_country["country_id"]

            profile = Profile(
                id=str(uuid.uuid4()),
                name=name,
                gender=gender_data["gender"],
                gender_probability=gender_data["probability"],
                age=age_data["age"],
                age_group=classify_age_group(age_data["age"]),
                country_id=country_id,
                country_name=COUNTRY_CODE_TO_NAME.get(country_id, country_id),
                country_probability=top_country["probability"],
            )
            db.add(profile)
            db.commit()
            return "inserted"

        except Exception:
            return "error"

    # Process in batches of 10
    batch_size = 10
    for i in range(0, min(len(names), 200), batch_size):
        batch = names[i:i + batch_size]
        results = await asyncio.gather(*[process_name(n) for n in batch])
        for r in results:
            if r == "inserted": count += 1
            elif r == "skipped": skipped += 1
            else: errors += 1

    return {
        "status": "success",
        "inserted": count,
        "skipped": skipped,
        "errors": errors,
        "total_processed": count + skipped + errors,
    }