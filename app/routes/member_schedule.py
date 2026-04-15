from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notification, Schedule, ScheduleApplication, User
from app.template_globals import attach_template_globals
from app.utils.auth import get_current_user

router = APIRouter(prefix="/member/schedules", tags=["member_schedules"])
templates = Jinja2Templates(directory="app/templates")
attach_template_globals(templates)

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")

# 정원·중복 신청: 대기+승인 모두 점유. 취소 가능: 대기 또는 승인.
_MEMBER_SCHEDULE_HOLD_STATUSES = ("pending", "approved")
_MEMBER_SCHEDULE_LIST_STATUSES = ("pending", "approved", "rejected")


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


def _member_unread_count(user: User, db: Session) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .count()
    )


@router.get("")
def member_schedule_list():
    """공개 목록은 /browse, 홈 앵커는 승인된 나의 일정."""
    return RedirectResponse(
        url="/member/schedules/browse",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _schedule_hold_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(
            ScheduleApplication.schedule_id,
            func.count(ScheduleApplication.id),
        )
        .filter(ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES))
        .group_by(ScheduleApplication.schedule_id)
        .all()
    )
    return {int(sid): int(n) for sid, n in rows}


@router.get("/browse")
def browse_open_member_schedules(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now()
    schedules = (
        db.query(Schedule)
        .filter(Schedule.status == "open", Schedule.event_datetime >= now)
        .order_by(Schedule.event_datetime.asc())
        .all()
    )
    holds = (
        db.query(ScheduleApplication)
        .filter(
            ScheduleApplication.user_id == current_user.id,
            ScheduleApplication.status.in_(_MEMBER_SCHEDULE_HOLD_STATUSES),
        )
        .all()
    )
    schedule_hold_status = {a.schedule_id: a.status for a in holds}
    application_counts = _schedule_hold_counts(db)

    return templates.TemplateResponse(
        "member_schedule_list.html",
        {
            "request": request,
            "current_user": current_user,
            "page_title": "모집 중 별도 일정",
            "unread_count": _member_unread_count(current_user, db),
            "schedules": schedules,
            "application_counts": application_counts,
            "schedule_hold_status": schedule_hold_status,
        },
    )


@router.get("/my/list")
def my_schedule_applications(
    request: Request,
    group: str = Query("day"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if group not in ("day", "week"):
        group = "day"

    applications = (
        db.query(ScheduleApplication)
        .join(Schedule, Schedule.id == ScheduleApplication.schedule_id)
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

    return templates.TemplateResponse(
        "my_schedule_applications.html",
        {
            "request": request,
            "current_user": current_user,
            "page_title": "내 신청",
            "unread_count": _member_unread_count(current_user, db),
            "application_groups": application_groups,
            "group_mode": group,
        },
    )


@router.get("/{schedule_id}")
def member_schedule_detail(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
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

    return templates.TemplateResponse(
        "member_schedule_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "page_title": "별도 모집 상세",
            "unread_count": _member_unread_count(current_user, db),
            "schedule": schedule,
            "my_application": my_application,
            "hold_count": hold_count,
            "approved_count": approved_count,
        },
    )


@router.post("/{schedule_id}/apply")
def apply_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
        url=f"/member/schedules/{schedule_id}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{schedule_id}/cancel")
def cancel_schedule_application(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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

    return RedirectResponse(
        url=f"/member/schedules/{schedule_id}",
        status_code=status.HTTP_303_SEE_OTHER
    )