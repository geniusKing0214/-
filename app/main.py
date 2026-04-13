from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, SessionLocal, engine, ensure_sqlite_migrations
from app.models import User, Event, EventSlot, Application, Notification
from app.routes.member_schedule import router as member_schedule_router
from app.template_globals import attach_template_globals, display_name as user_display_name


app = FastAPI(title="Scheduler App")

_session_secret = os.environ.get("SESSION_SECRET_KEY", "change-this-secret-key")
_session_https_only = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    same_site="lax",
    https_only=_session_https_only,
)


def _safe_internal_redirect_path(raw: Optional[str]) -> Optional[str]:
    """Same-origin path only; blocks open redirects (//evil.com)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s.startswith("/") or s.startswith("//") or "://" in s:
        return None
    if "\n" in s or "\r" in s:
        return None
    return s


def _wants_json_only(request: Request) -> bool:
    """True when client explicitly asks for JSON and not HTML (e.g. fetch API)."""
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept and "text/html" not in accept


def _login_url_with_next(request: Request) -> str:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    safe = _safe_internal_redirect_path(next_path)
    if safe:
        return f"/login?next={quote(safe, safe='')}"
    return "/login"


@app.exception_handler(HTTPException)
async def _http_exception_browser_redirect(request: Request, exc: HTTPException):
    if (
        exc.status_code == status.HTTP_401_UNAUTHORIZED
        and request.method == "GET"
        and not _wants_json_only(request)
    ):
        return RedirectResponse(
            url=_login_url_with_next(request),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


Base.metadata.create_all(bind=engine)
ensure_sqlite_migrations(engine)

app.include_router(member_schedule_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
attach_template_globals(templates)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    password = password.strip()
    password = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    plain_password = plain_password.strip()
    plain_password = plain_password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def get_current_user(request: Request, db: Session) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_unread_count(user: Optional[User], db: Session) -> int:
    if not user:
        return 0
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .count()
    )


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


def _set_if_present(model_obj, field_name: str, value) -> None:
    if hasattr(model_obj, field_name):
        setattr(model_obj, field_name, value)


def _active_application_statuses() -> set[str]:
    return {"pending", "approved"}


def _reapplicable_statuses() -> set[str]:
    return {"rejected", "cancelled", "canceled"}


def _get_user_approval_status(user: Optional[User]) -> str:
    if not user:
        return "approved"
    return getattr(user, "approval_status", "approved")


_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _day_label_ko(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일 ({_WEEKDAY_KO[d.weekday()]})"


def _week_range_label_ko(week_start: date, week_end: date) -> str:
    return (
        f"{week_start.year}년 {week_start.month}월 {week_start.day}일"
        f" ~ {week_end.year}년 {week_end.month}월 {week_end.day}일"
    )


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _application_event_dt(app: Application) -> datetime:
    return datetime.combine(app.event.event_date, app.slot.start_time)


def _group_applications_by_day(
    applications: list[Application],
) -> list[dict[str, Any]]:
    apps_sorted = sorted(
        applications, key=_application_event_dt, reverse=True
    )
    order: list[date] = []
    by_day: dict[date, list[Application]] = {}
    for app in apps_sorted:
        d = app.event.event_date
        if d not in by_day:
            by_day[d] = []
            order.append(d)
        by_day[d].append(app)
    return [{"label": _day_label_ko(d), "items": by_day[d]} for d in order]


def _group_applications_by_week(
    applications: list[Application],
) -> list[dict[str, Any]]:
    apps_sorted = sorted(
        applications, key=_application_event_dt, reverse=True
    )
    order: list[date] = []
    by_week: dict[date, list[Application]] = {}
    for app in apps_sorted:
        ws = _week_start_monday(app.event.event_date)
        if ws not in by_week:
            by_week[ws] = []
            order.append(ws)
        by_week[ws].append(app)
    groups: list[dict[str, Any]] = []
    for ws in order:
        we = ws + timedelta(days=6)
        groups.append(
            {
                "label": _week_range_label_ko(ws, we),
                "items": by_week[ws],
            }
        )
    return groups


def _event_sort_dt(event: Event) -> datetime:
    if event.slots:
        t = min(s.start_time for s in event.slots)
    else:
        t = datetime.min.time()
    return datetime.combine(event.event_date, t)


def _event_time_label(event: Event) -> str:
    if not event.slots:
        return event.event_date.strftime("%Y-%m-%d")
    t = min(s.start_time for s in event.slots)
    return f"{event.event_date.strftime('%Y-%m-%d')} {t.strftime('%H:%M')}"


def _group_events_by_day(events: list[Event]) -> list[dict[str, Any]]:
    events_sorted = sorted(events, key=_event_sort_dt)
    order: list[date] = []
    by_day: dict[date, list[Event]] = {}
    for e in events_sorted:
        d = e.event_date
        if d not in by_day:
            by_day[d] = []
            order.append(d)
        by_day[d].append(e)
    return [{"label": _day_label_ko(d), "items": by_day[d]} for d in order]


def _group_events_by_week(events: list[Event]) -> list[dict[str, Any]]:
    events_sorted = sorted(events, key=_event_sort_dt)
    order: list[date] = []
    by_week: dict[date, list[Event]] = {}
    for e in events_sorted:
        ws = _week_start_monday(e.event_date)
        if ws not in by_week:
            by_week[ws] = []
            order.append(ws)
        by_week[ws].append(e)
    return [
        {
            "label": _week_range_label_ko(ws, ws + timedelta(days=6)),
            "items": by_week[ws],
        }
        for ws in order
    ]


def _event_application_counts(
    db: Session, event_ids: list[int]
) -> dict[int, dict[str, int]]:
    if not event_ids:
        return {}
    rows = (
        db.query(Application.event_id, Application.status, func.count(Application.id))
        .filter(Application.event_id.in_(event_ids))
        .group_by(Application.event_id, Application.status)
        .all()
    )
    out: dict[int, dict[str, int]] = {
        eid: {"pending": 0, "approved": 0} for eid in event_ids
    }
    for eid, st, n in rows:
        if eid in out and st in ("pending", "approved"):
            out[eid][st] = int(n)
    return out


def admin_required(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise PermissionError("관리자 권한이 필요합니다.")
    return user


def render(
    request: Request,
    template_name: str,
    context: dict,
    db: Session,
    current_user: Optional[User] = None,
) -> HTMLResponse:
    if current_user is None:
        current_user = get_current_user(request, db)

    full_context = {
        "request": request,
        "current_user": current_user,
        "unread_count": get_unread_count(current_user, db),
        **context,
    }
    if "page_title" not in full_context:
        full_context["page_title"] = "스케줄"
    return templates.TemplateResponse(request, template_name, full_context)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "register.html", {}, db)


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    nickname: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    is_admin: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    username = username.strip()
    password = password.strip()
    password_confirm = password_confirm.strip()
    if name is not None:
        name = name.strip() or None
    if phone is not None:
        phone = phone.strip() or None
    nick_clean = (nickname or "").strip() or None
    if nick_clean is not None:
        if len(nick_clean) < 2:
            return render(
                request,
                "register.html",
                {"error": "닉네임은 2자 이상이거나 비워두세요."},
                db,
            )
        if len(nick_clean) > 50:
            return render(
                request,
                "register.html",
                {"error": "닉네임은 50자 이하로 입력해주세요."},
                db,
            )

    if password != password_confirm:
        return render(
            request,
            "register.html",
            {"error": "비밀번호와 비밀번호 확인이 일치하지 않습니다."},
            db,
        )

    if len(username) < 2:
        return render(
            request,
            "register.html",
            {"error": "아이디는 2자 이상 입력해주세요."},
            db,
        )

    if len(password) < 4:
        return render(
            request,
            "register.html",
            {"error": "비밀번호는 최소 4자 이상이어야 합니다."},
            db,
        )

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        return render(
            request,
            "register.html",
            {"error": "이미 존재하는 아이디입니다."},
            db,
        )

    user = User(
        username=username,
        nickname=nick_clean,
        password=hash_password(password),
        is_admin=bool(is_admin),
        approval_status="approved" if bool(is_admin) else "pending_approval",
    )
    db.add(user)
    db.commit()
    if user.is_admin:
        return redirect("/login")
    return render(
        request,
        "login.html",
        {"success": "회원가입이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다."},
        db,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    ctx: dict[str, Any] = {}
    safe_next = _safe_internal_redirect_path(next)
    if safe_next:
        ctx["login_next"] = safe_next
    return render(request, "login.html", ctx, db)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    safe_next = _safe_internal_redirect_path((next or "").strip())
    login_ctx = {"login_next": safe_next} if safe_next else {}

    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password):
        return render(
            request,
            "login.html",
            {**login_ctx, "error": "아이디 또는 비밀번호가 올바르지 않습니다."},
            db,
        )

    approval_status = _get_user_approval_status(user)
    if approval_status != "approved":
        if approval_status == "pending_approval":
            message = "관리자 승인 대기 중입니다. 승인 후 로그인 가능합니다."
        elif approval_status == "rejected":
            message = "계정이 관리자에 의해 거절되었습니다. 관리자에게 문의해주세요."
        else:
            message = "계정 상태를 확인할 수 없습니다. 관리자에게 문의해주세요."
        return render(
            request,
            "login.html",
            {**login_ctx, "error": message},
            db,
        )

    request.session["user_id"] = user.id
    return redirect(safe_next or "/")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    updated: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return redirect("/login")
    return render(
        request,
        "profile.html",
        {
            "page_title": "프로필",
            "profile_success": updated == "1",
        },
        db,
        current_user=user,
    )


@app.post("/profile")
def profile_update(
    request: Request,
    nickname: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return redirect("/login")
    nick = nickname.strip()
    if nick and len(nick) < 2:
        return render(
            request,
            "profile.html",
            {
                "page_title": "프로필",
                "error": "닉네임은 2자 이상이거나 비워두세요.",
            },
            db,
            current_user=user,
        )
    if len(nick) > 50:
        return render(
            request,
            "profile.html",
            {
                "page_title": "프로필",
                "error": "닉네임은 50자 이하로 입력해주세요.",
            },
            db,
            current_user=user,
        )
    user.nickname = nick if nick else None
    db.commit()
    return redirect("/profile?updated=1")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)

    events = (
        db.query(Event)
        .options(joinedload(Event.slots))
        .order_by(Event.event_date.asc(), Event.id.asc())
        .all()
    )

    event_cards = []
    for event in events:
        slot_items = []
        sorted_slots = sorted(event.slots, key=lambda s: s.start_time)

        for slot in sorted_slots:
            approved_count = (
                db.query(Application)
                .filter(
                    Application.slot_id == slot.id,
                    Application.status == "approved",
                )
                .count()
            )
            remaining = max(0, slot.capacity - approved_count)

            slot_items.append(
                {
                    "slot": slot,
                    "approved": approved_count,
                    "remaining": remaining,
                }
            )

        event_cards.append({"event": event, "slots": slot_items})

    return render(
        request,
        "index.html",
        {"event_cards": event_cards},
        db,
        current_user=current_user,
    )


@app.post("/apply")
def apply_to_slot(
    request: Request,
    event_id: int = Form(...),
    slot_id: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return redirect("/login")

    slot = (
        db.query(EventSlot)
        .options(joinedload(EventSlot.event))
        .filter(EventSlot.id == slot_id, EventSlot.event_id == event_id)
        .first()
    )
    if not slot:
        return redirect("/")

    if slot.is_active is False:
        return redirect("/")

    active_existing = (
        db.query(Application)
        .filter(
            Application.user_id == user.id,
            Application.event_id == event_id,
            Application.slot_id == slot.id,
            Application.status.in_(list(_active_application_statuses())),
        )
        .first()
    )
    if active_existing:
        return redirect("/my-applications")

    reusable_rejected = (
        db.query(Application)
        .filter(
            Application.user_id == user.id,
            Application.event_id == event_id,
            Application.slot_id == slot.id,
            Application.status.in_(list(_reapplicable_statuses())),
        )
        .order_by(Application.id.desc())
        .first()
    )

    approved_count = (
        db.query(Application)
        .filter(
            Application.slot_id == slot.id,
            Application.status == "approved",
        )
        .count()
    )
    if approved_count >= slot.capacity:
        return redirect("/")

    if reusable_rejected:
        reusable_rejected.status = "pending"
        _set_if_present(reusable_rejected, "updated_at", datetime.utcnow())
        _set_if_present(reusable_rejected, "rejected_at", None)
        _set_if_present(reusable_rejected, "rejected_reason", None)
        _set_if_present(reusable_rejected, "event_id", event_id)
        _set_if_present(reusable_rejected, "slot_id", slot.id)
        application = reusable_rejected
    else:
        application = Application(
            user_id=user.id,
            event_id=event_id,
            slot_id=slot.id,
            status="pending",
        )
        db.add(application)
        db.flush()

    admins = db.query(User).filter(User.is_admin == True).all()
    for admin in admins:
        db.add(
            Notification(
                user_id=admin.id,
                message=f"{user_display_name(user)}님이 '{slot.event.title}' {slot.start_time.strftime('%H:%M')} 슬롯에 신청했습니다.",
                is_read=False,
            )
        )

    db.commit()
    return redirect("/my-applications")


@app.get("/my-applications", response_class=HTMLResponse)
def my_applications(
    request: Request,
    group: str = Query("day"),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return redirect("/login")

    if group not in ("day", "week"):
        group = "day"

    applications = (
        db.query(Application)
        .join(Event, Application.event_id == Event.id)
        .join(EventSlot, Application.slot_id == EventSlot.id)
        .options(joinedload(Application.event), joinedload(Application.slot))
        .filter(Application.user_id == user.id)
        .order_by(
            Event.event_date.desc(),
            EventSlot.start_time.desc(),
            Application.id.desc(),
        )
        .all()
    )

    if group == "week":
        application_groups = _group_applications_by_week(applications)
    else:
        application_groups = _group_applications_by_day(applications)

    return render(
        request,
        "my_applications.html",
        {
            "page_title": "내 신청",
            "application_groups": application_groups,
            "group_mode": group,
        },
        db,
        current_user=user,
    )


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return redirect("/login")

    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )

    (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .update({"is_read": True}, synchronize_session=False)
    )
    db.commit()

    return render(
        request,
        "notifications.html",
        {"notifications": notifications},
        db,
        current_user=user,
    )


@app.get("/admin/events", response_class=HTMLResponse)
def admin_events(
    request: Request,
    group: str = Query("day"),
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    if group not in ("day", "week"):
        group = "day"

    events = (
        db.query(Event)
        .options(joinedload(Event.slots))
        .order_by(Event.event_date.asc(), Event.id.desc())
        .all()
    )

    event_ids = [e.id for e in events]
    event_stats = _event_application_counts(db, event_ids)
    event_time_labels = {e.id: _event_time_label(e) for e in events}

    if group == "week":
        event_groups = _group_events_by_week(events)
    else:
        event_groups = _group_events_by_day(events)

    return render(
        request,
        "admin_events.html",
        {
            "page_title": "이벤트 관리",
            "event_groups": event_groups,
            "group_mode": group,
            "event_stats": event_stats,
            "event_time_labels": event_time_labels,
        },
        db,
        current_user=admin,
    )


@app.get("/admin/events/{event_id}/edit", response_class=HTMLResponse)
def admin_event_edit(
    request: Request, event_id: int, db: Session = Depends(get_db)
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    event = (
        db.query(Event)
        .options(joinedload(Event.slots))
        .filter(Event.id == event_id)
        .first()
    )
    if not event:
        return redirect("/admin/events")

    return render(
        request,
        "admin_event_edit.html",
        {
            "page_title": "이벤트",
            "event": event,
        },
        db,
        current_user=admin,
    )


@app.get("/admin/members", response_class=HTMLResponse)
def admin_members(request: Request, db: Session = Depends(get_db)):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    members = db.query(User).order_by(User.id.asc()).all()
    return render(
        request,
        "admin_members.html",
        {
            "page_title": "회원 목록",
            "members": members,
        },
        db,
        current_user=admin,
    )


@app.post("/admin/events/create")
def create_event(
    request: Request,
    title: str = Form(...),
    event_date: str = Form(...),
    description: str = Form(""),
    slot_times: list[str] = Form(...),
    slot_capacities: list[int] = Form(...),
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    event = Event(
        title=title.strip(),
        event_date=datetime.strptime(event_date, "%Y-%m-%d").date(),
        description=description.strip() if description else "",
        created_by=admin.id,
    )
    db.add(event)
    db.flush()

    for slot_time, slot_capacity in zip(slot_times, slot_capacities):
        if not slot_time:
            continue
        db.add(
            EventSlot(
                event_id=event.id,
                start_time=datetime.strptime(slot_time, "%H:%M").time(),
                capacity=int(slot_capacity),
                is_active=True,
            )
        )

    db.commit()
    return redirect("/admin/events")


@app.post("/admin/events/{event_id}/delete")
def delete_event(event_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return redirect("/admin/events")

    db.delete(event)
    db.commit()
    return redirect("/admin/events")


@app.get("/admin/operations", response_class=HTMLResponse)
def admin_operations(request: Request, db: Session = Depends(get_db)):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    applications = (
        db.query(Application)
        .options(
            joinedload(Application.user),
            joinedload(Application.event),
            joinedload(Application.slot),
        )
        .order_by(Application.created_at.desc())
        .all()
    )

    pending_users = (
        db.query(User)
        .filter(User.is_admin == False, User.approval_status == "pending_approval")
        .order_by(User.id.desc())
        .all()
    )

    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == admin.id)
        .order_by(Notification.created_at.desc())
        .all()
    )

    pending_count = sum(1 for item in applications if item.status == "pending")
    approved_count = sum(1 for item in applications if item.status == "approved")
    rejected_count = sum(1 for item in applications if item.status == "rejected")

    (
        db.query(Notification)
        .filter(Notification.user_id == admin.id, Notification.is_read == False)
        .update({"is_read": True}, synchronize_session=False)
    )
    db.commit()

    return render(
        request,
        "admin_operations.html",
        {
            "applications": applications,
            "pending_users": pending_users,
            "notifications": notifications,
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
        },
        db,
        current_user=admin,
    )


@app.post("/admin/users/{user_id}/approve")
def approve_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return redirect("/admin/operations")

    user.approval_status = "approved"
    _set_if_present(user, "updated_at", datetime.utcnow())
    db.commit()

    db.add(
        Notification(
            user_id=admin.id,
            message=f"사용자 '{user_display_name(user)}' 계정을 승인했습니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.post("/admin/users/{user_id}/reject")
def reject_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return redirect("/admin/operations")

    user.approval_status = "rejected"
    _set_if_present(user, "updated_at", datetime.utcnow())
    db.commit()

    db.add(
        Notification(
            user_id=admin.id,
            message=f"사용자 '{user_display_name(user)}' 계정을 거절했습니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.get("/admin/applications")
def admin_applications_redirect():
    return redirect("/admin/operations")


@app.post("/admin/applications/{application_id}/approve")
def approve_application(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    application = (
        db.query(Application)
        .options(
            joinedload(Application.user),
            joinedload(Application.event),
            joinedload(Application.slot),
        )
        .filter(Application.id == application_id)
        .first()
    )
    if not application:
        return redirect("/admin/operations")

    approved_count = (
        db.query(Application)
        .filter(
            Application.slot_id == application.slot_id,
            Application.status == "approved",
            Application.id != application.id,
        )
        .count()
    )

    if approved_count >= application.slot.capacity:
        db.add(
            Notification(
                user_id=admin.id,
                message=f"'{application.event.title}' {application.slot.start_time.strftime('%H:%M')} 슬롯은 이미 마감되어 승인할 수 없습니다.",
                is_read=False,
            )
        )
        db.commit()
        return redirect("/admin/operations")

    application.status = "approved"
    application.reviewed_at = datetime.utcnow()
    application.reviewed_by = admin.id

    db.add(
        Notification(
            user_id=application.user_id,
            message=f"'{application.event.title}' {application.slot.start_time.strftime('%H:%M')} 신청이 승인되었습니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.post("/admin/applications/{application_id}/reject")
def reject_application(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    application = (
        db.query(Application)
        .options(
            joinedload(Application.user),
            joinedload(Application.event),
            joinedload(Application.slot),
        )
        .filter(Application.id == application_id)
        .first()
    )
    if not application:
        return redirect("/admin/operations")

    application.status = "rejected"
    application.reviewed_at = datetime.utcnow()
    application.reviewed_by = admin.id

    db.add(
        Notification(
            user_id=application.user_id,
            message=f"'{application.event.title}' {application.slot.start_time.strftime('%H:%M')} 신청이 거절되었습니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.post("/admin/applications/{application_id}/move")
def move_application(
    application_id: int,
    request: Request,
    target_event_id: int = Form(...),
    target_slot_id: int = Form(...),
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    application = (
        db.query(Application)
        .options(
            joinedload(Application.user),
            joinedload(Application.event),
            joinedload(Application.slot),
        )
        .filter(Application.id == application_id)
        .first()
    )
    if not application:
        return redirect("/admin/operations")

    target_event = db.query(Event).filter(Event.id == target_event_id).first()
    if not target_event:
        return redirect("/admin/operations")

    target_slot = (
        db.query(EventSlot)
        .filter(EventSlot.id == target_slot_id, EventSlot.event_id == target_event_id)
        .first()
    )
    if not target_slot:
        return redirect("/admin/operations")

    approved_count = (
        db.query(Application)
        .filter(
            Application.slot_id == target_slot.id,
            Application.status == "approved",
            Application.id != application.id,
        )
        .count()
    )
    if approved_count >= target_slot.capacity:
        db.add(
            Notification(
                user_id=admin.id,
                message=f"'{target_event.title}' {target_slot.start_time.strftime('%H:%M')} 슬롯은 이미 마감되어 이동할 수 없습니다.",
                is_read=False,
            )
        )
        db.commit()
        return redirect("/admin/operations")

    application.event_id = target_event_id
    application.slot_id = target_slot_id
    application.status = "pending"
    _set_if_present(application, "updated_at", datetime.utcnow())
    _set_if_present(application, "moved_at", datetime.utcnow())
    _set_if_present(application, "moved_by", admin.id)

    db.add(
        Notification(
            user_id=application.user_id,
            message=f"신청 슬롯이 '{target_event.title}' {target_slot.start_time.strftime('%H:%M')}로 변경되었고 재검토 대기 상태입니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.get("/health")
def health():
    return {"ok": True}