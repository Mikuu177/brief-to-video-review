"""Prepare local deployment secrets using only the Python standard library."""

import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    env = ROOT / ".env"
    if not env.exists() and os.environ.get("DATABASE_URL"):
        return
    text = env.read_text() if env.exists() else ""
    values = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#")
    )
    defaults = {"AUTH_MODE": "local", "POSTGRES_PASSWORD": secrets.token_urlsafe(24)}
    if values.get("AUTH_MODE", "local").strip("\"' ") == "session":
        defaults.update({
            "SESSION_SECRET": secrets.token_urlsafe(48),
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": secrets.token_urlsafe(16),
            "REVIEWER_USERNAME": "reviewer",
            "REVIEWER_PASSWORD": secrets.token_urlsafe(16),
        })
    generated_admin = not values.get("ADMIN_PASSWORD", "").strip("\"' ")
    for key, value in defaults.items():
        if values.get(key, "").strip("\"' "):
            continue
        values[key] = value
        pattern = rf"^{key}=.*$"
        if re.search(pattern, text, flags=re.MULTILINE):
            text = re.sub(pattern, f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text += f"\n{key}={value}\n"
    env.write_text(text)
    os.chmod(env, 0o600)
    if generated_admin and "ADMIN_PASSWORD" in defaults:
        access = ROOT / "data/LOCAL_ACCESS.md"
        access.parent.mkdir(exist_ok=True)
        access.write_text(
            f"# 本地预览登录\n\n入口：http://127.0.0.1:8008\n\n"
            f"管理员：`{values['ADMIN_USERNAME']}`\n\n密码：`{values['ADMIN_PASSWORD']}`\n\n"
            "仅限本机开发。该文件与 .env 不进入交付源码包。\n"
        )
        os.chmod(access, 0o600)


if __name__ == "__main__":
    prepare()
    print("Environment prepared; secrets are stored only in ignored local files.")
