from __future__ import annotations

from uuid import uuid4

from app.firebase_config import db

EVENTS_COLLECTION = "events"


def make_id():
    return uuid4().hex[:10]


def normalize_slot(slot: dict):
    if not isinstance(slot, dict):
        return None

    start_time = str(slot.get("start_time", "")).strip()

    try:
        capacity = int(slot.get("capacity", 0) or 0)
    except Exception:
        capacity = 0

    if not start_time or capacity <= 0:
        return None

    return {
        "id": str(slot.get("id") or make_id()),
        "start_time": start_time,
        "capacity": capacity,
    }


def build_sessions_from_legacy(data: dict):
    # 이미 sessions 구조가 있으면 그대로 사용
    existing_sessions = data.get("sessions")
    if isinstance(existing_sessions, list) and existing_sessions:
        normalized_sessions = []

        for session in existing_sessions:
            if not isinstance(session, dict):
                continue

            session_date = str(session.get("date", "")).strip()
            if not session_date:
                continue

            slots = []
            for slot in session.get("slots", []):
                normalized = normalize_slot(slot)
                if normalized:
                    slots.append(normalized)

            if slots:
                slots.sort(key=lambda x: x["start_time"])
                normalized_sessions.append({
                    "id": str(session.get("id") or make_id()),
                    "date": session_date,
                    "slots": slots,
                })

        if normalized_sessions:
            normalized_sessions.sort(key=lambda x: x["date"])
            return normalized_sessions

    # 예전 구조 1: date + slots
    legacy_date = str(data.get("date", "")).strip()
    legacy_slots = data.get("slots")

    if legacy_date and isinstance(legacy_slots, list) and legacy_slots:
        slots = []
        for slot in legacy_slots:
            normalized = normalize_slot(slot)
            if normalized:
                slots.append(normalized)

        if slots:
            slots.sort(key=lambda x: x["start_time"])
            return [{
                "id": make_id(),
                "date": legacy_date,
                "slots": slots,
            }]

    # 예전 구조 2: date + start_time + capacity
    legacy_start_time = str(data.get("start_time", "")).strip()

    try:
        legacy_capacity = int(data.get("capacity", 0) or 0)
    except Exception:
        legacy_capacity = 0

    if legacy_date and legacy_start_time and legacy_capacity > 0:
        return [{
            "id": make_id(),
            "date": legacy_date,
            "slots": [{
                "id": make_id(),
                "start_time": legacy_start_time,
                "capacity": legacy_capacity,
            }],
        }]

    return []


def main():
    docs = db.collection(EVENTS_COLLECTION).stream()

    converted_count = 0
    skipped_count = 0
    failed_count = 0

    for doc in docs:
        data = doc.to_dict() or {}
        doc_ref = doc.reference

        try:
            sessions = build_sessions_from_legacy(data)

            if not sessions:
                print(f"[SKIP] {doc.id} - 변환 가능한 날짜/슬롯 정보 없음")
                skipped_count += 1
                continue

            update_payload = {
                "title": str(data.get("title", "")).strip(),
                "description": str(data.get("description", "")).strip(),
                "color": str(data.get("color", "#2563eb")).strip() or "#2563eb",
                "note": str(data.get("note", "")).strip(),
                "sessions": sessions,
            }

            # created_by, created_at, updated_at 보존
            if "created_by" in data:
                update_payload["created_by"] = data.get("created_by", "")
            if "created_at" in data:
                update_payload["created_at"] = data.get("created_at", "")
            if "updated_at" in data:
                update_payload["updated_at"] = data.get("updated_at", "")

            doc_ref.update(update_payload)

            print(f"[OK] {doc.id} - sessions 구조로 변환 완료")
            converted_count += 1

        except Exception as e:
            print(f"[FAIL] {doc.id} - {e}")
            failed_count += 1

    print("")
    print("===== migration result =====")
    print(f"converted: {converted_count}")
    print(f"skipped:   {skipped_count}")
    print(f"failed:    {failed_count}")


if __name__ == "__main__":
    main()