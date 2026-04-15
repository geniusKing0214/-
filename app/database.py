import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

# 상대 경로(./scheduler.db)는 실행 cwd에 따라 파일이 달라져 DB가 비는 것처럼 보일 수 있음.
# app/ 상위(프로젝트 루트)에 항상 같은 파일을 쓴다.
_app_dir = Path(__file__).resolve().parent
_project_root = _app_dir.parent
_default_sqlite = _project_root / "scheduler.db"
_default_url = f"sqlite:///{_default_sqlite.as_posix()}"

DATABASE_URL = os.environ.get("DATABASE_URL", _default_url)


def _log_sqlite_persistence_hint(url: str) -> None:
    """재배포 후 데이터가 비는 경우, 대개 DB 파일이 컨테이너 임시 레이어(/app 등)에만 있을 때이다."""
    if not url.startswith("sqlite:"):
        return
    in_container = Path("/.dockerenv").exists() or bool(os.environ.get("FLY_APP_NAME"))
    if not in_container:
        return
    uses_data_volume = url.startswith("sqlite:////data")
    if uses_data_volume:
        logger.info("SQLite DATABASE_URL이 /data 볼륨 경로를 사용합니다.")
    else:
        logger.warning(
            "SQLite가 /data 볼륨이 아닌 URL을 사용 중입니다. "
            "배포 시마다 DB가 새로 생기면 스케줄·회원 데이터가 사라질 수 있습니다. "
            "DATABASE_URL=sqlite:////data/scheduler.db 와 Docker/Fly의 /data 마운트를 확인하세요. "
            "현재=%r",
            url,
        )

_log_sqlite_persistence_hint(DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def ensure_sqlite_migrations(engine) -> None:
    """기존 SQLite DB에 컬럼이 없을 때만 ALTER (Alembic 없이 경량 마이그레이션)."""
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        names = {row[1] for row in cols}
        if "nickname" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN nickname VARCHAR(50)"))
        if "google_sub" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
        if "firebase_uid" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN firebase_uid VARCHAR(128)"))


def ensure_schedule_application_status_migration(engine) -> None:
    """레거시 applied → approved. 별도 모집은 pending → 관리자 승인 후 approved."""
    try:
        insp = inspect(engine)
        if not insp.has_table("schedule_applications"):
            return
    except Exception:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE schedule_applications SET status = 'approved' "
                "WHERE status = 'applied'"
            )
        )


def ensure_events_location_column(engine) -> None:
    """기존 DB에 events.location 없으면 추가 (SQLite / PostgreSQL 등)."""
    try:
        insp = inspect(engine)
        if not insp.has_table("events"):
            return
        col_names = {c["name"] for c in insp.get_columns("events")}
    except Exception:
        return
    if "location" in col_names:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE events ADD COLUMN location VARCHAR(300)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()