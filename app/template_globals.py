from __future__ import annotations

from typing import Any, Optional


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
