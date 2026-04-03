from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from pathlib import Path
from datetime import date, datetime
import calendar
import os

from app.firebase_config import verify_firebase_token
from app.firestore_service import (
    get_events_by_month,
    create_event,
    delete_event,
    get_pending_requests,
    get_user_applications,
    apply_to_event,
    approve_application,
    reject_application,
    enrich_events_with_stats,
    get_unread_notifications,
    mark_notification_as_read,
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


def require_admin_or_redirect(request: Request):
    user = get_current_user(request)
    if not is_admin(user["email"]):
        return None, RedirectResponse(url="/", status_code=303)
    return user, None


def build_month_matrix(year: int, month: int, events: list, user_applications: list):
    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdatescalendar(year, month)

    event_map = {}
    for event in events:
        event_date = event.get("date")
        if not event_date:
            continue
        event_map.setdefault(event_date, []).append(event)

    applied_event_ids = {
        app_item.get("event_id")
        for app_item in user_applications
        if app_item.get("status") != "rejected"
    }

    today_str = date.today().isoformat()
    weeks = []

    for week in month_days:
        row = []
        for day in week:
            day_str = day.isoformat()
            day_events = event_map.get(day_str, [])

            day_events_sorted = sorted(
                day_events,
                key=lambda x: (x.get("start_time", ""), x.get("title", ""))
            )

            row.append({
                "date": day,
                "date_str": day_str,
                "day_num": day.day,
                "is_current_month": day.month == month,
                "is_today": day_str == today_str,
                "has_events": len(day_events_sorted) > 0,
                "events": [
                    {
                        **event,
                        "is_applied": event.get("id") in applied_event_ids
                    }
                    for event in day_events_sorted
                ]
            })
        weeks.append(row)

    return weeks


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


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    selected_date: str | None = None,
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
            event_dates = [event.get("date") for event in events if event.get("date")]
            selected_date = event_dates[0] if event_dates else f"{year:04d}-{month:02d}-01"

    applied_ids = {
        app_item.get("event_id")
        for app_item in user_applications
        if app_item.get("status") != "rejected"
    }

    selected_events = [
        {
            **event,
            "is_applied": event.get("id") in applied_ids
        }
        for event in events
        if event.get("date") == selected_date
    ]
    selected_events.sort(key=lambda x: (x.get("start_time", ""), x.get("title", "")))

    weeks = build_month_matrix(year, month, events, user_applications)
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
            "selected_events": selected_events,
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


@app.post("/apply/{event_id}")
async def apply_schedule(request: Request, event_id: str):
    user = get_current_user(request)
    if not user["email"]:
        return RedirectResponse(url="/login", status_code=303)

    try:
        apply_to_event(
            event_id=event_id,
            user_email=user["email"],
            user_name=user["name"] or ""
        )
    except Exception as e:
        print("apply_to_event error:", e)

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/notification/read/{application_id}")
async def read_notification(request: Request, application_id: str):
    user = get_current_user(request)
    if not user["email"]:
        return RedirectResponse(url="/login", status_code=303)

    try:
        mark_notification_as_read(application_id)
    except Exception as e:
        print("mark_notification_as_read error:", e)

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, year: int | None = None, month: int | None = None):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    today = date.today()
    year = year or today.year
    month = month or today.month

    try:
        events = get_events_by_month(year, month)
        events = enrich_events_with_stats(events)
    except Exception as e:
        print("admin get_events_by_month error:", e)
        events = []

    try:
        pending_requests = get_pending_requests()
    except Exception as e:
        print("get_pending_requests error:", e)
        pending_requests = []

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
            "events": events,
            "pending_requests": pending_requests,
            **nav,
        },
    )


@app.get("/admin/create", response_class=HTMLResponse)
async def create_event_page(request: Request):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    return templates.TemplateResponse(
        "create_event.html",
        {
            "request": request,
            "user": user,
            "is_admin": True,
        },
    )


@app.post("/admin/create")
async def create_event_submit(
    request: Request,
    title: str = Form(...),
    date_value: str = Form(...),
    start_time: str = Form(...),
    capacity: int = Form(...),
    description: str = Form(""),
    color: str = Form("#2563eb"),
    note: str = Form(""),
):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    try:
        create_event(
            {
                "title": title.strip(),
                "date": date_value,
                "start_time": start_time,
                "capacity": capacity,
                "description": description.strip(),
                "color": color.strip() or "#2563eb",
                "note": note.strip(),
                "created_by": user["email"],
                "created_at": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        print("create_event error:", e)

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete/{event_id}")
async def admin_delete_event(request: Request, event_id: str):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    try:
        delete_event(event_id)
    except Exception as e:
        print("delete_event error:", e)

    return RedirectResponse(url=request.headers.get("referer", "/admin"), status_code=303)


@app.post("/admin/approve/{application_id}")
async def admin_approve_application(request: Request, application_id: str):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    try:
        approve_application(application_id)
    except Exception as e:
        print("approve_application error:", e)

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/reject/{application_id}")
async def admin_reject_application(request: Request, application_id: str):
    user, redirect_response = require_admin_or_redirect(request)
    if redirect_response:
        return redirect_response

    try:
        reject_application(application_id)
    except Exception as e:
        print("reject_application error:", e)

    return RedirectResponse(url="/admin", status_code=303)