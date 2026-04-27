import os
import secrets
import hashlib
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import User
from app.services.github_service import exchange_code_for_token, get_github_user
from app.services.token_service import (
    generate_access_token,
    generate_refresh_token,
    rotate_refresh_token,
    invalidate_refresh_token,
)
from app.middleware.auth_middleware import get_current_user, get_db
from pydantic import BaseModel


router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory state store — good enough for single-instance deployment
# For multi-instance, swap this for Redis
pending_states: dict = {}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────────
# GET /auth/github  — kick off OAuth
# ─────────────────────────────────────────────
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

    # Clean up states older than 10 minutes
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


# ─────────────────────────────────────────────
# GET /auth/github/callback
# ─────────────────────────────────────────────
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

        # Upsert user
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

        # ── CLI flow: redirect to local server with tokens in query params ──
        if state_data["is_cli"]:
            cli_redirect = (
                f"{os.getenv('CLI_REDIRECT_BASE')}:{state_data['cli_port']}/callback"
                f"?access_token={access_token}&refresh_token={refresh_token}&username={user.username}"
            )
            return RedirectResponse(cli_redirect)

        # ── Web flow: set HTTP-only cookies ──
        response = RedirectResponse(url=f"{os.getenv('FRONTEND_URL')}/dashboard")
        cookie_opts = dict(
            httponly=True,
            secure=os.getenv("NODE_ENV") == "production",
            samesite="strict",
        )
        response.set_cookie("access_token", access_token, max_age=180, **cookie_opts)
        response.set_cookie("refresh_token", refresh_token, max_age=300, **cookie_opts)
        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")


# ─────────────────────────────────────────────
# POST /auth/refresh
# ─────────────────────────────────────────────
class RefreshRequest(BaseModel):
    refresh_token: str = None


@router.post("/refresh")
async def refresh_tokens(
    request: Request,
    body: RefreshRequest = None,
    db: Session = Depends(get_db),
):
    # Accept from body (CLI) or cookie (web)
    raw_token = (body.refresh_token if body else None) or request.cookies.get("refresh_token")

    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    try:
        access_token, new_refresh_token, user = rotate_refresh_token(raw_token, db)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    response = JSONResponse({
        "status": "success",
        "access_token": access_token,
        "refresh_token": new_refresh_token,
    })

    # If web (cookie present), also update cookies
    if request.cookies.get("refresh_token"):
        cookie_opts = dict(httponly=True, secure=os.getenv("NODE_ENV") == "production", samesite="strict")
        response.set_cookie("access_token", access_token, max_age=180, **cookie_opts)
        response.set_cookie("refresh_token", new_refresh_token, max_age=300, **cookie_opts)

    return response


# ─────────────────────────────────────────────
# POST /auth/logout
# ─────────────────────────────────────────────
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

    response = JSONResponse({"status": "success", "message": "Logged out successfully"})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response


# ─────────────────────────────────────────────
# GET /auth/me
# ─────────────────────────────────────────────
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