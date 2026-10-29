import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api import assets, integrity, projects, reviews, rulesets, video_content
from app.api.auth import current_user, issue_session, token_hash, verify_password
from app.db import get_db
from app.errors import ReviewError, fail
from app.models import LoginSession, User
from app.settings import ROOT, settings


@asynccontextmanager
async def lifespan(app):
    if settings.auth_mode == "session" and len(settings.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET is required; run scripts/bootstrap.py first")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Creator Review API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_boundary(request: Request, call_next):
    request.state.request_id = secrets.token_hex(8)
    content_length = request.headers.get("content-length", "0")
    if not content_length.isdigit() or len(content_length) > 20:
        return JSONResponse(
            {
                "code": "INVALID_INPUT",
                "message": "无效请求长度",
                "details": {},
                "request_id": request.state.request_id,
            },
            status_code=400,
        )
    if int(content_length) > settings.review_max_bytes + 1024 * 1024:
        return JSONResponse(
            {
                "code": "FILE_TOO_LARGE",
                "message": "请求超过上传范围",
                "details": {},
                "request_id": request.state.request_id,
            },
            status_code=413,
        )
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in (
            f"http://{request.headers.get('host')}",
            f"https://{request.headers.get('host')}",
        ):
            return JSONResponse(
                {
                    "code": "INVALID_ORIGIN",
                    "message": "请求来源不匹配",
                    "details": {},
                    "request_id": request.state.request_id,
                },
                status_code=403,
            )
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = (
        exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    )
    return JSONResponse({**detail, "request_id": request.state.request_id}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "code": "INVALID_INPUT",
            "message": "输入格式不符合接口要求",
            "details": {"fields": [str(e["loc"]) for e in exc.errors()]},
            "request_id": request.state.request_id,
        },
        status_code=422,
    )


@app.exception_handler(ReviewError)
async def domain_error(request, exc):
    return JSONResponse(
        {"code": exc.code, "message": str(exc), "details": {}, "request_id": request.state.request_id},
        status_code=422,
    )


@app.get("/api/v1/health/live")
def live():
    return {"status": "ok", "version": app.version}


@app.get("/api/v1/health/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
    return {"status": "ready", "database": "postgresql", "queue": "redis/rq"}


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


@app.post("/api/v1/sessions")
def login(body: LoginInput, request: Request, db: Session = Depends(get_db)):
    if settings.auth_mode == "local":
        fail("LOGIN_DISABLED", "单用户工作空间无需登录", 404)
    connection = Redis.from_url(settings.redis_url)
    key = f"login-attempts:{request.client.host}"
    attempts = connection.incr(key)
    if attempts == 1:
        connection.expire(key, 60)
    if attempts > 20:
        fail("RATE_LIMITED", "登录尝试过多，请稍后重试", 429)
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.password_hash):
        fail("INVALID_CREDENTIALS", "账号或密码错误", 401)
    token = issue_session(db, user)
    response = JSONResponse({"id": user.id, "username": user.username, "role": user.role})
    response.set_cookie(
        "review_session",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=43200,
        path="/",
    )
    return response


@app.get("/api/v1/session")
def session(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role, "auth_mode": settings.auth_mode}


@app.delete("/api/v1/session")
def logout(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_hash(request.cookies.get("review_session", ""))
        )
    )
    if row:
        db.delete(row)
        db.commit()
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("review_session")
    return response


@app.get("/api/v1/config")
def config(user: User = Depends(current_user)):
    return {
        "text": {"ready": not settings.missing("text"), "missing": settings.missing("text")},
        "video": {"ready": not settings.missing("video"), "missing": settings.missing("video")},
        "models": {"text": settings.ark_text_model or None, "video": settings.ark_video_model or None},
        "limits": {
            "max_bytes": settings.review_max_bytes,
            "max_duration_ms": settings.review_max_duration_ms,
        },
        "video_policy": "原始 MP4 + 完整音轨 + 连续视频片段，保留证据与人工终审",
        "model_live_tested": settings.ark_video_audio_verified,
    }


for router in (
    assets.router,
    projects.router,
    rulesets.router,
    reviews.router,
    integrity.router,
    video_content.router,
):
    app.include_router(router, prefix="/api/v1")

web_dist = ROOT / "apps/web/dist"
if web_dist.exists():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
