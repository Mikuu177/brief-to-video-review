import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from app.db import get_db
from app.errors import fail
from app.models import LoginSession, Project, User
from app.settings import settings
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(password_hash(password, stored.split("$")[0]), stored)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if settings.auth_mode == "local":
        user = db.scalar(select(User).where(User.username == "workspace"))
        if user is None:
            fail("SETUP_REQUIRED", "请先运行 scripts/bootstrap.py 初始化工作空间", 503)
        return user
    token = request.cookies.get("review_session", "")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(token)))
    if session is None or session.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        fail("UNAUTHENTICATED", "请先登录", 401)
    return db.get(User, session.user_id)


def project_access(db: Session, project_id: str, user: User, lock: bool = False) -> Project:
    query = select(Project).where(Project.id == project_id)
    if lock:
        query = query.with_for_update()
    project = db.scalar(query)
    if project is None:
        fail("NOT_FOUND", "项目不存在", 404)
    if user.role != "admin" and user.id != project.owner_id and user.id not in project.member_ids:
        fail("FORBIDDEN", "没有该项目的访问权限", 403)
    return project


def issue_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(40)
    db.add(
        LoginSession(
            token_hash=token_hash(token), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=12)
        )
    )
    db.commit()
    return token
