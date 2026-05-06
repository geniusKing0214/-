"""Cloud Firestore persistence — Firebase Console에서 Firestore를 켠 뒤 사용."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any, Optional

logger = logging.getLogger(__name__)

COL_USERS = "users"
COL_EVENTS = "events"
COL_SLOTS = "event_slots"
COL_APPLICATIONS = "applications"
COL_NOTIFICATIONS = "notifications"
COL_SCHEDULES = "schedules"
COL_SCHEDULE_APPLICATIONS = "schedule_applications"
def _iso_date(d: date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def _iso_dt(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso_time(t: time) -> str:
    return t.strftime("%H:%M:%S")


def _parse_time(s: str) -> time:
    parts = str(s).strip().split(":")
    h, m = int(parts[0]), int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    return time(h, m, sec)


def get_firestore_client():
    from app.firebase_init import init_firebase_admin

    if not init_firebase_admin():
        raise RuntimeError(
            "Firebase Admin 자격 증명이 없습니다. FIREBASE_CREDENTIALS_JSON 등을 설정하세요."
        )
    from firebase_admin import firestore

    return firestore.client()


class FirestoreDAO:
    """문서 ID = 숫자 id 의 문자열."""

    def __init__(self) -> None:
        self._db = get_firestore_client()

    @property
    def db(self):
        return self._db

    def _coll(self, name: str):
        return self._db.collection(name)

    def allocate_id(self, field: str) -> int:
        """트랜잭션으로 단조 증가 ID."""
        from firebase_admin import firestore

        cref = self._coll("meta").document("counters")

        @firestore.transactional
        def _tx(transaction):
            snap = cref.get(transaction=transaction)
            data = snap.to_dict() or {}
            cur = int(data.get(field, 0))
            nxt = cur + 1
            transaction.set(cref, {field: nxt}, merge=True)
            return nxt

        return _tx(self._db.transaction())

    # --- serialize helpers ---
    def user_to_dict(self, u: Any) -> dict[str, Any]:
        return {
            "username": u.username,
            "nickname": getattr(u, "nickname", None),
            "google_sub": getattr(u, "google_sub", None),
            "firebase_uid": getattr(u, "firebase_uid", None),
            "password": u.password,
            "is_admin": bool(u.is_admin),
            "created_at": _iso_dt(u.created_at) if u.created_at else None,
            "approval_status": getattr(u, "approval_status", "approved") or "approved",
        }

    def dict_to_user(self, uid: int, d: dict[str, Any]) -> Any:
        from app.models import User

        u = User()
        u.id = uid
        u.username = d.get("username", "")
        u.nickname = d.get("nickname")
        u.google_sub = d.get("google_sub")
        u.firebase_uid = d.get("firebase_uid")
        u.password = d.get("password", "")
        u.is_admin = bool(d.get("is_admin", False))
        ca = d.get("created_at")
        u.created_at = _parse_dt(ca) if ca else datetime.utcnow()
        u.approval_status = d.get("approval_status", "approved")
        return u

    def event_to_dict(self, e: Any) -> dict[str, Any]:
        return {
            "title": e.title,
            "event_date": _iso_date(e.event_date),
            "location": getattr(e, "location", None),
            "description": e.description,
            "created_by": e.created_by,
            "created_at": _iso_dt(e.created_at) if e.created_at else None,
        }

    def dict_to_event(self, eid: int, d: dict[str, Any]) -> Any:
        from app.models import Event

        e = Event()
        e.id = eid
        e.title = d.get("title", "")
        e.event_date = _parse_date(d.get("event_date", "1970-01-01"))
        e.location = d.get("location")
        e.description = d.get("description")
        e.created_by = d.get("created_by")
        ca = d.get("created_at")
        e.created_at = _parse_dt(ca) if ca else datetime.utcnow()
        e.slots = []
        return e

    def slot_to_dict(self, s: Any) -> dict[str, Any]:
        return {
            "event_id": s.event_id,
            "start_time": _iso_time(s.start_time),
            "capacity": int(s.capacity),
            "is_active": bool(s.is_active),
        }

    def dict_to_slot(self, sid: int, d: dict[str, Any]) -> Any:
        from app.models import EventSlot

        s = EventSlot()
        s.id = sid
        s.event_id = int(d.get("event_id", 0))
        s.start_time = _parse_time(d.get("start_time", "00:00:00"))
        s.capacity = int(d.get("capacity", 1))
        s.is_active = bool(d.get("is_active", True))
        return s

    def application_to_dict(self, a: Any) -> dict[str, Any]:
        out = {
            "user_id": a.user_id,
            "event_id": a.event_id,
            "slot_id": a.slot_id,
            "status": a.status,
            "created_at": _iso_dt(a.created_at) if a.created_at else None,
        }
        if getattr(a, "reviewed_at", None):
            out["reviewed_at"] = _iso_dt(a.reviewed_at)
        if getattr(a, "reviewed_by", None) is not None:
            out["reviewed_by"] = a.reviewed_by
        return out

    def dict_to_application(self, aid: int, d: dict[str, Any]) -> Any:
        from app.models import Application

        a = Application()
        a.id = aid
        a.user_id = int(d.get("user_id", 0))
        a.event_id = int(d.get("event_id", 0))
        a.slot_id = int(d.get("slot_id", 0))
        a.status = d.get("status", "pending")
        ca = d.get("created_at")
        a.created_at = _parse_dt(ca) if ca else datetime.utcnow()
        ra = d.get("reviewed_at")
        a.reviewed_at = _parse_dt(ra) if ra else None
        a.reviewed_by = d.get("reviewed_by")
        return a

    def notification_to_dict(self, n: Any) -> dict[str, Any]:
        return {
            "user_id": n.user_id,
            "message": n.message,
            "is_read": bool(n.is_read),
            "created_at": _iso_dt(n.created_at) if n.created_at else None,
        }

    def dict_to_notification(self, nid: int, d: dict[str, Any]) -> Any:
        from app.models import Notification

        n = Notification()
        n.id = nid
        n.user_id = int(d.get("user_id", 0))
        n.message = d.get("message", "")
        n.is_read = bool(d.get("is_read", False))
        ca = d.get("created_at")
        n.created_at = _parse_dt(ca) if ca else datetime.utcnow()
        return n

    def schedule_to_dict(self, s: Any) -> dict[str, Any]:
        return {
            "title": s.title,
            "description": getattr(s, "description", None),
            "location": getattr(s, "location", None),
            "event_datetime": _iso_dt(s.event_datetime),
            "recruit_limit": int(getattr(s, "recruit_limit", 0) or 0),
            "status": getattr(s, "status", "open"),
            "created_at": _iso_dt(s.created_at) if s.created_at else None,
        }

    def dict_to_schedule(self, sid: int, d: dict[str, Any]) -> Any:
        from app.models import Schedule

        s = Schedule()
        s.id = sid
        s.title = d.get("title", "")
        s.description = d.get("description")
        s.location = d.get("location")
        s.event_datetime = _parse_dt(d.get("event_datetime", "1970-01-01T00:00:00"))
        s.recruit_limit = int(d.get("recruit_limit", 0))
        s.status = d.get("status", "open")
        ca = d.get("created_at")
        s.created_at = _parse_dt(ca) if ca else datetime.utcnow()
        return s

    def schedule_application_to_dict(self, a: Any) -> dict[str, Any]:
        return {
            "user_id": a.user_id,
            "schedule_id": a.schedule_id,
            "applied_at": _iso_dt(a.applied_at) if a.applied_at else None,
            "status": a.status,
        }

    def dict_to_schedule_application(self, aid: int, d: dict[str, Any]) -> Any:
        from app.models import ScheduleApplication

        a = ScheduleApplication()
        a.id = aid
        a.user_id = int(d.get("user_id", 0))
        a.schedule_id = int(d.get("schedule_id", 0))
        ap = d.get("applied_at")
        a.applied_at = _parse_dt(ap) if ap else datetime.utcnow()
        a.status = d.get("status", "pending")
        return a

    def save_user(self, u: Any) -> None:
        uid = int(u.id)
        self._coll(COL_USERS).document(str(uid)).set(self.user_to_dict(u))

    def delete_user_doc(self, uid: int) -> None:
        self._coll(COL_USERS).document(str(uid)).delete()

    def save_event(self, e: Any) -> None:
        eid = int(e.id)
        self._coll(COL_EVENTS).document(str(eid)).set(self.event_to_dict(e))

    def delete_event_doc(self, eid: int) -> None:
        self._coll(COL_EVENTS).document(str(eid)).delete()

    def save_slot(self, s: Any) -> None:
        sid = int(s.id)
        self._coll(COL_SLOTS).document(str(sid)).set(self.slot_to_dict(s))

    def delete_slot_doc(self, sid: int) -> None:
        self._coll(COL_SLOTS).document(str(sid)).delete()

    def save_application(self, a: Any) -> None:
        aid = int(a.id)
        self._coll(COL_APPLICATIONS).document(str(aid)).set(self.application_to_dict(a))

    def delete_application_doc(self, aid: int) -> None:
        self._coll(COL_APPLICATIONS).document(str(aid)).delete()

    def delete_notification_doc(self, nid: int) -> None:
        self._coll(COL_NOTIFICATIONS).document(str(nid)).delete()

    def delete_schedule_doc(self, sid: int) -> None:
        self._coll(COL_SCHEDULES).document(str(sid)).delete()

    def delete_schedule_application_doc(self, aid: int) -> None:
        self._coll(COL_SCHEDULE_APPLICATIONS).document(str(aid)).delete()

    def save_notification(self, n: Any) -> None:
        nid = int(n.id)
        self._coll(COL_NOTIFICATIONS).document(str(nid)).set(self.notification_to_dict(n))

    def save_schedule(self, s: Any) -> None:
        sid = int(s.id)
        self._coll(COL_SCHEDULES).document(str(sid)).set(self.schedule_to_dict(s))

    def save_schedule_application(self, a: Any) -> None:
        aid = int(a.id)
        self._coll(COL_SCHEDULE_APPLICATIONS).document(str(aid)).set(
            self.schedule_application_to_dict(a)
        )

    def iter_users(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_USERS).stream():
            try:
                uid = int(doc.id)
                out.append(self.dict_to_user(uid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip user doc %s: %s", doc.id, e)
        return out

    def iter_events(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_EVENTS).stream():
            try:
                eid = int(doc.id)
                out.append(self.dict_to_event(eid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip event doc %s: %s", doc.id, e)
        return out

    def iter_slots(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_SLOTS).stream():
            try:
                sid = int(doc.id)
                out.append(self.dict_to_slot(sid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip slot doc %s: %s", doc.id, e)
        return out

    def iter_applications(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_APPLICATIONS).stream():
            try:
                aid = int(doc.id)
                out.append(self.dict_to_application(aid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip application doc %s: %s", doc.id, e)
        return out

    def iter_notifications(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_NOTIFICATIONS).stream():
            try:
                nid = int(doc.id)
                out.append(self.dict_to_notification(nid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip notification doc %s: %s", doc.id, e)
        return out

    def iter_schedules(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_SCHEDULES).stream():
            try:
                sid = int(doc.id)
                out.append(self.dict_to_schedule(sid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip schedule doc %s: %s", doc.id, e)
        return out

    def iter_schedule_applications(self) -> list[Any]:
        out = []
        for doc in self._coll(COL_SCHEDULE_APPLICATIONS).stream():
            try:
                aid = int(doc.id)
                out.append(self.dict_to_schedule_application(aid, doc.to_dict() or {}))
            except Exception as e:
                logger.warning("skip schedule_application doc %s: %s", doc.id, e)
        return out
