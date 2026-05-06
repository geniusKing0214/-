"""Firestore-backed session mimicking a subset of sqlalchemy.orm.Session used by this app."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Optional, Union

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList
from sqlalchemy.sql.expression import UnaryExpression

from app.firestore_dao import FirestoreDAO
from app.models import (
    Application,
    Event,
    EventSlot,
    Notification,
    Schedule,
    ScheduleApplication,
    User,
)

logger = logging.getLogger(__name__)

MODEL_COUNTER_FIELD = {
    User: "users",
    Event: "events",
    EventSlot: "event_slots",
    Application: "applications",
    Notification: "notifications",
    Schedule: "schedules",
    ScheduleApplication: "schedule_applications",
}


def _col_key(elem: Any) -> Optional[str]:
    if elem is None:
        return None
    k = getattr(elem, "key", None)
    if k:
        return str(k)
    iexp = getattr(elem, "clause_element", None)
    if iexp is not None:
        k2 = getattr(iexp, "key", None)
        if k2:
            return str(k2)
    return None


def _literal_value(right: Any) -> Any:
    if right is None:
        return None
    v = getattr(right, "value", None)
    if v is not None:
        return v
    ev = getattr(right, "effective_value", None)
    if ev is not None:
        return ev
    try:
        return right.value  # type: ignore[attr-defined]
    except Exception:
        pass
    return right


def _matches(entity: Any, crit: Any) -> bool:
    """Evaluate SQL criterion against a mapped instance (AND-combine compound parts)."""
    if crit is None:
        return True
    if isinstance(crit, BooleanClauseList):
        op = getattr(crit, "operator", None)
        clauses = getattr(crit, "clauses", ()) or ()
        if op is operators.and_:
            return all(_matches(entity, c) for c in clauses)
        if op is operators.or_:
            return any(_matches(entity, c) for c in clauses)
        return all(_matches(entity, c) for c in clauses)
    if isinstance(crit, BinaryExpression):
        op = crit.operator
        lk = _col_key(crit.left)
        rv = _literal_value(crit.right)
        if lk is None:
            return True
        ev = getattr(entity, lk)
        if op is operators.eq:
            return ev == rv
        if op is operators.ne:
            return ev != rv
        if op is operators.gt:
            return ev > rv
        if op is operators.ge:
            return ev >= rv
        if op is operators.lt:
            return ev < rv
        if op is operators.le:
            return ev <= rv
        if op is operators.in_op:
            if rv is None:
                return False
            try:
                it = list(rv) if not isinstance(rv, (str, bytes)) else [rv]
            except TypeError:
                it = [rv]
            return ev in it
        if op is operators.is_:
            return ev is None if rv is None else ev is rv
        logger.warning("unsupported FS criterion op=%s left=%s", op, lk)
        return True
    logger.warning("unsupported FS criterion type=%s", type(crit))
    return True


def _extract_user_id_from_crit(crit: Any) -> Optional[int]:
    if crit is None:
        return None
    if isinstance(crit, BooleanClauseList):
        for c in getattr(crit, "clauses", ()) or ():
            u = _extract_user_id_from_crit(c)
            if u is not None:
                return u
        return None
    if isinstance(crit, BinaryExpression):
        if _col_key(crit.left) == "user_id" and crit.operator is operators.eq:
            v = _literal_value(crit.right)
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


def _extract_status_in_from_crit(crit: Any) -> Optional[tuple[str, ...]]:
    if crit is None:
        return None
    if isinstance(crit, BooleanClauseList):
        for c in getattr(crit, "clauses", ()) or ():
            t = _extract_status_in_from_crit(c)
            if t is not None:
                return t
        return None
    if isinstance(crit, BinaryExpression):
        if _col_key(crit.left) == "status" and crit.operator is operators.in_op:
            rv = _literal_value(crit.right)
            try:
                return tuple(rv) if rv is not None else ()
            except TypeError:
                return None
    return None


def _matches_pair(
    left_ent: Any,
    right_ent: Any,
    crit: Any,
    left_alias: str,
    right_alias: str,
) -> bool:
    """Join ON 또는 복합 필터: 두 엔티티를 번갈아 매칭."""
    if crit is None:
        return True
    if isinstance(crit, BooleanClauseList):
        op = getattr(crit, "operator", None)
        clauses = getattr(crit, "clauses", ()) or ()
        if op is operators.and_:
            return all(_matches_pair(left_ent, right_ent, c, left_alias, right_alias) for c in clauses)
        if op is operators.or_:
            return any(_matches_pair(left_ent, right_ent, c, left_alias, right_alias) for c in clauses)
        return all(_matches_pair(left_ent, right_ent, c, left_alias, right_alias) for c in clauses)
    if isinstance(crit, BinaryExpression):
        lk_l = _col_key(crit.left)
        lk_r = _col_key(crit.right)
        rv_l = _literal_value(crit.left)
        rv_r = _literal_value(crit.right)
        op = crit.operator
        # Column == Column (join keys)
        if lk_l and lk_r and op is operators.eq:
            vl = getattr(left_ent, lk_l, None)
            vr = getattr(right_ent, lk_r, None)
            if vl is not None and vr is not None:
                return vl == vr
        # Left.field == value
        if lk_l:
            cls_l = getattr(crit.left, "class_", None)
            if cls_l is not None:
                owner = left_ent if cls_l == type(left_ent) else right_ent
                if owner is not None:
                    ev = getattr(owner, lk_l, None)
                    rv = _literal_value(crit.right)
                    if op is operators.eq:
                        return ev == rv
                    if op is operators.in_op:
                        try:
                            it = list(rv) if rv is not None and not isinstance(rv, (str, bytes)) else [rv]
                        except TypeError:
                            it = [rv]
                        return ev in it
        return _matches(left_ent, crit) or _matches(right_ent, crit)
    return True


class FSQuery:
    def __init__(self, sess: "FSSession", entities: tuple[Any, ...]):
        self._sess = sess
        self._dao = sess.dao
        self._entities = entities
        self._filters: list[Any] = []
        self._joins: list[tuple[Any, Any]] = []
        self._order: list[Any] = []
        self._group_by: list[Any] = []
        self._limit: Optional[int] = None
        self._distinct = False
        self._loads: list[Any] = []

    def filter(self, *criterion: Any) -> "FSQuery":
        self._filters.extend(criterion)
        return self

    def options(self, *opts: Any) -> "FSQuery":
        self._loads.extend(opts)
        return self

    def join(self, target: Any, onclause: Any = None, **kwargs: Any) -> "FSQuery":
        self._joins.append((target, onclause))
        return self

    def distinct(self, *expr: Any) -> "FSQuery":
        self._distinct = True
        return self

    def order_by(self, *cols: Any) -> "FSQuery":
        self._order.extend(cols)
        return self

    def group_by(self, *cols: Any) -> "FSQuery":
        self._group_by.extend(cols)
        return self

    def limit(self, n: int) -> "FSQuery":
        self._limit = int(n)
        return self

    def first(self) -> Any:
        rows = self.all()
        return rows[0] if rows else None

    def _finalize(self, rows: list[Any]) -> list[Any]:
        for r in rows:
            if MODEL_COUNTER_FIELD.get(type(r)):
                self._sess._track(r)
        return rows

    def count(self) -> int:
        return len(self.all())

    def all(self) -> list[Any]:
        return self._execute()

    def _primary_model(self) -> Optional[type]:
        e0 = self._entities[0]
        if isinstance(e0, type) and hasattr(e0, "__tablename__"):
            return e0
        cls = getattr(e0, "class_", None)
        if cls is not None and hasattr(cls, "__tablename__"):
            return cls
        return None

    def _model_iter(self, m: type) -> list[Any]:
        if m is User:
            return self._dao.iter_users()
        if m is Event:
            return self._dao.iter_events()
        if m is EventSlot:
            return self._dao.iter_slots()
        if m is Application:
            return self._dao.iter_applications()
        if m is Notification:
            return self._dao.iter_notifications()
        if m is Schedule:
            return self._dao.iter_schedules()
        if m is ScheduleApplication:
            return self._dao.iter_schedule_applications()
        return []

    def _wants_slots(self) -> bool:
        for o in self._loads:
            if "slots" in str(o):
                return True
        return False

    def _attach_event_slots(self, events: list[Any]) -> None:
        all_slots = self._dao.iter_slots()
        by_eid: dict[int, list[Any]] = {}
        for sl in all_slots:
            by_eid.setdefault(sl.event_id, []).append(sl)
        for e in events:
            e.slots = sorted(by_eid.get(e.id, []), key=lambda s: s.start_time)

    def _attach_application_refs(self, apps: list[Any]) -> None:
        evs = {e.id: e for e in self._dao.iter_events()}
        sls = {s.id: s for s in self._dao.iter_slots()}
        for a in apps:
            a.event = evs.get(a.event_id)
            a.slot = sls.get(a.slot_id)

    def _attach_schedule_application_refs(self, apps: list[Any]) -> None:
        sch = {s.id: s for s in self._dao.iter_schedules()}
        for a in apps:
            a.schedule = sch.get(a.schedule_id)

    def _sort_rows(self, rows: list[Any], model: type) -> list[Any]:
        if not self._order:
            return rows

        def sort_key(r: Any) -> tuple:
            keys = []
            for ob in self._order:
                desc = False
                colname = None
                if isinstance(ob, UnaryExpression):
                    desc = ob.modifier is operators.desc_op
                    el = ob.element
                    colname = _col_key(el)
                    # Schedule.event_datetime via joined entity — 컬럼이 Schedule에 있음
                    if colname is None:
                        colname = _col_key(getattr(el, "clause_element", el))
                else:
                    colname = _col_key(ob)
                if not colname:
                    continue
                val = getattr(r, colname, None)
                keys.append(val)
            return tuple(keys)

        descending = any(
            isinstance(ob, UnaryExpression) and ob.modifier is operators.desc_op
            for ob in self._order
        )
        try:
            rows = sorted(rows, key=sort_key, reverse=descending)
        except Exception:
            pass
        return rows

    def _exec_aggregate(self) -> list[Any]:
        """func.count + group_by 조합 (애플리케이션 코드 패턴 한정)."""
        apps = self._dao.iter_applications()
        for f in self._filters:
            apps = [a for a in apps if _matches(a, f)]

        # (slot_id, count) group_by slot_id — 홈 슬롯 UI
        if (
            len(self._entities) >= 2
            and self._group_by
            and _col_key(self._group_by[0]) == "slot_id"
        ):
            slot_ids: set[int] = set()
            for f in self._filters:
                if isinstance(f, BinaryExpression) and _col_key(f.left) == "slot_id":
                    if f.operator is operators.in_op:
                        slot_ids |= set(_literal_value(f.right) or ())
            by_slot: dict[int, int] = {}
            for a in apps:
                if slot_ids and a.slot_id not in slot_ids:
                    continue
                if getattr(a, "status", None) != "approved":
                    continue
                by_slot[a.slot_id] = by_slot.get(a.slot_id, 0) + 1
            return [(sid, n) for sid, n in by_slot.items()]

        # (event_id, status, count)
        if (
            len(self._group_by) >= 2
            and _col_key(self._group_by[0]) == "event_id"
            and _col_key(self._group_by[1]) == "status"
        ):
            out: dict[int, dict[str, int]] = {}
            eids = set()
            for f in self._filters:
                if isinstance(f, BinaryExpression) and f.operator is operators.in_op:
                    if _col_key(f.left) == "event_id":
                        eids |= set(_literal_value(f.right) or ())
            for a in apps:
                if eids and a.event_id not in eids:
                    continue
                st = getattr(a, "status", "")
                if st not in ("pending", "approved"):
                    continue
                out.setdefault(a.event_id, {"pending": 0, "approved": 0})
                if st in out[a.event_id]:
                    out[a.event_id][st] += 1
            triples = []
            for eid, d in out.items():
                if d["pending"]:
                    triples.append((eid, "pending", d["pending"]))
                if d["approved"]:
                    triples.append((eid, "approved", d["approved"]))
            return triples

        # slot_id count + join EventSlot filter event_id (관리자 슬롯별 인원)
        if self._joins and self._group_by and _col_key(self._group_by[0]) == "slot_id":
            event_id = None
            for f in self._filters:
                if isinstance(f, BinaryExpression) and _col_key(f.left) == "event_id":
                    event_id = _literal_value(f.right)
            slots = [s for s in self._dao.iter_slots() if event_id is None or s.event_id == event_id]
            slot_ids = [s.id for s in slots]
            statuses = ("pending", "approved")
            by_slot: dict[int, int] = {}
            for a in apps:
                if a.slot_id not in slot_ids:
                    continue
                if a.status not in statuses:
                    continue
                by_slot[a.slot_id] = by_slot.get(a.slot_id, 0) + 1
            return [(sid, n) for sid, n in by_slot.items()]

        # schedule_application schedule_id + count
        sas = self._dao.iter_schedule_applications()
        for f in self._filters:
            sas = [x for x in sas if _matches(x, f)]
        if self._group_by and _col_key(self._group_by[0]) == "schedule_id":
            grp: dict[int, int] = {}
            for x in sas:
                grp[x.schedule_id] = grp.get(x.schedule_id, 0) + 1
            return [(k, v) for k, v in grp.items()]

        logger.warning("FS aggregate fallback empty entities=%s group=%s", self._entities, self._group_by)
        return []

    def _exec_join_schedule_member_calendar(self) -> list[Any]:
        """member_schedule_list: Schedule ⋈ ScheduleApplication."""
        schedules = self._dao.iter_schedules()
        apps = self._dao.iter_schedule_applications()
        crit = self._joins[0][1] if self._joins else None
        uid = _extract_user_id_from_crit(crit)
        statuses = _extract_status_in_from_crit(crit)
        for f in self._filters:
            if isinstance(f, BinaryExpression) and _col_key(f.left) == "user_id":
                uid = _literal_value(f.right)
        out: list[Any] = []
        for s in schedules:
            ok = False
            for a in apps:
                if a.schedule_id != s.id:
                    continue
                if uid is not None and a.user_id != uid:
                    continue
                if statuses and getattr(a, "status", None) not in statuses:
                    continue
                if crit and not _matches_pair(s, a, crit, "Schedule", "SA"):
                    continue
                ok = True
                break
            if ok:
                out.append(s)
        if self._distinct:
            seen = set()
            dedup = []
            for s in out:
                if s.id in seen:
                    continue
                seen.add(s.id)
                dedup.append(s)
            out = dedup
        out = self._sort_rows(out, Schedule)
        return self._finalize(out)

    def _exec_join_schedule_application_order_schedule_dt(self) -> list[Any]:
        """my_schedule_applications: SA join Schedule order by event_datetime."""
        sas = self._dao.iter_schedule_applications()
        for f in self._filters:
            sas = [x for x in sas if _matches(x, f)]
        sch_map = {s.id: s for s in self._dao.iter_schedules()}
        rows = []
        for a in sas:
            s = sch_map.get(a.schedule_id)
            if s is None:
                continue
            a.schedule = s
            rows.append(a)
        rows.sort(key=lambda r: r.schedule.event_datetime, reverse=True)
        return self._finalize(rows)

    def _scalar_column_all(self) -> list[Any]:
        """단일 컬럼 .all() — distinct schedule_id 등."""
        ent = self._entities[0]
        model_cls = getattr(ent, "class_", None)
        colname = _col_key(ent)
        if model_cls is None or not colname:
            return []
        rows = self._model_iter(model_cls)
        for f in self._filters:
            rows = [r for r in rows if _matches(r, f)]
        out_vals = [getattr(r, colname) for r in rows]
        if self._distinct:
            seen: set[Any] = set()
            dedup: list[Any] = []
            for v in out_vals:
                if v in seen:
                    continue
                seen.add(v)
                dedup.append(v)
            out_vals = dedup
        tup = [(v,) for v in out_vals]
        if self._limit is not None:
            tup = tup[: self._limit]
        return tup

    def _execute(self) -> list[Any]:
        e0 = self._entities[0]
        if hasattr(e0, "class_") and hasattr(e0, "key") and len(self._entities) == 1:
            return self._scalar_column_all()

        if self._group_by:
            return self._exec_aggregate()

        if len(self._entities) >= 2 and not self._primary_model():
            return self._exec_aggregate()

        model = self._primary_model()
        if model is None:
            return []

        # 조인 전용 패턴
        if model is Schedule and self._joins:
            return self._exec_join_schedule_member_calendar()
        if model is ScheduleApplication and self._joins:
            return self._exec_join_schedule_application_order_schedule_dt()

        rows = self._model_iter(model)
        for f in self._filters:
            rows = [r for r in rows if _matches(r, f)]

        rows = self._sort_rows(rows, model)

        if model is Event and self._wants_slots():
            self._attach_event_slots(rows)

        if model is Application:
            self._attach_application_refs(rows)
            users_map = {u.id: u for u in self._dao.iter_users()}
            for a in rows:
                a.user = users_map.get(a.user_id)

        if model is EventSlot:
            ev_map = {e.id: e for e in self._dao.iter_events()}
            for sl in rows:
                sl.event = ev_map.get(sl.event_id)

        if model is ScheduleApplication and self._loads:
            self._attach_schedule_application_refs(rows)

        if self._distinct:
            seen = set()
            dedup = []
            for r in rows:
                pk = getattr(r, "id", None)
                if pk in seen:
                    continue
                seen.add(pk)
                dedup.append(r)
            rows = dedup

        if self._limit is not None:
            rows = rows[: self._limit]

        return self._finalize(rows)


class FSSession:
    _is_fs_session = True

    def __init__(self) -> None:
        self.dao = FirestoreDAO()
        self._pending: list[Any] = []
        self._identity_map: dict[tuple[type, int], Any] = {}
        self._deleted: dict[str, set[int]] = {v: set() for v in MODEL_COUNTER_FIELD.values()}

    def _track(self, obj: Any) -> None:
        oid = getattr(obj, "id", None)
        if oid is None:
            return
        cls = type(obj)
        if cls not in MODEL_COUNTER_FIELD:
            return
        self._identity_map[(cls, int(oid))] = obj

    def query(self, *entities: Any) -> FSQuery:
        return FSQuery(self, entities)

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    def delete(self, obj: Any) -> None:
        oid = getattr(obj, "id", None)
        if oid is None:
            return
        colname = MODEL_COUNTER_FIELD.get(type(obj))
        if colname:
            self._deleted.setdefault(colname, set()).add(int(oid))

    def flush(self) -> None:
        """PK 할당만 수행. 실제 Firestore 쓰기는 commit()에서 일괄 처리."""
        batch = list(self._pending)
        self._pending.clear()
        for obj in batch:
            if getattr(obj, "id", None):
                continue
            field = MODEL_COUNTER_FIELD.get(type(obj))
            if not field:
                continue
            obj.id = self.dao.allocate_id(field)
            self._track(obj)

    def _save_obj(self, obj: Any) -> None:
        cls = type(obj)
        if cls is User:
            self.dao.save_user(obj)
        elif cls is Event:
            self.dao.save_event(obj)
        elif cls is EventSlot:
            self.dao.save_slot(obj)
        elif cls is Application:
            self.dao.save_application(obj)
        elif cls is Notification:
            self.dao.save_notification(obj)
        elif cls is Schedule:
            self.dao.save_schedule(obj)
        elif cls is ScheduleApplication:
            self.dao.save_schedule_application(obj)

    def commit(self) -> None:
        while self._pending:
            self.flush()

        for obj in list(self._identity_map.values()):
            self._save_obj(obj)

        for colname, ids in self._deleted.items():
            for i in ids:
                if colname == "users":
                    self.dao.delete_user_doc(i)
                elif colname == "events":
                    self.dao.delete_event_doc(i)
                    for sl in self.dao.iter_slots():
                        if sl.event_id == i:
                            self.dao.delete_slot_doc(sl.id)
                    for ap in self.dao.iter_applications():
                        if ap.event_id == i:
                            self.dao.delete_application_doc(ap.id)
                elif colname == "event_slots":
                    self.dao.delete_slot_doc(i)
                elif colname == "applications":
                    self.dao.delete_application_doc(i)
                elif colname == "notifications":
                    self.dao.delete_notification_doc(i)
                elif colname == "schedules":
                    self.dao.delete_schedule_doc(i)
                elif colname == "schedule_applications":
                    self.dao.delete_schedule_application_doc(i)
        for k in self._deleted:
            self._deleted[k].clear()
        self._identity_map.clear()

    def rollback(self) -> None:
        self._pending.clear()
        for k in self._deleted:
            self._deleted[k].clear()
        self._identity_map.clear()

    def refresh(self, obj: Any) -> None:
        oid = getattr(obj, "id", None)
        if oid is None:
            return
        cls = type(obj)
        if cls is Event:
            rows = [e for e in self.dao.iter_events() if e.id == oid]
            if rows:
                fresh = rows[0]
                obj.title = fresh.title
                obj.event_date = fresh.event_date
                obj.location = fresh.location
                obj.description = fresh.description
                self._attach_slots_event(obj)

    def _attach_slots_event(self, e: Any) -> None:
        slots = [s for s in self.dao.iter_slots() if s.event_id == e.id]
        e.slots = sorted(slots, key=lambda s: s.start_time)

    def close(self) -> None:
        pass
