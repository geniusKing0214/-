"""Firebase Admin SDK + 웹 설정. 예전 FIREBASE_JSON / 웹 API 키 방식과 호환."""

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
        raw = os.environ.get("FIREBASE_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            logger.warning("FIREBASE_CREDENTIALS_JSON / FIREBASE_JSON is not valid JSON")
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
    return {
        "apiKey": api_key,
        "authDomain": auth_domain,
        "projectId": project_id,
    }


def firebase_google_login_ready() -> bool:
    if not get_firebase_web_config():
        return False
    return init_firebase_admin()


def verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    from firebase_admin import auth

    if not init_firebase_admin():
        raise RuntimeError("Firebase Admin not initialized")
    return auth.verify_id_token(id_token)
