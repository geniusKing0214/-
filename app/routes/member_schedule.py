import calendar as calendar_mod
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.cal_grid import build_cal_weeks, schedule_calendar_date
from app.database import get_db
from app.models import Schedule, ScheduleApplication, User

router = APIRouter(prefix="/member/schedules", tags=["member_schedules"])

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")

# 정원·중복 신청: 대기+승인 모두 점유. 취소 가능: 대기 또는 승인.
_MEMBER_SCHEDULE_HOLD_STATUSES = ("pending", "approved")
_MEMBER_SCHEDULE_LIST_STATUSES = ("pending", "approved", "rejected", "cancelled")
# 달력·「내 승인 별도 일정」목록: 이 유저에게 관리자 승인된 건만 (applied 는 구버전 DB 호환)
_MEMBER_APPROVED_CALENDAR_STATUSES = ("approved", "applied")


def _day_label_ko(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일 ({_WEEKDAY_KO[d.weekday()]})"


def _week_range_label_ko(ws: date, we: date) -> str:
    return (
        f"{ws.year}년 {ws.month}월 {ws.day}일"
        f" ~ {we.year}년 {we.month}월 {we.day}일"
    )


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _group_member_apps_by_day(
    applications: list[ScheduleApplication],
) -> list[dict[str, Any]]:
    apps_sorted = sorted(
        applications,
        key=lambda a: a.schedule.event_datetime,
        reverse=True,
    )
    order: list[date] = []
    by_day: dict[date, list[ScheduleApplication]] = {}
    for app in apps_sorted:
        d = app.schedule.event_datetime.date()
        if d not in by_day:
            by_day[d] = []
            order.append(d)
        by_day[d].append(app)
    return [{"label": _day_label_ko(d), "items": by_day[d]} for d in order]


def _group_member_apps_by_week(
    applications: list[ScheduleApplication],
) -> list[dict[str, Any]]:
    apps_sorted = sorted(
        applications,
        key=lambda a: a.schedule.event_datetime,
        reverse=True,
    )
    order: list[date] = []
    by_week: dict[date, list[ScheduleApplication]] = {}
    for app in apps_sorted:
        ws = _week_start_monday(app.schedule.event_datetime.date())
        if ws not in by_week:
            by_week[ws] = []
            order.append(ws)
        by_week[ws].append(app)
    return [
        {
            "label": _week_range_label_ko(ws, ws + timedelta(days=6)),
            "items": by_week[ws],
        }
        for ws in order
    ]


def require_approved_user(request: Request, db: Session = Depends(get_db)) -> User:
    """세션 기반 로그인·승인 확인(문자열 user_id 정수화 포함). HTML 라우트용."""
    raw = request.session.get("user_id")
    if raw is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자 정보를 찾을 수 없습니다.")
    approval = getattr(user, "approval_status", "approved")
    if approval != "approved":
        if approval == "pending_approval":
            detail = "관리자 승인 대기 중입니다."
        elif approval == "rejected":
            detail = "관리자에 의해 계정이 거절되었습니다."
        else:
            detail = "계정 상태를 확인할 수 없습니다."
        raise HTTPException(status_code=403, detail=detail)
    return user


def _member_html(
    request: Request, template: str, ctx: Dict[str, Any], db: Session, user: User
):
    """main.render 와 동일한 템플릿 컨텍스트(순환 import 회피용 지연 import)."""
    from app.main import render

    return render(request, template, ctx, db, current_user=user)


@router.get("")
def member_schedule_list(
    request: Request,
    month: Optional[str] = Query(None),
    sel: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    """내게 승인된 별도 일정 — 달력 칸에 표시, 날짜 선택 시 아래에 상세."""
    from app.main import _parse_home_month, _parse_sel_day, _shift_calendar_month

    y, m = _parse_home_month(month)
    month_param = f"{y}-{m:02d}"
    py, pm = _shift_calendar_month(y, m, -1)
    ny, nm = _shift_calendar_month(y, m, 1)
    first = date(y, m, 1)
    last = date(y, m, calendar_mod.monthrange(y, m)[1])

    approved = (
        db.query(Schedule)
        .join(
            ScheduleApplication,
            (ScheduleApplication.schedule_id == Schedule.id)
            & (ScheduleApplication.user_id == current_user.id)
            & (
                ScheduleApplication.status.in_(_MEMBER_APPROVED_CALENDAR_STATUSES)
            ),
        )
        .distinct()
        .order_by(Schedule.event_datetime.asc())
        .all()
    )
    member_schedules_by_date: dict[date, list] = {}
    for s in approved:
        sd = schedule_calendar_date(s)
        if first <= sd <= last:
            member_schedules_by_date.setdefault(sd, []).append(s)

    cal_weeks = build_cal_weeks(
        y,
        m,
        view="member_personal",
        by_date={},
        member_schedules_by_date=member_schedules_by_date,
    )

    sel_day = _parse_sel_day(sel)
    if sel_day and (sel_day.year, sel_day.month) != (y, m):
        sel_day = None

    if sel_day:
        schedules = member_schedules_by_date.get(sel_day, [])
    else:
        schedules = []

    has_month_events = bool(member_schedules_by_date)

    return _member_html(
        request,
        "member_schedule_list.html",
        {
            "page_title": "내 승인 별도 일정",
            "schedules": schedules,
            "cal_year": y,
            "cal_month": m,
            "month_param": month_param,
            "prev_month_param": f"{py}-{pm:02d}",
            "next_month_param": f"{ny}-{nm:02d}",
            "cal_weeks": cal_weeks,
            "weekday_headers": ("월", "화", "수", "목", "금", "토", "일"),
            "sel_day": sel_day,
            "has_month_events": has_month_events,
            "sched_calendar_base_path": "/member/schedules",
            "sched_show_member_cal_chips": True,
            "sched_cal_wrap_class": "sched-cal-wrap--member-chips",
        },
        db,
        current_user,
    )


@router.get("/browse")
def browse_open_member_schedules():
    """레거시 URL: 모집 중 목록으로 안내."""
    return RedirectResponse(
        url="/member/schedules/open",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/open")
def member_schedule_recruiting(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    """모집 중인 별도 일정 전체 — 신청·검토용."""
    now_dt = datetime.now()
    schedules = (
        db.query(Schedule)
        .filter(Schedule.status == "open", Schedule.event_datetime >= now_dt)
        .order_by(Schedule.event_datetime.asc())
        .all()
    )
    hold_rows = (
        db.query(
            ScheduleApplication.schedule_id,
            func.count(ScheduleApplication.id),
        )
        .filter(ScheduleApplication.status.in_(("pending", "approved")))
        .group_by(ScheduleApplication.schedule_id)
        .all()
    )
    application_counts = {int(sid): int(n) for sid, n in hold_rows}
    user_holds = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(("pending", "approved")),
        )
        .all()
    )
    schedule_hold_status = {a.schedule_id: a.status for a in user_holds}

    return _member_html(
        request,
        "member_schedule_recruiting.html",
        {
            "page_title": "모집 중 별도 일정",
            "schedules": schedules,
            "application_counts": application_counts,
            "schedule_hold_status": schedule_hold_status,
        },
        db,
        current_user,
    )


@router.get("/my/list")
def my_schedule_applications(
    request: Request,
    group: str = Query("day"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    if group not in ("day", "week"):
        group = "day"

    applications = (
        db.query(ScheduleApplication)
        .join(Schedule, Schedule.id == ScheduleApplication.schedule_id)
        .options(joinedload(ScheduleApplication.schedule))
        .filter(
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_LIST_STATUSES),
        )
        .order_by(Schedule.event_datetime.desc())
        .all()
    )

    if group == "week":
        application_groups = _group_member_apps_by_week(applications)
    else:
        application_groups = _group_member_apps_by_day(applications)

    return _member_html(
        request,
        "my_schedule_applications.html",
        {
            "page_title": "내 신청",
            "application_groups": application_groups,
            "group_mode": group,
        },
        db,
        current_user,
    )


@router.get("/{schedule_id}")
def member_schedule_detail(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="스케줄을 찾을 수 없습니다.")

    if schedule.status != "open":
        may_view = (
            db.query(ScheduleApplication.id)
            .filter(
                ScheduleApplication.schedule_id == schedule_id,
                ScheduleApplication.user_id == current_user.id,
                ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
            )
            .first()
        )
        if not may_view:
            raise HTTPException(status_code=404, detail="스케줄을 찾을 수 없습니다.")

    my_application = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .first()
    )

    hold_count = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .count()
    )
    approved_count = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.status == "approved",
        )
        .count()
    )

    return _member_html(
        request,
        "member_schedule_detail.html",
        {
            "page_title": "별도 모집 상세",
            "schedule": schedule,
            "my_application": my_application,
            "hold_count": hold_count,
            "approved_count": approved_count,
        },
        db,
        current_user,
    )


@router.post("/{schedule_id}/apply")
def apply_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="스케줄을 찾을 수 없습니다.")

    if schedule.status != "open":
        raise HTTPException(status_code=400, detail="모집이 마감된 이벤트입니다.")

    if schedule.event_datetime < datetime.now():
        raise HTTPException(status_code=400, detail="이미 지난 이벤트에는 신청할 수 없습니다.")

    existing = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="이미 신청한 이벤트입니다.")

    current_count = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .count()
    )

    if schedule.recruit_limit > 0 and current_count >= schedule.recruit_limit:
        raise HTTPException(status_code=400, detail="모집 인원이 마감되었습니다.")

    application = ScheduleApplication(
        user_id=current_user.id,
        schedule_id=schedule_id,
        status="pending",
    )
    db.add(application)
    db.commit()

    return RedirectResponse(
        url="/member/schedules/my/list",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{schedule_id}/cancel")
def cancel_schedule_application(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_approved_user),
):
    application = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.schedule_id == schedule_id,
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .first()
    )

    if not application:
        raise HTTPException(status_code=404, detail="신청 내역이 없습니다.")

    application.status = "cancelled"
    db.commit()

    return RedirectResponse(url="/member/schedules", status_code=status.HTTP_303_SEE_OTHER)