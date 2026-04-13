from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from passlib.context import CryptContext

from . import models

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_user(db: Session, username: str, password: str, is_admin: bool = False):
    user = models.User(
        username=username,
        password=hash_password(password),
        is_admin=is_admin
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()


def create_notification(db: Session, user_id: int, message: str):
    notice = models.Notification(user_id=user_id, message=message)
    db.add(notice)
    db.commit()
    return notice


def get_unread_notification_count(db: Session, user_id: int) -> int:
    return db.query(models.Notification).filter(
        models.Notification.user_id == user_id,
        models.Notification.is_read == False
    ).count()


def approved_count_for_slot(db: Session, slot_id: int) -> int:
    return db.query(models.Application).filter(
        models.Application.slot_id == slot_id,
        models.Application.status == "approved"
    ).count()


def remaining_capacity_for_slot(db: Session, slot_id: int) -> int:
    slot = db.query(models.EventSlot).filter(models.EventSlot.id == slot_id).first()
    if not slot:
        return 0
    approved = approved_count_for_slot(db, slot_id)
    remaining = slot.capacity - approved
    return max(remaining, 0)


def create_event_with_slots(db: Session, admin_id: int, title: str, event_date, description: str, slots_data: list):
    event = models.Event(
        title=title,
        event_date=event_date,
        description=description,
        created_by=admin_id
    )
    db.add(event)
    db.flush()

    for slot_data in slots_data:
        slot = models.EventSlot(
            event_id=event.id,
            start_time=slot_data["start_time"],
            capacity=slot_data["capacity"],
            is_active=True
        )
        db.add(slot)

    db.commit()
    db.refresh(event)
    return event


def get_events_grouped_by_date(db: Session):
    events = db.query(models.Event).order_by(models.Event.event_date.asc()).all()
    grouped = {}
    for event in events:
        key = event.event_date.strftime("%Y-%m-%d")
        grouped.setdefault(key, []).append(event)
    return grouped


def user_has_application_in_event(db: Session, user_id: int, event_id: int):
    return db.query(models.Application).filter(
        models.Application.user_id == user_id,
        models.Application.event_id == event_id
    ).first()


def create_application(db: Session, user_id: int, event_id: int, slot_id: int):
    app = models.Application(
        user_id=user_id,
        event_id=event_id,
        slot_id=slot_id,
        status="pending"
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def approve_application(db: Session, application_id: int, admin_id: int):
    app = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not app:
        return None, "신청 내역이 없습니다."

    if app.status != "pending":
        return None, "이미 처리된 신청입니다."

    remaining = remaining_capacity_for_slot(db, app.slot_id)
    if remaining <= 0:
        return None, "해당 슬롯 정원이 가득 찼습니다."

    app.status = "approved"
    app.reviewed_at = datetime.utcnow()
    app.reviewed_by = admin_id
    db.commit()
    db.refresh(app)
    return app, None


def reject_application(db: Session, application_id: int, admin_id: int):
    app = db.query(models.Application).filter(models.Application.id == application_id).first()
    if not app:
        return None, "신청 내역이 없습니다."

    if app.status != "pending":
        return None, "이미 처리된 신청입니다."

    app.status = "rejected"
    app.reviewed_at = datetime.utcnow()
    app.reviewed_by = admin_id
    db.commit()
    db.refresh(app)
    return app, None