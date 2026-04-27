import time
import logging
from contextlib import asynccontextmanager
import slowapi

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.database import engine
from app import models
from app.routers import profiles
from app.routers import auth

# ── Create all tables (users, refresh_tokens, profiles) ──
models.Base.metadata.create_all(bind=engine)

# ── Logging setup ──
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── Rate limiter ──
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Insighta Labs+ — Intelligence Query Engine")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to FRONTEND_URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request logging middleware ──
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 2)
    logger.info(f"{request.method} {request.url.path} {response.status_code} {duration}ms")
    return response

# ── Routers ──
app.include_router(auth.router)                    # /auth/*
app.include_router(profiles.router, prefix="/api") # /api/profiles/*

# ── Health check ──
@app.get("/")
def root():
    return {"status": "ok", "message": "Insighta Labs+ is running"}

@app.get("/health")
def health():
    return {"status": "ok"}