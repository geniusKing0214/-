"""bcrypt 직접 사용 (passlib은 bcrypt 4.1+ 와 호환 문제로 제거). 기존 $2b$ 해시와 호환."""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    raw = password.strip().encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    raw = plain_password.strip().encode("utf-8")[:72]
    try:
        h = hashed_password.encode("utf-8")
        return bcrypt.checkpw(raw, h)
    except Exception:
        return False
