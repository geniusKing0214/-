from google.cloud.firestore_v1 import FieldFilter
from app.firebase_config import db

EVENTS_COLLECTION = "events"
APPLICATIONS_COLLECTION = "applications"


def _doc_to_dict(doc):
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def get_events_by_month(year: int, month: int):
    start_date = f"{year:04d}-{month:02d}-01"

    if month == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month + 1

    end_date = f"{next_year:04d}-{next_month:02d}-01"

    docs = (
        db.collection(EVENTS_COLLECTION)
        .where(filter=FieldFilter("date", ">=", start_date))
        .where(filter=FieldFilter("date", "<", end_date))
        .order_by("date")
        .stream()
    )

    result = []
    for doc in docs:
        item = _doc_to_dict(doc)
        item.setdefault("start_time", "")
        item.setdefault("title", "(제목 없음)")
        item.setdefault("date", "")
        item.setdefault("capacity", 0)
        item.setdefault("description", "")
        item.setdefault("color", "#2563eb")
        item.setdefault("note", "")
        result.append(item)

    result.sort(key=lambda x: (x.get("date", ""), x.get("start_time", ""), x.get("title", "")))
    return result


def create_event(event_data: dict):
    ref = db.collection(EVENTS_COLLECTION).document()
    ref.set(event_data)
    return ref.id


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
        event_id = item.get("event_id")
        item["event"] = None

        if event_id:
            event_doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
            if event_doc.exists:
                event_item = _doc_to_dict(event_doc)
                event_item.setdefault("color", "#2563eb")
                event_item.setdefault("note", "")
                item["event"] = event_item

        result.append(item)

    return result


def apply_to_event(event_id: str, user_email: str, user_name: str):
    existing = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("event_id", "==", event_id))
        .where(filter=FieldFilter("user_email", "==", user_email))
        .stream()
    )

    existing_list = list(existing)
    if existing_list:
        return existing_list[0].id

    payload = {
        "event_id": event_id,
        "user_email": user_email,
        "user_name": user_name,
        "status": "pending",
        "notification_read": True,
        "notification_message": "",
    }

    ref = db.collection(APPLICATIONS_COLLECTION).document()
    ref.set(payload)
    return ref.id


def approve_application(application_id: str):
    app_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    app_doc = app_ref.get()

    if not app_doc.exists:
        return

    app_data = app_doc.to_dict() or {}
    event_title = "신청한 일정"
    event_id = app_data.get("event_id")

    if event_id:
        event_doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
        if event_doc.exists:
            event_data = event_doc.to_dict() or {}
            event_title = event_data.get("title") or event_title

    app_ref.update({
        "status": "approved",
        "notification_read": False,
        "notification_message": f"'{event_title}' 일정이 승인되었습니다.",
    })


def reject_application(application_id: str):
    app_ref = db.collection(APPLICATIONS_COLLECTION).document(application_id)
    app_doc = app_ref.get()

    if not app_doc.exists:
        return

    app_data = app_doc.to_dict() or {}
    event_title = "신청한 일정"
    event_id = app_data.get("event_id")

    if event_id:
        event_doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
        if event_doc.exists:
            event_data = event_doc.to_dict() or {}
            event_title = event_data.get("title") or event_title

    app_ref.update({
        "status": "rejected",
        "notification_read": False,
        "notification_message": f"'{event_title}' 일정이 거절되었습니다.",
    })


def get_pending_requests():
    docs = (
        db.collection(APPLICATIONS_COLLECTION)
        .where(filter=FieldFilter("status", "==", "pending"))
        .stream()
    )

    result = []
    for doc in docs:
        item = _doc_to_dict(doc)
        event_id = item.get("event_id")
        item["event"] = None

        if event_id:
            event_doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
            if event_doc.exists:
                event_item = _doc_to_dict(event_doc)
                event_item.setdefault("color", "#2563eb")
                event_item.setdefault("note", "")
                item["event"] = event_item

        result.append(item)

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

    for doc in docs:
        item = _doc_to_dict(doc)
        status = item.get("status", "pending")

        if status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1
        else:
            pending += 1

        applicants.append(item)

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
        enriched.append({
            **event,
            **stats,
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
        event_id = item.get("event_id")
        item["event"] = None

        if event_id:
            event_doc = db.collection(EVENTS_COLLECTION).document(event_id).get()
            if event_doc.exists:
                item["event"] = _doc_to_dict(event_doc)

        result.append(item)

    return result


def mark_notification_as_read(application_id: str):
    db.collection(APPLICATIONS_COLLECTION).document(application_id).update({
        "notification_read": True
    })