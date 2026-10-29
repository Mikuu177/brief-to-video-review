"""Audit tracked and publishable untracked files; never print matched secret values."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKLT[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{30,}\b"),
]
FORBIDDEN_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx", ".dump", ".sql", ".mp4", ".mov",
    ".pdf", ".doc", ".docx", ".xlsx", ".png", ".jpg", ".zip", ".gz",
}


def audit(root=ROOT, private_values=()):
    names = subprocess.check_output(
        ["git", "-c", "core.excludesfile=/dev/null", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    ).decode().split("\0")
    failures = []
    checked = 0
    for name in sorted(set(filter(None, names))):
        path = root / name
        checked += 1
        if path.is_symlink() or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append({"file": name, "reason": "private/binary asset or symlink"})
            continue
        if name.startswith(("data/", "evaluation/runs/", "deliverables/")) or (
            path.name.startswith(".env") and path.name != ".env.example"
        ):
            failures.append({"file": name, "reason": "runtime data or environment"})
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append({"file": name, "reason": "unreviewed binary file"})
            continue
        if any(pattern.search(text) for pattern in PATTERNS):
            failures.append({"file": name, "reason": "credential signature"})
        if any(value and value in text for value in private_values):
            failures.append({"file": name, "reason": "known private value"})
    return {"files_checked": checked, "failures": failures}


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["failures"]))
