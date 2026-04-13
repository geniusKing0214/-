import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 상대 경로(./scheduler.db)는 실행 cwd에 따라 파일이 달라져 DB가 비는 것처럼 보일 수 있음.
# app/ 상위(프로젝트 루트)에 항상 같은 파일을 쓴다.
_app_dir = Path(__file__).resolve().parent
_project_root = _app_dir.parent
_default_sqlite = _project_root / "scheduler.db"
_default_url = f"sqlite:///{_default_sqlite.as_posix()}"

DATABASE_URL = os.environ.get("DATABASE_URL", _default_url)

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()