"""Generate local-only credentials and initialize configured accounts. Never print secrets."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def initialize():
    from init_env import prepare

    prepare()
    from alembic import command
    from alembic.config import Config
    from app.api.auth import password_hash
    from app.db import SessionLocal
    from app.models import User
    from app.settings import settings
    from sqlalchemy import select

    if settings.auth_mode == "session" and not settings.admin_password:
        raise RuntimeError("ADMIN_PASSWORD is required for the initialized administrator account")

    os.chdir(ROOT)
    command.upgrade(Config(str(ROOT / "apps/api/alembic.ini")), "head")
    with SessionLocal() as db:
        if settings.auth_mode == "local":
            if not db.scalar(select(User).where(User.username == "workspace")):
                db.add(User(username="workspace", password_hash="login-disabled", role="admin"))
            db.commit()
            print("Database migrated; single-user workspace ready. No login required.")
            return
        for name, password, role in [
            (settings.admin_username, settings.admin_password, "admin"),
            (settings.reviewer_username, settings.reviewer_password, "reviewer"),
        ]:
            if password and not db.scalar(select(User).where(User.username == name)):
                db.add(User(username=name, password_hash=password_hash(password), role=role))
        db.commit()
    print("Database migrated; configured accounts initialized. Local credentials are never printed.")


if __name__ == "__main__":
    initialize()
