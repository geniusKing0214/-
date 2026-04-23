import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

# 상대 경로(./scheduler.db)는 실행 cwd에 따라 파일이 달라져 DB가 비는 것처럼 보일 수 있음.
# 로컬·Docker 모두 프로젝트 루트의 data/scheduler.db 한 곳을 쓴다(Docker Compose의 ./data 마운트와 동일).
_app_dir = Path(__file__).resolve().parent
_project_root = _app_dir.parent
_default_sqlite = _project_root / "data" / "scheduler.db"
_default_url = f"sqlite:///{_default_sqlite.as_posix()}"


def _migrate_legacy_root_sqlite_if_using_default() -> None:
    """예전 기본값(<루트>/scheduler.db)만 있으면 data/scheduler.db 로 복사해 경로를 통일."""
    if os.environ.get("DATABASE_URL", "").strip():
        return
    if _default_sqlite.exists():
        return
    legacy = _project_root / "scheduler.db"
    if not legacy.is_file():
        return
    try:
        _default_sqlite.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, _default_sqlite)
        logger.info(
            "기존 루트의 scheduler.db를 %s 로 복사했습니다. 이후 로그인·가입 데이터는 이 파일에만 저장됩니다.",
            _default_sqlite,
        )
    except OSError as e:
        logger.warning("scheduler.db → data/scheduler.db 복사 실패: %s", e)


def _warn_if_duplicate_sqlite_files() -> None:
    """루트 DB가 data보다 더 최근이면(예: 예전 기본 경로로만 실행) 혼동 방지 로그."""
    if os.environ.get("DATABASE_URL", "").strip():
        return
    legacy = _project_root / "scheduler.db"
    if not legacy.is_file() or not _default_sqlite.is_file():
        return
    try:
        if legacy.stat().st_mtime <= _default_sqlite.stat().st_mtime + 1.0:
            return
    except OSError:
        return
    logger.warning(
        "프로젝트 루트 scheduler.db가 data/scheduler.db보다 최근입니다. "
        "앱은 data/scheduler.db만 사용합니다. 루트에 최신 가입·로그인 데이터가 있다면 "
        "서버를 끈 뒤 루트 파일을 data/scheduler.db로 덮어쓰세요."
    )


def _normalize_database_url(raw: str) -> str:
    """Render 등이 주는 postgres:// 를 SQLAlchemy가 기대하는 postgresql:// 로 맞춘다."""
    s = raw.strip()
    if s.startswith("postgres://"):
        return "postgresql://" + s[len("postgres://") :]
    return s


_raw_database_url = os.environ.get("DATABASE_URL", _default_url)
DATABASE_URL = _normalize_database_url(_raw_database_url)

_migrate_legacy_root_sqlite_if_using_default()
_warn_if_duplicate_sqlite_files()


def _is_render() -> bool:
    return os.environ.get("RENDER", "").lower() in ("true", "1", "yes")


def _log_sqlite_persistence_hint(url: str) -> None:
    """재배포 후 데이터가 비는 경우, 대개 DB 파일이 컨테이너 임시 레이어에만 있을 때이다."""
    if not url.startswith("sqlite:"):
        return
    norm = url.replace("\\", "/")
    in_container = (
        Path("/.dockerenv").exists()
        or bool(os.environ.get("FLY_APP_NAME"))
        or _is_render()
    )
    uses_data_volume = norm.startswith("sqlite:////data") or norm.startswith(
        "sqlite:////app/data"
    )
    if not in_container:
        if "/data/scheduler.db" in norm:
            logger.info(
                "SQLite 로컬 파일: 프로젝트 data/scheduler.db (Docker Compose ./data 와 동일 경로)."
            )
        return
    if uses_data_volume:
        logger.info("SQLite DATABASE_URL이 영구 볼륨 경로(/data 또는 /app/data)를 사용합니다.")
    else:
        logger.warning(
            "SQLite가 영구 볼륨 경로가 아닙니다. 배포마다 DB가 새로 생기면 "
            "회원·스케줄 데이터가 사라집니다. "
            "Render: PostgreSQL 사용(권장) 또는 Persistent Disk를 /data(또는 /app/data)에 마운트하고 "
            "DATABASE_URL=sqlite:////data/scheduler.db 와 맞추세요. Fly: fly.toml [mounts] destination. "
            "현재=%r",
            url,
        )
    if _is_render() and not uses_data_volume:
        logger.warning(
            "Render 환경: 관리형 PostgreSQL을 쓰면(환경 변수 DATABASE_URL) 별도 디스크 없이 데이터가 유지됩니다. "
            "render.yaml 예시를 저장소 루트에 참고하세요."
        )


_log_sqlite_persistence_hint(DATABASE_URL)


def _ensure_sqlite_parent_dir(url: str) -> None:
    if not url.startswith("sqlite:"):
        return
    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        if not u.database:
            return
        p = Path(u.database)
        if not p.is_absolute():
            p = _project_root / p
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("SQLite DB 상위 디렉터리 생성 실패: %s", e)


_ensure_sqlite_parent_dir(DATABASE_URL)


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite:"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))

if DATABASE_URL.startswith("sqlite:"):
    try:
        from sqlalchemy.engine import make_url

        _u = make_url(DATABASE_URL)
        if _u.database:
            logger.info("SQLite 데이터베이스 파일: %s", _u.database)
    except Exception:
        pass
else:
    logger.info("DB 백엔드: PostgreSQL (배포 후에도 데이터 유지)")

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
        # 예전 스키마: email / name — 현재 모델은 username 기준
        if "username" not in names and "email" in names:
            conn.execute(text("ALTER TABLE users RENAME COLUMN email TO username"))
            cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            names = {row[1] for row in cols}
        if "approval_status" not in names:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN approval_status VARCHAR(30) "
                    "DEFAULT 'approved'"
                )
            )
        if "is_admin" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
            cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            names = {row[1] for row in cols}
        if "nickname" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN nickname VARCHAR(50)"))
        if "google_sub" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
        if "firebase_uid" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN firebase_uid VARCHAR(128)"))
        # 레거시 name → 빈 닉네임 채우기
        names_after = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "name" in names_after and "nickname" in names_after:
            conn.execute(
                text(
                    "UPDATE users SET nickname = name WHERE "
                    "(nickname IS NULL OR TRIM(nickname) = '') "
                    "AND name IS NOT NULL AND TRIM(name) != ''"
                )
            )


def ensure_users_is_admin_coercion(engine) -> None:
    """is_admin 이 NULL 이면 비관리자로 맞춤. raw SQL 대신 ORM으로 타입(SQLite·PostgreSQL·레거시 정수 등) 호환."""
    try:
        insp = inspect(engine)
        if not insp.has_table("users"):
            return
        col_names = {c["name"] for c in insp.get_columns("users")}
        if "is_admin" not in col_names:
            return
    except Exception as e:
        logger.warning("ensure_users_is_admin_coercion (inspect): %s", e)
        return

    try:
        from app.models import User

        db = SessionLocal()
        try:
            n = (
                db.query(User)
                .filter(User.is_admin.is_(None))
                .update({User.is_admin: False}, synchronize_session=False)
            )
            if n:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("ensure_users_is_admin_coercion: %s", e)


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