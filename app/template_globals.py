from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote


def user_is_admin(user: Optional[Any]) -> bool:
    """DB·레거시 값 호환: NULL, 0/1 정수도 안정적으로 판별."""
    if user is None:
        return False
    v = getattr(user, "is_admin", None)
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)


def display_name(user: Optional[Any]) -> str:
    """닉네임이 있으면 닉네임, 없으면 로그인 아이디(username)."""
    if user is None:
        return ""
    raw = getattr(user, "nickname", None)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return str(getattr(user, "username", "") or "")


def attach_template_globals(templates) -> None:
    templates.env.globals["display_name"] = display_name
    templates.env.globals["user_is_admin"] = user_is_admin
    templates.env.filters["urlquote"] = lambda s: quote(str(s or ""), safe="")
