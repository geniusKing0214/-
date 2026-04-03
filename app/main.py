from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.firebase_config import verify_firebase_token
from app.firestore_service import (
    apply_to_slot,
    approve_application,
    create_event,
    delete_event,
    enrich_events_with_stats,
    get_event,
    get_events_by_month,
    get_pending_requests,
    get_unread_notifications,
    get_user_applications,
    mark_notification_as_read,
    reject_application,
    update_event,
)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

load_dotenv(PROJECT_DIR / ".env")

app = FastAPI()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ADMIN_EMAILS_RAW = os.getenv("ADMIN_EMAILS", "")
ADMIN_EMAILS = {
    email.strip().lower()
    for email in ADMIN_EMAILS_RAW.split(",")
    if email.strip()
}

calendar.setfirstweekday(calendar.SUNDAY)


def firebase_context():
    return {
        "firebase_api_key": os.getenv("FIREBASE_WEB_API_KEY", ""),
        "firebase_auth_domain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "firebase_project_id": os.getenv("FIREBASE_PROJECT_ID", ""),
        "firebase_storage_bucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
        "firebase_messaging_sender_id": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
        "firebase_app_id": os.getenv("FIREBASE_APP_ID", ""),
    }


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    return email.strip().lower()


def get_current_user(request: Request):
    email = normalize_email(request.session.get("user_email"))
    return {
        "email": email,
        "name": request.session.get("user_name"),
        "picture": request.session.get("user_picture"),
    }


def is_admin(email: str | None) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    return normalized in ADMIN_EMAILS


def require_admin(request: Request):
    user = get_current_user(request)
    if not is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


def month_nav(year: int, month: int):
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return {
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


def parse_sessions_json(raw: str):
    import json

    try:
        data = json.loads(raw or "[]")
    except Exception:
        raise HTTPException(status_code=400, detail="세션 데이터 형식이 올바르지 않습니다.")

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="세션 데이터 형식이 올바르지 않습니다.")

    sessions = []

    for session in data:
        if not isinstance(session, dict):
            continue

        session_id = str(session.get("id") or uuid4().hex[:10])
        session_date = str(session.get("date", "")).strip()
        slots_raw = session.get("slots", [])

        if not session_date or not isinstance(slots_raw, list):
            continue

        slots = []
        for slot in slots_raw:
            if not isinstance(slot, dict):
                continue

            slot_id = str(slot.get("id") or uuid4().hex[:10])
            start_time = str(slot.get("start_time", "")).strip()

            try:
                capacity = int(slot.get("capacity", 0))
            except Exception:
                capacity = 0

            if not start_time or capacity <= 0:
                continue

            slots.append({
                "id": slot_id,
                "start_time": start_time,
                "capacity": capacity,
            })

        if slots:
            slots.sort(key=lambda x: x["start_time"])
            sessions.append({
                "id": session_id,
                "date": session_date,
                "slots": slots,
            })

    sessions.sort(key=lambda x: x["date"])

    if not sessions:
        raise HTTPException(status_code=400, detail="최소 1개 이상의 날짜 세션과 슬롯이 필요합니다.")

    return sessions


def split_contiguous_dates(date_strings: list[str]):
    if not date_strings:
        return []

    unique_dates = sorted({datetime.strptime(d, "%Y-%m-%d").date() for d in date_strings})
    ranges = []

    start = unique_dates[0]
    end = unique_dates[0]

    for current in unique_dates[1:]:
        if current == end + timedelta(days=1):
            end = current
        else:
            ranges.append((start, end))
            start = current
            end = current

    ranges.append((start, end))
    return ranges


def build_calendar_data(year: int, month: int, events: list, user_applications: list, selected_date: str | None):
    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdatescalendar(year, month)
    today_str = date.today().isoformat()

    applied_pairs = {
        (app.get("event_id"), app.get("session_id"))
        for app in user_applications
        if app.get("status") != "rejected"
    }

    weeks = []
    for week in month_days:
        week_cells = []
        for day in week:
            day_str = day.isoformat()

            day_event_titles = []
            for event in events:
                for session in event.get("sessions", []):
                    if session.get("date") == day_str:
                        day_event_titles.append({
                            "title": event.get("title", ""),
                            "color": event.get("color", "#2563eb"),
                        })

            week_cells.append({
                "date": day,
                "date_str": day_str,
                "day_num": day.day,
                "is_current_month": day.month == month,
                "is_today": day_str == today_str,
                "is_selected": day_str == selected_date,
                "day_event_titles": day_event_titles[:3],
                "extra_event_count": max(0, len(day_event_titles) - 3),
            })

        weeks.append({
            "days": week_cells,
            "bars": [],
            "bar_rows": 0,
        })

    selected_day_events = []
    if selected_date:
        for event in events:
            matched_session = None
            for session in event.get("sessions", []):
                if session.get("date") == selected_date:
                    matched_session = session
                    break

            if matched_session:
                selected_day_events.append({
                    "id": event["id"],
                    "title": event.get("title", ""),
                    "description": event.get("description", ""),
                    "color": event.get("color", "#2563eb"),
                    "note": event.get("note", ""),
                    "session": matched_session,
                    "is_applied": (event["id"], matched_session["id"]) in applied_pairs,
                })

    selected_day_events.sort(key=lambda x: x.get("title", ""))

    for event in events:
        session_dates = [session.get("date") for session in event.get("sessions", []) if session.get("date")]
        date_ranges = split_contiguous_dates(session_dates)

        for range_start, range_end in date_ranges:
            for week_index, week in enumerate(month_days):
                week_start = week[0]
                week_end = week[-1]

                seg_start = max(range_start, week_start)
                seg_end = min(range_end, week_end)

                if seg_start > seg_end:
                    continue

                start_col = (seg_start - week_start).days
                end_col = (seg_end - week_start).days

                bar = {
                    "event_id": event["id"],
                    "title": event.get("title", ""),
                    "color": event.get("color", "#2563eb"),
                    "start_col": start_col,
                    "end_col": end_col,
                    "is_start": seg_start == range_start,
                    "is_end": seg_end == range_end,
                    "row": 0,
                }

                placed = False
                for row_index in range(20):
                    collision = False
                    for existing in weeks[week_index]["bars"]:
                        if existing["row"] != row_index:
                            continue
                        if not (bar["end_col"] < existing["start_col"] or bar["start_col"] > existing["end_col"]):
                            collision = True
                            break
                    if not collision:
                        bar["row"] = row_index
                        placed = True
                        break

                if not placed:
                    bar["row"] = len(weeks[week_index]["bars"])

                weeks[week_index]["bars"].append(bar)
                weeks[week_index]["bar_rows"] = max(weeks[week_index]["bar_rows"], bar["row"] + 1)

    return weeks, selected_day_events


def group_admin_events_by_date(events: list):
    date_map = {}

    for event in events:
        applicants = event.get("applicants", [])
        applicant_map = {}
        for applicant in applicants:
            key = (applicant.get("session_id"), applicant.get("slot_id"))
            applicant_map.setdefault(key, []).append(applicant)

        for session in event.get("sessions", []):
            session_date = session.get("date")
            if not session_date:
                continue

            date_group = date_map.setdefault(session_date, {
                "date": session_date,
                "events": [],
                "total_count": 0,
                "pending_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
            })

            session_slots = []
            session_pending = 0
            session_approved = 0
            session_rejected = 0

            for slot in session.get("slots", []):
                slot_applicants = applicant_map.get((session.get("id"), slot.get("id")), [])

                pending_count = sum(1 for a in slot_applicants if a.get("status") == "pending")
                approved_count = sum(1 for a in slot_applicants if a.get("status") == "approved")
                rejected_count = sum(1 for a in slot_applicants if a.get("status") == "rejected")

                session_pending += pending_count
                session_approved += approved_count
                session_rejected += rejected_count

                slot_capacity = int(slot.get("capacity", 0) or 0)

                session_slots.append({
                    **slot,
                    "applicants": sorted(
                        slot_applicants,
                        key=lambda x: (
                            x.get("status", ""),
                            x.get("user_name", "") or x.get("user_email", "")
                        )
                    ),
                    "pending_count": pending_count,
                    "approved_count": approved_count,
                    "rejected_count": rejected_count,
                    "remaining": max(0, slot_capacity - approved_count),
                })

            total_count = session_pending + session_approved + session_rejected

            date_group["events"].append({
                "id": event.get("id"),
                "title": event.get("title", ""),
                "description": event.get("description", ""),
                "color": event.get("color", "#2563eb"),
                "note": event.get("note", ""),
                "session_id": session.get("id"),
                "date": session_date,
                "slots": session_slots,
                "total_count": total_count,
                "pending_count": session_pending,
                "approved_count": session_approved,
                "rejected_count": session_rejected,
            })

            date_group["total_count"] += total_count
            date_group["pending_count"] += session_pending
            date_group["approved_count"] += session_approved
            date_group["rejected_count"] += session_rejected

    grouped_dates = []
    for date_key in sorted(date_map.keys()):
        group = date_map[date_key]
        group["events"].sort(key=lambda x: (x.get("title", ""), x.get("id", "")))
        grouped_dates.append(group)

    return grouped_dates


def build_admin_calendar_data(year: int, month: int, grouped_events_by_date: list, selected_date: str | None):
    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdatescalendar(year, month)
    today_str = date.today().isoformat()

    grouped_map = {item["date"]: item for item in grouped_events_by_date}

    weeks = []
    for week in month_days:
        row = []
        for day in week:
            day_str = day.isoformat()
            group = grouped_map.get(day_str)

            event_count = len(group["events"]) if group else 0
            pending_count = group["pending_count"] if group else 0

            row.append({
                "date": day,
                "date_str": day_str,
                "day_num": day.day,
                "is_current_month": day.month == month,
                "is_today": day_str == today_str,
                "is_selected": day_str == selected_date,
                "event_count": event_count,
                "pending_count": pending_count,
                "has_events": event_count > 0,
            })
        weeks.append(row)

    selected_group = grouped_map.get(selected_date) if selected_date else None
    return weeks, selected_group


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    selected_date: str | None = None,
    selected_event_id: str | None = None,
):
    today = date.today()
    year = year or today.year
    month = month or today.month

    user = get_current_user(request)
    user_email = user["email"]

    try:
        events = get_events_by_month(year, month)
    except Exception as e:
        print("get_events_by_month error:", e)
        events = []

    try:
        user_applications = get_user_applications(user_email) if user_email else []
    except Exception as e:
        print("get_user_applications error:", e)
        user_applications = []

    try:
        unread_notifications = get_unread_notifications(user_email) if user_email else []
    except Exception as e:
        print("get_unread_notifications error:", e)
        unread_notifications = []

    if not selected_date:
        if year == today.year and month == today.month:
            selected_date = today.isoformat()
        else:
            event_dates = []
            for event in events:
                for session in event.get("sessions", []):
                    event_dates.append(session.get("date"))
            selected_date = event_dates[0] if event_dates else f"{year:04d}-{month:02d}-01"

    weeks, selected_events = build_calendar_data(
        year=year,
        month=month,
        events=events,
        user_applications=user_applications,
        selected_date=selected_date,
    )

    applied_triplets = {
        (app.get("event_id"), app.get("session_id"), app.get("slot_id"))
        for app in user_applications
        if app.get("status") != "rejected"
    }

    selected_event = None
    if selected_event_id:
        for event in selected_events:
            if event.get("id") == selected_event_id:
                slots = []
                for slot in event["session"].get("slots", []):
                    slots.append({
                        **slot,
                        "is_applied": (
                            event["id"],
                            event["session"]["id"],
                            slot["id"]
                        ) in applied_triplets
                    })

                selected_event = {
                    **event,
                    "session": {
                        **event["session"],
                        "slots": slots,
                    }
                }
                break

    nav = month_nav(year, month)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin(user_email),
            "year": year,
            "month": month,
            "month_label": f"{year}년 {month}월",
            "weeks": weeks,
            "weekdays": ["일", "월", "화", "수", "목", "금", "토"],
            "selected_date": selected_date,
            "selected_event_id": selected_event.get("id") if selected_event else None,
            "selected_events": selected_events,
            "selected_event": selected_event,
            "unread_notifications": unread_notifications,
            **nav,
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin(user["email"]),
            **firebase_context(),
        },
    )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "user": user,
            "is_admin": is_admin(user["email"]),
            **firebase_context(),
        },
    )


@app.post("/session/login")
async def session_login(request: Request):
    body = await request.json()
    id_token = body.get("idToken")

    if not id_token:
        raise HTTPException(status_code=400, detail="Missing idToken")

    decoded = verify_firebase_token(id_token)
    email = normalize_email(decoded.get("email"))
    name = decoded.get("name", "")
    picture = decoded.get("picture", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in token")

    request.session["user_email"] = email
    request.session["user_name"] = name
    request.session["user_picture"] = picture

    return JSONResponse({"ok": True})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.post("/apply/{event_id}/{session_id}/{slot_id}")
async def apply_schedule(request: Request, event_id: str, session_id: str, slot_id: str):
    user = get_current_user(request)
    if not user["email"]:
        return RedirectResponse(url="/login", status_code=303)

    apply_to_slot(
        event_id=event_id,
        session_id=session_id,
        slot_id=slot_id,
        user_email=user["email"],
        user_name=user["name"] or "",
    )

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/notification/read/{application_id}")
async def read_notification(request: Request, application_id: str):
    user = get_current_user(request)
    if not user["email"]:
        return RedirectResponse(url="/login", status_code=303)

    mark_notification_as_read(application_id)
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    selected_date: str | None = None,
):
    user = require_admin(request)

    today = date.today()
    year = year or today.year
    month = month or today.month

    events = enrich_events_with_stats(get_events_by_month(year, month))
    grouped_events_by_date = group_admin_events_by_date(events)

    if not selected_date:
        if year == today.year and month == today.month:
            selected_date = today.isoformat()
        else:
            selected_date = grouped_events_by_date[0]["date"] if grouped_events_by_date else f"{year:04d}-{month:02d}-01"

    admin_weeks, selected_admin_group = build_admin_calendar_data(
        year=year,
        month=month,
        grouped_events_by_date=grouped_events_by_date,
        selected_date=selected_date,
    )

    pending_requests = get_pending_requests()
    nav = month_nav(year, month)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "is_admin": True,
            "year": year,
            "month": month,
            "month_label": f"{year}년 {month}월",
            "selected_date": selected_date,
            "admin_weeks": admin_weeks,
            "selected_admin_group": selected_admin_group,
            "pending_requests": pending_requests,
            **nav,
        },
    )


@app.get("/admin/create", response_class=HTMLResponse)
async def create_event_page(request: Request):
    user = require_admin(request)
    return templates.TemplateResponse(
        "create_event.html",
        {
            "request": request,
            "user": user,
            "is_admin": True,
            "mode": "create",
            "event": None,
        },
    )


@app.post("/admin/create")
async def create_event_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    color: str = Form("#2563eb"),
    note: str = Form(""),
    sessions_json: str = Form("[]"),
):
    user = require_admin(request)
    sessions = parse_sessions_json(sessions_json)

    create_event({
        "title": title.strip(),
        "description": description.strip(),
        "color": color.strip() or "#2563eb",
        "note": note.strip(),
        "sessions": sessions,
        "created_by": user["email"],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    })

    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/edit/{event_id}", response_class=HTMLResponse)
async def edit_event_page(request: Request, event_id: str):
    user = require_admin(request)
    event = get_event(event_id)

    if not event:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")

    return templates.TemplateResponse(
        "create_event.html",
        {
            "request": request,
            "user": user,
            "is_admin": True,
            "mode": "edit",
            "event": event,
        },
    )


@app.post("/admin/edit/{event_id}")
async def edit_event_submit(
    request: Request,
    event_id: str,
    title: str = Form(...),
    description: str = Form(""),
    color: str = Form("#2563eb"),
    note: str = Form(""),
    sessions_json: str = Form("[]"),
):
    require_admin(request)

    event = get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")

    sessions = parse_sessions_json(sessions_json)

    update_event(event_id, {
        "title": title.strip(),
        "description": description.strip(),
        "color": color.strip() or "#2563eb",
        "note": note.strip(),
        "sessions": sessions,
        "updated_at": datetime.now().isoformat(),
    })

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete/{event_id}")
async def admin_delete_event(request: Request, event_id: str):
    require_admin(request)
    delete_event(event_id)
    return RedirectResponse(url=request.headers.get("referer", "/admin"), status_code=303)


@app.post("/admin/approve/{application_id}")
async def admin_approve_application(request: Request, application_id: str):
    require_admin(request)
    approve_application(application_id)
    return RedirectResponse(url=request.headers.get("referer", "/admin"), status_code=303)


@app.post("/admin/reject/{application_id}")
async def admin_reject_application(request: Request, application_id: str):
    require_admin(request)
    reject_application(application_id)
    return RedirectResponse(url=request.headers.get("referer", "/admin"), status_code=303)