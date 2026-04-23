"""달력 그리드(홈·별도 일정 등) 공통 생성."""
from __future__ import annotations

import calendar as calendar_mod
from datetime import date, datetime
from typing import Any


def _coerce_to_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip()[:10])
        except ValueError:
            return None
    return None


def schedule_calendar_date(sched: Any) -> date:
    """Schedule.event_datetime 이 datetime·date·문자열일 때 달력 날짜 키."""
    d = _coerce_to_date(getattr(sched, "event_datetime", None))
    if d is None:
        return date.today()
    return d


def _schedule_hhmm(sched: Any) -> str:
    ed = getattr(sched, "event_datetime", None)
    if isinstance(ed, datetime):
        return ed.strftime("%H:%M")
    if isinstance(ed, date):
        return "00:00"
    if isinstance(ed, str):
        s = ed.strip()
        try:
            if "T" in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%H:%M")
            if len(s) >= 16 and s[10] in (" ", "T"):
                return datetime.strptime(s[:16].replace("T", " "), "%Y-%m-%d %H:%M").strftime("%H:%M")
        except ValueError:
            pass
        return "00:00"
    return "--:--"


def _normalize_member_schedules_by_date(raw: dict | None) -> dict[date, list]:
    if not raw:
        return {}
    out: dict[date, list] = {}
    for k, lst in raw.items():
        dk = _coerce_to_date(k)
        if dk is None:
            continue
        out.setdefault(dk, []).extend(list(lst or []))
    return out


def short_schedule_title(title: str, max_len: int = 18) -> str:
    t = (title or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def build_cal_weeks(
    y: int,
    m: int,
    *,
    view: str,
    by_date: dict[date, list],
    member_schedules_by_date: dict[date, list],
) -> list[list[dict[str, Any]]]:
    """
    view: admin_merged | member_merged | member_personal | member_events
    member_personal: 달력 칸 안에 승인된 별도 일정(시간+제목) 최대 2개 + 더보기
    admin_merged / member_merged: 이벤트+별도 일정 점 합산
    그 외(member_events 등): 점(calendar_marker_count)만 사용
    """
    member_schedules_by_date = _normalize_member_schedules_by_date(member_schedules_by_date)
    cal = calendar_mod.Calendar(firstweekday=0)
    cal_weeks: list[list[dict[str, Any]]] = []
    for week in cal.monthdatescalendar(y, m):
        row: list[dict[str, Any]] = []
        for d in week:
            n_ev = len(by_date.get(d, []))
            n_ms = len(member_schedules_by_date.get(d, []))
            if view == "member_personal":
                marker = min(n_ms, 3)
            elif view == "member_events":
                marker = min(n_ev, 3)
            else:
                marker = min(n_ev + n_ms, 3)

            cell: dict[str, Any] = {
                "date": d,
                "in_month": d.month == m,
                "is_today": d == date.today(),
                "event_count": n_ev,
                "member_approved_schedule_count": n_ms,
                "calendar_marker_count": marker,
                "member_cell_chips": [],
                "member_cell_more": 0,
            }

            if view == "member_personal" and d.month == m:
                ms_day = member_schedules_by_date.get(d, [])
                chips: list[dict[str, Any]] = []
                for s in ms_day[:2]:
                    chips.append(
                        {
                            "id": s.id,
                            "time": _schedule_hhmm(s),
                            "title": short_schedule_title(s.title, 18),
                        }
                    )
                cell["member_cell_chips"] = chips
                cell["member_cell_more"] = max(0, len(ms_day) - 2)

            row.append(cell)
        cal_weeks.append(row)
    return cal_weeks
