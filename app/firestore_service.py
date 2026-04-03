from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from google.cloud.firestore_v1 import FieldFilter
from app.firebase_config import db

EVENTS_COLLECTION = "events"
APPLICATIONS_COLLECTION = "applications"


def _doc_to_dict(doc):
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def _safe_slots(slots):
    result = []
    for slot in slots or []:
        if not isinstance(slot, dict):
            continue

        start_time = str(slot.get("start_time", "")).strip()

        try:
            capacity = int(slot.get("capacity", 0) or 0)
        except Exception:
            capacity = 0

        if not start_time or capacity <= 0:
            continue

        result.append({
            "id": str(slot.get("id") or uuid4().hex[:10]),
            "start_time": start_time,
            "capacity": capacity,
        })

    result.sort(key=lambda x: x.get("start_time", ""))
    return result


def _safe_sessions(sessions):
    result = []
    for session in sessions or []:
        if not isinstance(session, dict):
            continue

        session_date = str(session.get("date", "")).strip()
        slots = _safe_slots(session.get("slots", []))

        if not session_date or not slots:
            continue

        result.append({
            "id": str(session.get("id") or uuid4().hex[:10]),
            "date": session_date,
            "slots": slots,
        })

    result.sort(key=lambda x: x.get("date", ""))
    return result


def get_event(event_id: str):
    doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
    if not doc.exists:
        return None

    item = _doc_to_dict(doc)
    item.setdefault("title", "(제목 없음)")
    item.setdefault("description", "")
    item.setdefault("color", "#2563eb")
    item.setdefault("note", "")
    item["sessions"] = _safe_sessions(item.get("sessions", []))
    return item


def get_events_by_month(year: int, month: int):
    docs = db.collection(EVENTS_COLLECTION).stream()

    month_prefix = f"{year:04d}-{month:02d}-"
    result = []

    for doc in docs:
        item = _doc_to_dict(doc)
        item.setdefault("title", "(제목 없음)")
        item.setdefault("description", "")
        item.setdefault("color", "#2563eb")
        item.setdefault("note", "")
        all_sessions = _safe_sessions(item.get("sessions", []))

        filtered_sessions = [
            session for session in all_sessions
            if str(session.get("date", "")).startswith(month_prefix)
        ]

        if not filtered_sessions:
            continue

        item["sessions"] = filtered_sessions
        result.append(item)

    result.sort(key=lambda x: (
        (x.get("sessions") or [{}])[0].get("date", ""),
        x.get("title", "")
    ))
    return result


def create_event(event_data: dict):
    payload = {
        "title": str(event_data.get("title", "")).strip(),
        "description": str(event_data.get("description", "")).strip(),
        "color": str(event_data.get("color", "#2563eb")).strip() or "#2563eb",
        "note": str(event_data.get("note", "")).strip(),
        "sessions": _safe_sessions(event_data.get("sessions", [])),
        "created_by": event_data.get("created_by", ""),
        "created_at": event_data.get("created_at", ""),
        "updated_at": event_data.get("updated_at", ""),
    }
    ref = db.collection(EVENTS_COLLECTION).document()
    ref.set(payload)
    return ref.id


def update_event(event_id: str, event_data: dict):
    payload = {
        "title": str(event_data.get("title", "")).strip(),
        "description": str(event_data.get("description", "")).strip(),
        "color": str(event_data.get("color", "#2563eb")).strip() or "#2563eb",
        "note": str(event_data.get("note", "")).strip(),
        "sessions": _safe_sessions(event_data.get("sessions", [])),
        "updated_at": event_data.get("updated_at", ""),
    }
    db.collection(EVENTS_COLLECTION).document(event_id).update(payload)


def delete_event(event_id: str):
    db.collection(EVENTS_COLLECTION).document(event_id).delete()

    apps = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("event_id", "==", event_id))
        .stream()
    )
    for doc in apps:
        doc.reference.delete()


def get_user_applications(user_email: str):
    if not user_email:
        return []

    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("user_email", "==", user_email))
        .stream()
    )

    result = []
    for doc in docs:
        item = _doc_to_dict(doc)
        event = get_event(item.get("event_id", ""))
        item["event"] = event
        item["session"] = None
        item["slot"] = None

        if event:
            for session in event.get("sessions", []):
                if session.get("id") == item.get("session_id"):
                    item["session"] = session
                    for slot in session.get("slots", []):
                        if slot.get("id") == item.get("slot_id"):
                            item["slot"] = slot
                            break
                    break

        result.append(item)

    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


def get_session_slot_approved_counts(event_id: str, session_id: str):
    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("event_id", "==", event_id))
        .where(filter=FieldFilter("session_id", "==", session_id))
        .where(filter=FieldFilter("status", "==", "approved"))
        .stream()
    )

    counts = {}
    for doc in docs:
        item = doc.to_dict() or {}
        slot_id = item.get("slot_id")
        if not slot_id:
            continue
        counts[slot_id] = counts.get(slot_id, 0) + 1

    return counts


def apply_to_slot(event_id: str, session_id: str, slot_id: str, user_email: str, user_name: str):
    event = get_event(event_id)
    if not event:
        return {"ok": False, "message": "이벤트를 찾을 수 없습니다."}

    target_session = None
    target_slot = None

    for session in event.get("sessions", []):
        if session.get("id") == session_id:
            target_session = session
            for slot in session.get("slots", []):
                if slot.get("id") == slot_id:
                    target_slot = slot
                    break
            break

    if not target_session or not target_slot:
        return {"ok": False, "message": "세션 또는 슬롯 정보를 찾을 수 없습니다."}

    existing_docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("event_id", "==", event_id))
        .where(filter=FieldFilter("session_id", "==", session_id))
        .where(filter=FieldFilter("user_email", "==", user_email))
        .stream()
    )
    existing_list = list(existing_docs)

    if existing_list:
        return {"ok": False, "message": "이미 이 날짜 일정에 신청했습니다."}

    approved_counts = get_session_slot_approved_counts(event_id, session_id)
    approved_count = approved_counts.get(slot_id, 0)
    capacity = int(target_slot.get("capacity", 0))

    if approved_count >= capacity:
        return {"ok": False, "message": "해당 슬롯 정원이 마감되었습니다."}

    payload = {
        "event_id": event_id,
        "session_id": session_id,
        "slot_id": slot_id,
        "user_email": user_email,
        "user_name": user_name,
        "status": "pending",
        "notification_read": True,
        "notification_message": "",
        "created_at": datetime.now().isoformat(),
    }

    ref = db.collection(APPLICATIONS_COLLECTION).document()
    ref.set(payload)
    return {"ok": True, "id": ref.id}


def approve_application(application_id: str):
    app_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    app_doc = app_ref.get()

    if not app_doc.exists:
        return {"ok": False, "message": "신청 정보를 찾을 수 없습니다."}

    app_data = app_doc.to_dict() or {}
    event = get_event(app_data.get("event_id", ""))
    if not event:
        return {"ok": False, "message": "이벤트를 찾을 수 없습니다."}

    session_id = app_data.get("session_id")
    slot_id = app_data.get("slot_id")

    target_session = None
    target_slot = None
    for session in event.get("sessions", []):
        if session.get("id") == session_id:
            target_session = session
            for slot in session.get("slots", []):
                if slot.get("id") == slot_id:
                    target_slot = slot
                    break
            break

    if not target_session or not target_slot:
        return {"ok": False, "message": "세션/슬롯을 찾을 수 없습니다."}

    approved_counts = get_session_slot_approved_counts(event["id"], session_id)
    approved_count = approved_counts.get(slot_id, 0)
    capacity = int(target_slot.get("capacity", 0))

    if app_data.get("status") != "approved" and approved_count >= capacity:
        return {"ok": False, "message": "해당 슬롯 정원이 가득 찼습니다."}

    event_title = event.get("title") or "신청한 일정"
    session_date = target_session.get("date", "")
    slot_time = target_slot.get("start_time", "")

    app_ref.update({
        "status": "approved",
        "notification_read": False,
        "notification_message": f"'{event_title}' {session_date} {slot_time} 슬롯이 승인되었습니다.",
    })
    return {"ok": True}


def reject_application(application_id: str):
    app_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    app_doc = app_ref.get()

    if not app_doc.exists:
        return {"ok": False, "message": "신청 정보를 찾을 수 없습니다."}

    app_data = app_doc.to_dict() or {}
    event = get_event(app_data.get("event_id", ""))

    event_title = "신청한 일정"
    session_date = ""
    slot_time = ""

    if event:
        event_title = event.get("title") or event_title
        for session in event.get("sessions", []):
            if session.get("id") == app_data.get("session_id"):
                session_date = session.get("date", "")
                for slot in session.get("slots", []):
                    if slot.get("id") == app_data.get("slot_id"):
                        slot_time = slot.get("start_time", "")
                        break
                break

    app_ref.update({
        "status": "rejected",
        "notification_read": False,
        "notification_message": f"'{event_title}' {session_date} {slot_time} 슬롯이 거절되었습니다.",
    })
    return {"ok": True}


def get_pending_requests():
    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("status", "==", "pending"))
        .stream()
    )

    result = []
    for doc in docs:
        item = _doc_to_dict(doc)
        event = get_event(item.get("event_id", ""))
        item["event"] = event
        item["session"] = None
        item["slot"] = None

        if event:
            for session in event.get("sessions", []):
                if session.get("id") == item.get("session_id"):
                    item["session"] = session
                    for slot in session.get("slots", []):
                        if slot.get("id") == item.get("slot_id"):
                            item["slot"] = slot
                            break
                    break

        result.append(item)

    result.sort(key=lambda x: (
        (x.get("session") or {}).get("date", ""),
        ((x.get("slot") or {}).get("start_time", "")),
        x.get("user_name", "") or x.get("user_email", "")
    ))
    return result


def get_event_application_stats(event_id: str):
    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("event_id", "==", event_id))
        .stream()
    )

    pending = 0
    approved = 0
    rejected = 0
    applicants = []

    event = get_event(event_id)
    session_map = {}
    slot_map = {}

    if event:
        for session in event.get("sessions", []):
            session_map[session["id"]] = session
            for slot in session.get("slots", []):
                slot_map[(session["id"], slot["id"])] = slot

    for doc in docs:
        item = _doc_to_dict(doc)
        status = item.get("status", "pending")

        if status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1
        else:
            pending += 1

        item["session"] = session_map.get(item.get("session_id"))
        item["slot"] = slot_map.get((item.get("session_id"), item.get("slot_id")))
        applicants.append(item)

    applicants.sort(key=lambda x: (
        (x.get("session") or {}).get("date", ""),
        (x.get("slot") or {}).get("start_time", ""),
        x.get("user_name", "") or x.get("user_email", "")
    ))

    return {
        "pending_count": pending,
        "approved_count": approved,
        "rejected_count": rejected,
        "total_count": pending + approved + rejected,
        "applicants": applicants,
    }


def enrich_events_with_stats(events: list):
    enriched = []

    for event in events:
        stats = get_event_application_stats(event["id"])
        sessions = []

        for session in event.get("sessions", []):
            approved_counts = get_session_slot_approved_counts(event["id"], session["id"])
            slots = []

            for slot in session.get("slots", []):
                approved_count = approved_counts.get(slot["id"], 0)
                slots.append({
                    **slot,
                    "approved_count": approved_count,
                    "remaining": max(0, int(slot.get("capacity", 0)) - approved_count),
                })

            sessions.append({
                **session,
                "slots": slots,
            })

        enriched.append({
            **event,
            **stats,
            "sessions": sessions,
        })

    return enriched


def get_unread_notifications(user_email: str):
    if not user_email:
        return []

    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("user_email", "==", user_email))
        .where(filter=FieldFilter("notification_read", "==", False))
        .stream()
    )

    result = []
    for doc in docs:
        item = _doc_to_dict(doc)
        event = get_event(item.get("event_id", ""))
        item["event"] = event
        item["session"] = None
        item["slot"] = None

        if event:
            for session in event.get("sessions", []):
                if session.get("id") == item.get("session_id"):
                    item["session"] = session
                    for slot in session.get("slots", []):
                        if slot.get("id") == item.get("slot_id"):
                            item["slot"] = slot
                            break
                    break

        result.append(item)

    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


def mark_notification_as_read(application_id: str):
    db.collection(APPLICATIONS_COLLECTION).document(application_id).update({
        "notification_read": True
    })