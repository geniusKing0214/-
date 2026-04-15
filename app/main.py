from __future__ import annotations

import calendar as calendar_mod
import logging
import os
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app.database import (
    Base,
    SessionLocal,
    engine,
    ensure_events_location_column,
    ensure_schedule_application_status_migration,
    ensure_sqlite_migrations,
)
from app.firebase_init import (
    firebase_google_login_ready,
    get_firebase_web_config,
    verify_firebase_id_token,
)
from app.models import (
    User,
    Event,
    EventSlot,
    Application,
    Notification,
    Schedule,
    ScheduleApplication,
)
from app.password_utils import hash_password, verify_password
from app.routes.member_schedule import router as member_schedule_router
from app.template_globals import attach_template_globals, display_name as user_display_name

logger = logging.getLogger(__name__)

app = FastAPI(title="Scheduler App")

_session_secret = (
    (os.environ.get("SESSION_SECRET_KEY") or "").strip()
    or (os.environ.get("SECRET_KEY") or "").strip()
    or "change-this-secret-key"
)
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


def _redirect_login_for_home(request: Request) -> RedirectResponse:
    """비로그인이 메인(/)에 올 때 — 로그인 후 원래 URL로 돌아가게 next 전달."""
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    safe = _safe_internal_redirect_path(next_path)
    if safe and not safe.startswith("/login"):
        return redirect(f"/login?next={quote(safe, safe='')}&error=need_login")
    return redirect("/login?error=need_login")


def _firebase_login_template_ctx(safe_next: Optional[str]) -> dict[str, Any]:
    """웹 SDK용 config는 키만 있으면 넘김. 서버 검증까지 되면 firebase_auth_enabled=True."""
    ctx: dict[str, Any] = {
        "firebase_auth_enabled": False,
        "firebase_config": None,
    }
    cfg = get_firebase_web_config()
    if cfg:
        ctx["firebase_config"] = cfg
    if cfg and firebase_google_login_ready():
        ctx["firebase_auth_enabled"] = True
    if safe_next:
        ctx["login_next"] = safe_next
    return ctx


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
ensure_events_location_column(engine)
ensure_schedule_application_status_migration(engine)

app.include_router(member_schedule_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
attach_template_globals(templates)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


def _admin_bootstrap_emails() -> set[str]:
    raw = (os.environ.get("ADMIN_BOOTSTRAP_EMAILS") or "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _admin_emails_env() -> Optional[set[str]]:
    """설정되어 있으면(비어 있지 않으면) 이 목록만 관리자. 미설정·빈 값이면 None."""
    raw = (os.environ.get("ADMIN_EMAILS") or "").strip()
    if not raw:
        return None
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _sync_admin_from_env_emails(db: Session, user: User, email: str) -> None:
    """ADMIN_EMAILS가 있으면: 목록 이메일만 관리자+승인, 목록 밖은 관리자 해제."""
    admins = _admin_emails_env()
    if admins is None:
        return
    norm = email.strip().lower()
    changed = False
    if norm in admins:
        if not user.is_admin:
            user.is_admin = True
            changed = True
        if user.approval_status != "approved":
            user.approval_status = "approved"
            changed = True
    else:
        if user.is_admin:
            user.is_admin = False
            changed = True
    if changed:
        _set_if_present(user, "updated_at", datetime.utcnow())
        db.commit()


def _maybe_auto_approve_admin(db: Session, user: User, email: str) -> None:
    """ADMIN_EMAILS 미사용 시에만: 부트스트랩 이메일이거나 DB에 회원이 한 명뿐이면 승인+관리자."""
    if _admin_emails_env() is not None:
        return
    norm = email.strip().lower()
    boot = _admin_bootstrap_emails()
    sole_account = db.query(User).count() == 1
    if not ((boot and norm in boot) or sole_account):
        return
    changed = False
    if user.approval_status != "approved":
        user.approval_status = "approved"
        changed = True
    if not user.is_admin:
        user.is_admin = True
        changed = True
    if changed:
        _set_if_present(user, "updated_at", datetime.utcnow())
        db.commit()


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


def _parse_home_month(raw: Optional[str]) -> tuple[int, int]:
    t = date.today()
    if not raw or not str(raw).strip():
        return t.year, t.month
    s = str(raw).strip()
    try:
        if len(s) >= 7 and s[4] == "-":
            y = int(s[:4])
            m = int(s[5:7])
            if 1 <= m <= 12 and 2000 <= y <= 2100:
                return y, m
    except ValueError:
        pass
    return t.year, t.month


def _shift_calendar_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return y, m


def _parse_sel_day(raw: Optional[str]) -> Optional[date]:
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _home_slots_ui(db: Session, user: Optional[User], event: Event) -> list[dict[str, Any]]:
    """홈 화면 슬롯별 신청 버튼/상태."""
    slots = sorted(
        [s for s in event.slots if s.is_active],
        key=lambda s: s.start_time,
    )
    if not slots:
        return []
    slot_ids = [s.id for s in slots]
    approved_rows = (
        db.query(Application.slot_id, func.count(Application.id))
        .filter(
            Application.slot_id.in_(slot_ids),
            Application.status == "approved",
        )
        .group_by(Application.slot_id)
        .all()
    )
    approved_by_slot = {int(sid): int(n) for sid, n in approved_rows}

    my_by_slot: dict[int, Application] = {}
    if user:
        for app in (
            db.query(Application)
            .filter(
                Application.user_id == user.id,
                Application.slot_id.in_(slot_ids),
            )
            .order_by(Application.id.desc())
            .all()
        ):
            if app.slot_id not in my_by_slot:
                my_by_slot[app.slot_id] = app

    out: list[dict[str, Any]] = []
    for slot in slots:
        approved = approved_by_slot.get(slot.id, 0)
        full = approved >= slot.capacity
        my = my_by_slot.get(slot.id)
        row: dict[str, Any] = {
            "slot": slot,
            "approved": approved,
            "capacity": slot.capacity,
            "full": full,
        }
        if not user:
            row["cta"] = "login"
        elif my and my.status in ("pending", "approved"):
            row["cta"] = "status"
            row["status"] = my.status
        elif full:
            row["cta"] = "full"
        else:
            row["cta"] = "apply"
        out.append(row)
    return out


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
    ctx: dict[str, Any] = _firebase_login_template_ctx(None)
    ctx["auth_mode"] = "register"
    ctx["page_title"] = "회원가입"
    return render(request, "login.html", ctx, db)


@app.post("/register")
def register_post_legacy(request: Request):
    return redirect("/register", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    safe_next = _safe_internal_redirect_path(next)
    ctx: dict[str, Any] = _firebase_login_template_ctx(safe_next)
    ctx["auth_mode"] = "login"
    ctx["page_title"] = "로그인"
    err = (request.query_params.get("error") or "").strip()
    if err == "google_failed":
        ctx["error"] = "Google 로그인에 실패했습니다. 다시 시도해 주세요."
    elif err == "google_email":
        ctx["error"] = "Google 계정에서 이메일 정보를 받지 못했습니다."
    elif err == "google_unverified":
        ctx["error"] = "Google 이메일 인증이 완료된 계정만 사용할 수 있습니다."
    elif err == "google_pending":
        ctx["error"] = "관리자 승인 대기 중입니다. 승인 후 Google 로그인을 사용할 수 있습니다."
    elif err == "google_rejected":
        ctx["error"] = "계정이 거절되어 Google 로그인을 사용할 수 없습니다. 관리자에게 문의해 주세요."
    elif err == "need_login":
        ctx["error"] = "로그인 후 메인 화면과 일정을 이용할 수 있습니다."
    return render(request, "login.html", ctx, db)


@app.post("/login")
def login_post_legacy(request: Request):
    return redirect("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.post("/auth/firebase/session")
async def firebase_auth_session(request: Request, db: Session = Depends(get_db)):
    try:
        if not firebase_google_login_ready():
            return JSONResponse(
                {"ok": False, "detail": "Firebase 로그인이 서버에 설정되지 않았습니다."},
                status_code=503,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"ok": False, "detail": "잘못된 요청입니다."},
                status_code=400,
            )
        id_token = (body.get("id_token") or "").strip()
        next_raw = body.get("next") or ""
        if not id_token:
            return JSONResponse(
                {"ok": False, "detail": "인증 토큰이 없습니다."},
                status_code=400,
            )
        try:
            decoded = verify_firebase_id_token(id_token)
        except Exception:
            return JSONResponse(
                {"ok": False, "detail": "Google 로그인 토큰을 확인할 수 없습니다."},
                status_code=401,
            )

        email = (decoded.get("email") or "").strip()
        uid = (decoded.get("uid") or "").strip()
        if not email or not uid:
            return JSONResponse(
                {"ok": False, "detail": "이메일 정보를 받지 못했습니다."},
                status_code=400,
            )
        if decoded.get("email_verified") is False:
            return JSONResponse(
                {"ok": False, "detail": "Google 이메일 인증이 완료된 계정만 사용할 수 있습니다."},
                status_code=403,
            )
        if len(email) > 255:
            return JSONResponse(
                {"ok": False, "detail": "이메일 주소가 너무 깁니다."},
                status_code=400,
            )

        user = db.query(User).filter(User.firebase_uid == uid).first()
        if user is None:
            existing = db.query(User).filter(User.username == email).first()
            if existing is not None:
                if existing.firebase_uid and existing.firebase_uid != uid:
                    return JSONResponse(
                        {
                            "ok": False,
                            "detail": "이 이메일은 다른 Google 계정과 연결되어 있습니다.",
                        },
                        status_code=409,
                    )
                existing.firebase_uid = uid
                user = existing
                db.commit()
            else:
                user = User(
                    username=email,
                    nickname=None,
                    firebase_uid=uid,
                    password=hash_password(secrets.token_hex(24)),
                    is_admin=False,
                    approval_status="pending_approval",
                )
                db.add(user)
                db.commit()

        _sync_admin_from_env_emails(db, user, email)
        _maybe_auto_approve_admin(db, user, email)

        approval_status = _get_user_approval_status(user)
        if approval_status != "approved":
            if approval_status == "pending_approval":
                return JSONResponse(
                    {
                        "ok": False,
                        "detail": "관리자 승인 대기 중입니다. 승인 후 Google 로그인을 사용할 수 있습니다.",
                    },
                    status_code=403,
                )
            if approval_status == "rejected":
                return JSONResponse(
                    {
                        "ok": False,
                        "detail": "계정이 거절되어 Google 로그인을 사용할 수 없습니다.",
                    },
                    status_code=403,
                )
            return JSONResponse(
                {"ok": False, "detail": "계정 상태를 확인할 수 없습니다."},
                status_code=403,
            )

        request.session["user_id"] = user.id
        dest = _safe_internal_redirect_path(str(next_raw).strip()) or "/"
        return JSONResponse({"ok": True, "redirect": dest})
    except Exception:
        logger.exception("firebase_auth_session failed")
        try:
            db.rollback()
        except Exception:
            pass
        return JSONResponse(
            {
                "ok": False,
                "detail": "서버 오류가 발생했습니다. 잠시 후 다시 시도하거나 Render 로그를 확인해 주세요.",
            },
            status_code=500,
        )


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
            "page_title": "내 정보",
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
                "page_title": "내 정보",
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
                "page_title": "내 정보",
                "error": "닉네임은 50자 이하로 입력해주세요.",
            },
            db,
            current_user=user,
        )
    user.nickname = nick if nick else None
    db.commit()
    return redirect("/profile?updated=1")


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    month: Optional[str] = Query(None),
    sel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if not current_user:
        return _redirect_login_for_home(request)
    approval = getattr(current_user, "approval_status", "approved")
    if approval != "approved":
        request.session.clear()
        if approval == "rejected":
            return redirect("/login?error=google_rejected")
        return redirect("/login?error=google_pending")

    y, m = _parse_home_month(month)
    month_param = f"{y}-{m:02d}"
    py, pm = _shift_calendar_month(y, m, -1)
    ny, nm = _shift_calendar_month(y, m, 1)
    first = date(y, m, 1)
    last = date(y, m, calendar_mod.monthrange(y, m)[1])

    events_in_month = (
        db.query(Event)
        .options(joinedload(Event.slots))
        .filter(Event.event_date >= first, Event.event_date <= last)
        .order_by(Event.event_date.asc(), Event.id.asc())
        .all()
    )
    for e in events_in_month:
        e.slots = [s for s in e.slots if s.is_active]

    by_date: dict[date, list[Event]] = {}
    for e in events_in_month:
        by_date.setdefault(e.event_date, []).append(e)

    cal = calendar_mod.Calendar(firstweekday=0)
    cal_weeks: list[list[dict[str, Any]]] = []
    for week in cal.monthdatescalendar(y, m):
        row: list[dict[str, Any]] = []
        for d in week:
            n = len(by_date.get(d, []))
            row.append(
                {
                    "date": d,
                    "in_month": d.month == m,
                    "is_today": d == date.today(),
                    "event_count": n,
                }
            )
        cal_weeks.append(row)

    sel_day = _parse_sel_day(sel)
    if sel_day and (sel_day.year, sel_day.month) != (y, m):
        sel_day = None

    def _blocks(evts: list[Event]) -> list[dict[str, Any]]:
        return [
            {
                "event": e,
                "slots_ui": _home_slots_ui(db, current_user, e),
                "time_label": _event_time_label(e),
            }
            for e in evts
        ]

    if sel_day:
        detail_groups = [
            {
                "label": _day_label_ko(sel_day),
                "blocks": _blocks(by_date.get(sel_day, [])),
            }
        ]
    else:
        detail_groups = [
            {
                "label": _day_label_ko(d),
                "blocks": _blocks(by_date[d]),
            }
            for d in sorted(by_date.keys())
        ]

    has_detail_blocks = any(g["blocks"] for g in detail_groups)

    approved_member_schedule_ids = [
        row[0]
        for row in db.query(ScheduleApplication.schedule_id)
        .filter(
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status == "approved",
        )
        .order_by(ScheduleApplication.id.asc())
        .all()
    ]
    if approved_member_schedule_ids:
        schedules = (
            db.query(Schedule)
            .filter(Schedule.id.in_(approved_member_schedule_ids))
            .order_by(Schedule.event_datetime.asc())
            .all()
        )
    else:
        schedules = []

    ctx: dict[str, Any] = {
        "page_title": "스케줄",
        "cal_year": y,
        "cal_month": m,
        "month_param": month_param,
        "prev_month_param": f"{py}-{pm:02d}",
        "next_month_param": f"{ny}-{nm:02d}",
        "cal_weeks": cal_weeks,
        "weekday_headers": ("월", "화", "수", "목", "금", "토", "일"),
        "detail_groups": detail_groups,
        "has_detail_blocks": has_detail_blocks,
        "sel_day": sel_day,
        "has_month_events": bool(events_in_month),
        "member_schedules": schedules,
    }

    return render(
        request,
        "home.html",
        ctx,
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

    # join + joinedload(Application.event) 동시 사용 시 SQLAlchemy가 경로를 꼬아 500이 날 수 있음(특히 Postgres).
    applications = (
        db.query(Application)
        .options(joinedload(Application.event), joinedload(Application.slot))
        .filter(Application.user_id == user.id)
        .all()
    )
    applications = [
        a
        for a in applications
        if a.event is not None and a.slot is not None
    ]
    applications.sort(key=_application_event_dt, reverse=True)

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
        {"page_title": "알림", "notifications": notifications},
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


@app.get("/admin/events/new", response_class=HTMLResponse)
def admin_event_create_page(request: Request, db: Session = Depends(get_db)):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")
    return render(
        request,
        "admin_event_create.html",
        {"page_title": "스케줄 생성"},
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


@app.post("/admin/members/{user_id}/nickname")
def admin_member_set_nickname(
    user_id: int,
    request: Request,
    nickname: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return redirect("/admin/members")

    nick = nickname.strip()
    if nick and len(nick) < 2:
        members = db.query(User).order_by(User.id.asc()).all()
        return render(
            request,
            "admin_members.html",
            {
                "page_title": "회원 목록",
                "members": members,
                "member_error_id": user_id,
                "member_error_msg": "닉네임은 2자 이상이거나 비워두세요.",
            },
            db,
            current_user=admin,
        )
    if len(nick) > 50:
        members = db.query(User).order_by(User.id.asc()).all()
        return render(
            request,
            "admin_members.html",
            {
                "page_title": "회원 목록",
                "members": members,
                "member_error_id": user_id,
                "member_error_msg": "닉네임은 50자 이하로 입력해주세요.",
            },
            db,
            current_user=admin,
        )

    target.nickname = nick if nick else None
    db.commit()
    return redirect("/admin/members")


@app.post("/admin/events/create")
def create_event(
    request: Request,
    title: str = Form(...),
    event_date: str = Form(...),
    location: str = Form(""),
    description: str = Form(""),
    slot_times: list[str] = Form(...),
    slot_capacities: list[int] = Form(...),
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    ev_date = datetime.strptime(event_date, "%Y-%m-%d").date()
    loc = location.strip()
    event = Event(
        title=title.strip(),
        event_date=ev_date,
        location=loc if loc else None,
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
    return redirect(f"/?month={ev_date.year}-{ev_date.month:02d}&sel={ev_date.isoformat()}")


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

    member_schedule_applications_pending = (
        db.query(ScheduleApplication)
        .options(
            joinedload(ScheduleApplication.user),
            joinedload(ScheduleApplication.schedule),
        )
        .filter(ScheduleApplication.status == "pending")
        .order_by(ScheduleApplication.applied_at.desc())
        .all()
    )

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
            "page_title": "운영 · 회원가입 승인",
            "applications": applications,
            "pending_users": pending_users,
            "notifications": notifications,
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "member_schedule_applications_pending": member_schedule_applications_pending,
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


@app.post("/admin/member-schedule-applications/{application_id}/approve")
def approve_member_schedule_application(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    sa = (
        db.query(ScheduleApplication)
        .options(
            joinedload(ScheduleApplication.user),
            joinedload(ScheduleApplication.schedule),
        )
        .filter(ScheduleApplication.id == application_id)
        .first()
    )
    if not sa or sa.status != "pending":
        return redirect("/admin/operations")

    sa.status = "approved"
    db.add(
        Notification(
            user_id=sa.user_id,
            message=f"'{sa.schedule.title}' 별도 모집 신청이 승인되었습니다.",
            is_read=False,
        )
    )
    db.commit()
    return redirect("/admin/operations")


@app.post("/admin/member-schedule-applications/{application_id}/reject")
def reject_member_schedule_application(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        admin = admin_required(request, db)
    except PermissionError:
        return redirect("/login")

    sa = (
        db.query(ScheduleApplication)
        .options(
            joinedload(ScheduleApplication.user),
            joinedload(ScheduleApplication.schedule),
        )
        .filter(ScheduleApplication.id == application_id)
        .first()
    )
    if not sa or sa.status != "pending":
        return redirect("/admin/operations")

    sa.status = "rejected"
    db.add(
        Notification(
            user_id=sa.user_id,
            message=f"'{sa.schedule.title}' 별도 모집 신청이 거절되었습니다.",
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