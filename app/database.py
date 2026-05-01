import atexit
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

# 상대 경로(./scheduler.db)는 실행 cwd에 따라 파일이 달라져 DB가 비는 것처럼 보일 수 있음.
# 로컬·Docker 모두 프로젝트 루트의 data/scheduler.db 한 곳을 쓴다(Docker Compose의 ./data 마운트와 동일).
_app_dir = Path(__file__).resolve().parent
_project_root = _app_dir.parent
_default_sqlite = _project_root / "data" / "scheduler.db"
_default_url = f"sqlite:///{_default_sqlite.as_posix()}"


def _migrate_legacy_root_sqlite_if_using_default() -> None:
    """예전 기본값(<루트>/scheduler.db)을 data/scheduler.db 로 맞춤. data가 없거나 0바이트면 루트에서 복사."""
    if os.environ.get("DATABASE_URL", "").strip():
        return
    legacy = _project_root / "scheduler.db"
    if not legacy.is_file():
        return
    try:
        need_copy = False
        if not _default_sqlite.exists():
            need_copy = True
        else:
            try:
                if _default_sqlite.stat().st_size == 0:
                    need_copy = True
            except OSError:
                need_copy = True
        if not need_copy:
            return
        _default_sqlite.parent.mkdir(parents=True, exist_ok=True)
        if _default_sqlite.exists():
            try:
                _default_sqlite.unlink()
            except OSError as e:
                logger.warning("빈/손상 data DB 제거 실패(덮어쓰기 시도): %s", e)
        shutil.copy2(legacy, _default_sqlite)
        logger.info(
            "기존 루트의 scheduler.db를 %s 로 복사했습니다. 이후 로그인·가입 데이터는 이 파일에만 저장됩니다.",
            _default_sqlite,
        )
    except OSError as e:
        logger.warning("scheduler.db → data/scheduler.db 복사 실패: %s", e)


def _sqlite_active_file_path(url: str) -> Optional[Path]:
    """SQLite DATABASE_URL 에 해당하는 로컬 파일 경로(절대)."""
    if not url.startswith("sqlite:"):
        return None
    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        if not u.database:
            return None
        p = Path(u.database)
        if not p.is_absolute():
            p = _project_root / p
        return p
    except Exception:
        return None


def _prune_old_sqlite_backups(backup_dir: Path, keep: int) -> None:
    keep = max(3, min(int(keep), 200))
    files = sorted(
        [p for p in backup_dir.glob("scheduler*.db") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


def _sqlite_do_backup_to_file(db_path: Path, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src_conn = sqlite3.connect(db_path.as_posix(), timeout=60.0)
        try:
            dst_conn = sqlite3.connect(dest.as_posix(), timeout=60.0)
            try:
                with dst_conn:
                    src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        return True
    except Exception as e:
        logger.warning("SQLite 백업 복사 실패: %s", e)
        return False


def _sqlite_autobackup_if_configured(
    db_path: Path, *, force: bool = False, shutdown: bool = False
) -> None:
    """주기적으로 backups/ 에 복사. force=True 이면 간격 무시(종료 시 등)."""
    if shutdown:
        if os.environ.get("SCHEDULER_SQLITE_BACKUP_AT_EXIT", "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return
    else:
        raw = os.environ.get("SCHEDULER_SQLITE_BACKUP", "1").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return
    if not db_path.is_file():
        return
    try:
        if db_path.stat().st_size < 64:
            return
    except OSError:
        return

    try:
        keep = int(os.environ.get("SCHEDULER_SQLITE_BACKUP_KEEP", "40"))
    except ValueError:
        keep = 40

    cloudish = bool(os.environ.get("FLY_APP_NAME")) or os.environ.get(
        "RENDER", ""
    ).lower() in ("true", "1", "yes")
    default_interval = "3600" if cloudish else "21600"
    try:
        interval = int(
            os.environ.get(
                "SCHEDULER_SQLITE_BACKUP_INTERVAL_SECONDS", default_interval
            )
        )
    except ValueError:
        interval = int(default_interval)
    interval = max(60, interval)

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    marker = backup_dir / ".last_autobackup_ts"
    now = time.time()
    if not force and not shutdown:
        if marker.is_file():
            try:
                if now - marker.stat().st_mtime < interval:
                    return
            except OSError:
                pass

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if shutdown:
        dest = backup_dir / f"scheduler-shutdown-{stamp}.db"
    else:
        dest = backup_dir / f"scheduler-{stamp}.db"
    if not _sqlite_do_backup_to_file(db_path, dest):
        return
    if not shutdown:
        try:
            marker.touch()
        except OSError:
            pass
    logger.info("SQLite 백업 저장: %s", dest)
    _prune_old_sqlite_backups(backup_dir, keep)


def _sqlite_shutdown_backup() -> None:
    try:
        p = _sqlite_active_file_path(DATABASE_URL)
        if p is None:
            return
        _sqlite_autobackup_if_configured(p, force=True, shutdown=True)
    except Exception as e:
        logger.warning("종료 시 SQLite 백업 스킵: %s", e)


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


# 빈 문자열 DATABASE_URL= 은 Render 등에서 이미지 기본값을 덮어써 기동 실패·스테일 URL을 만든다.
_env_db_raw = os.environ.get("DATABASE_URL")
_env_db = (_env_db_raw or "").strip()
_render_boot = str(os.environ.get("RENDER", "")).strip().lower() in ("true", "1", "yes")
if _env_db:
    _raw_database_url = _env_db
elif _render_boot and Path("/.dockerenv").exists():
    _raw_database_url = "sqlite:////data/scheduler.db"
else:
    _raw_database_url = _default_url
DATABASE_URL = _normalize_database_url(_raw_database_url)

_migrate_legacy_root_sqlite_if_using_default()
_warn_if_duplicate_sqlite_files()


def _is_render() -> bool:
    return os.environ.get("RENDER", "").lower() in ("true", "1", "yes")


def _is_render_native_project_sqlite(url: str) -> bool:
    """Render 'Python 3' 네이티브 빌드는 Docker 없이 /opt/render/project/.../data/scheduler.db 를 쓴다."""
    norm = url.replace("\\", "/")
    if not norm.startswith("sqlite:"):
        return False
    return "/opt/render/project/" in norm and "data/scheduler.db" in norm


def _ensure_render_uses_durable_database_or_exit() -> None:
    """Render에서 비영구 SQLite로 뜨는 경우 기동을 막아 DB 유실을 줄인다."""
    if not _is_render():
        return
    if DATABASE_URL.startswith("postgresql"):
        logger.info(
            "Render + PostgreSQL: 재배포 후에도 회원·일정 데이터는 이 DATABASE_URL 의 DB에 유지됩니다."
        )
        return
    norm = DATABASE_URL.replace("\\", "/")
    if norm.startswith("sqlite:////data") or norm.startswith("sqlite:////app/data"):
        logger.warning(
            "Render + SQLite(영구 볼륨 경로): /data 또는 /app/data 에 Persistent Disk 가 "
            "마운트되어 있어야 재배포 후에도 데이터가 남습니다. 디스크 없으면 Postgres(render.yaml)을 권장합니다."
        )
        return
    if _is_render_native_project_sqlite(DATABASE_URL):
        logger.warning(
            "Render Native(Python) + 프로젝트 폴더 SQLite — 재배포 시 DB가 비워질 수 있습니다. "
            "영구 저장은 Environment 에 PostgreSQL DATABASE_URL 을 연결하세요."
        )
        return
    if os.environ.get("RENDER_ALLOW_SQLITE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        logger.warning(
            "RENDER_ALLOW_SQLITE=1 — Render에서 SQLite를 허용했습니다. "
            "컨테이너 로컬 디스크만 쓰면 재배포 시 데이터가 사라질 수 있습니다."
        )
        return
    logger.critical(
        "Render 배포: 데이터 유실을 막으려면 PostgreSQL DATABASE_URL 을 연결하세요.\n"
        "  • Dashboard → PostgreSQL → 웹 서비스 Environment 의 DATABASE_URL 에 내부 URL 연결\n"
        "  • 또는 render.yaml Blueprint 로 Postgres 포함 배포\n"
        "그 외 SQLite는 컨테이너 재생성 시 비워질 수 있습니다. "
        "임시로만 허용하려면 Environment 에 RENDER_ALLOW_SQLITE=1\n"
        "현재 DATABASE_URL=%r",
        DATABASE_URL,
    )
    raise SystemExit(4)


def _enforce_persistent_storage_or_exit() -> None:
    """Docker/Fly/Render에서 비영구 SQLite로 뜨면 기동 자체를 막아 유실을 예방."""
    if os.environ.get("ALLOW_EPHEMERAL_SQLITE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        logger.warning(
            "ALLOW_EPHEMERAL_SQLITE=1 — 컨테이너 임시 디스크 SQLite는 배포 시 데이터가 사라질 수 있습니다."
        )
        return
    u = DATABASE_URL
    if u.startswith("postgresql"):
        return
    if not u.startswith("sqlite:"):
        return
    norm = u.replace("\\", "/")
    if norm.startswith("sqlite:////data") or norm.startswith("sqlite:////app/data"):
        return
    if _is_render() and _is_render_native_project_sqlite(u):
        logger.warning(
            "Render Native 빌드: /opt/render/project/.../data/scheduler.db 사용 중입니다. "
            "Docker(/data 볼륨) 또는 PostgreSQL 이 아니면 기동은 허용하지만 데이터 유지는 PostgreSQL 을 권장합니다."
        )
        return
    in_cloud = bool(os.environ.get("FLY_APP_NAME")) or _is_render()
    in_docker = Path("/.dockerenv").exists()
    if in_cloud or in_docker:
        logger.critical(
            "SQLite가 영구 볼륨(/data)에 연결되지 않았습니다. "
            "이 설정으로는 재배포·이미지 갱신 시 스케줄·회원 DB가 유실됩니다.\n"
            "  • Fly.io: fly.toml [mounts] + DATABASE_URL=sqlite:////data/scheduler.db\n"
            "  • Docker Compose: volumes ./data:/data + DATABASE_URL=sqlite:////data/scheduler.db\n"
            "  • Render: PostgreSQL DATABASE_URL (render.yaml 권장)\n"
            "개발 전용 우회: ALLOW_EPHEMERAL_SQLITE=1\n"
            "현재 DATABASE_URL=%r",
            u,
        )
        raise SystemExit(2)


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
_enforce_persistent_storage_or_exit()
_ensure_render_uses_durable_database_or_exit()


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite:"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))

if DATABASE_URL.startswith("sqlite:"):

    @event.listens_for(engine, "connect")
    def _sqlite_set_wal(dbapi_connection, _connection_record) -> None:
        try:
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
        except Exception:
            pass


_sqlite_path = _sqlite_active_file_path(DATABASE_URL)
if _sqlite_path is not None:
    _sqlite_autobackup_if_configured(_sqlite_path)

if DATABASE_URL.startswith("sqlite:"):
    try:
        if _sqlite_path is not None:
            logger.info("SQLite 실제 파일: %s", _sqlite_path.resolve())
            logger.info(
                "SQLite 자동 백업 폴더(SCHEDULER_SQLITE_BACKUP=1): %s",
                (_sqlite_path.parent / "backups").resolve(),
            )
        else:
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

atexit.register(_sqlite_shutdown_backup)

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