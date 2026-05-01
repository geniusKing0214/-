"""Firebase Admin SDK + 웹 설정. 여러 환경 변수 이름과 호환."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_admin_initialized = False


def _load_service_account_dict() -> Optional[dict[str, Any]]:
    raw = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
    if not raw:
        raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raw = os.environ.get("FIREBASE_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            logger.warning(
                "Firebase service account env is not valid JSON "
                "(FIREBASE_CREDENTIALS_JSON / FIREBASE_SERVICE_ACCOUNT_JSON / FIREBASE_JSON)"
            )
            return None
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not read GOOGLE_APPLICATION_CREDENTIALS: %s", e)
            return None
    return None


def init_firebase_admin() -> bool:
    global _admin_initialized
    if _admin_initialized:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.warning("firebase-admin is not installed")
        return False

    info = _load_service_account_dict()
    if not info:
        return False

    if firebase_admin._apps:
        _admin_initialized = True
        return True

    cred = credentials.Certificate(info)
    firebase_admin.initialize_app(cred)
    _admin_initialized = True
    logger.info("Firebase Admin SDK initialized.")
    return True


def get_firebase_web_config() -> Optional[dict[str, str]]:
    api_key = (
        os.environ.get("FIREBASE_WEB_API_KEY", "").strip()
        or os.environ.get("FIREBASE_API_KEY", "").strip()
    )
    auth_domain = os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip()
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
    if not api_key or not auth_domain or not project_id:
        return None
    cfg: dict[str, str] = {
        "apiKey": api_key,
        "authDomain": auth_domain,
        "projectId": project_id,
    }
    app_id = os.environ.get("FIREBASE_APP_ID", "").strip()
    if app_id:
        cfg["appId"] = app_id
    sender = os.environ.get("FIREBASE_MESSAGING_SENDER_ID", "").strip()
    if sender:
        cfg["messagingSenderId"] = sender
    bucket = os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip()
    if bucket:
        cfg["storageBucket"] = bucket
    return cfg


def firebase_google_login_ready() -> bool:
    if not get_firebase_web_config():
        return False
    return init_firebase_admin()


def log_firebase_configuration_hints() -> None:
    """터미널·배포 로그에서 무엇이 빠졌는지 바로 보이게 한다."""
    web_missing: list[str] = []
    if not (
        os.environ.get("FIREBASE_WEB_API_KEY", "").strip()
        or os.environ.get("FIREBASE_API_KEY", "").strip()
    ):
        web_missing.append("FIREBASE_WEB_API_KEY")
    if not os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip():
        web_missing.append("FIREBASE_AUTH_DOMAIN")
    if not os.environ.get("FIREBASE_PROJECT_ID", "").strip():
        web_missing.append("FIREBASE_PROJECT_ID")

    sa_raw = any(
        os.environ.get(k, "").strip()
        for k in ("FIREBASE_CREDENTIALS_JSON", "FIREBASE_SERVICE_ACCOUNT_JSON", "FIREBASE_JSON")
    )
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    gac_ok = bool(gac and os.path.isfile(gac))
    sa_missing = not sa_raw and not gac_ok

    if web_missing:
        logger.warning(
            "Firebase 웹 SDK 환경 변수가 비어 있습니다: %s — Firebase Console → 프로젝트 설정 → "
            "일반 → 내 앱(웹) SDK 에서 복사해 .env 또는 Render Environment 에 넣으세요.",
            ", ".join(web_missing),
        )
    if sa_missing:
        logger.warning(
            "Firebase 서비스 계정이 없습니다. Firebase Console → 프로젝트 설정 → 서비스 계정 → "
            "새 비공개 키 생성(JSON). Render 면 FIREBASE_CREDENTIALS_JSON 에 JSON 전체를 한 줄로, "
            "로컬이면 GOOGLE_APPLICATION_CREDENTIALS=파일경로 를 쓰세요.",
        )
    elif sa_raw:
        info = _load_service_account_dict()
        if not info:
            logger.warning(
                "Firebase 서비스 계정 JSON 이 인식되지 않습니다. FIREBASE_*_JSON 값이 "
                "올바른 JSON 한 덩어리인지(따옴표 이스케이프) 확인하세요."
            )


def verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    from firebase_admin import auth

    if not init_firebase_admin():
        raise RuntimeError("Firebase Admin not initialized")
    return auth.verify_id_token(id_token)
