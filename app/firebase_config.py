import os
import json
import firebase_admin

from pathlib import Path
from firebase_admin import credentials, auth, firestore
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def initialize_firebase():
    if firebase_admin._apps:
        return firebase_admin.get_app()

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_path = BASE_DIR / "serviceAccountKey.json"

    # 1. Render 배포용: 환경변수에 JSON 문자열로 넣은 경우
    if service_account_json:
        try:
            cred_dict = json.loads(service_account_json)
            cred = credentials.Certificate(cred_dict)
            return firebase_admin.initialize_app(cred)
        except Exception as e:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_JSON 파싱 실패: {e}")

    # 2. 로컬 개발용: serviceAccountKey.json 파일이 있는 경우
    if service_account_path.exists():
        try:
            cred = credentials.Certificate(str(service_account_path))
            return firebase_admin.initialize_app(cred)
        except Exception as e:
            raise RuntimeError(f"serviceAccountKey.json 로드 실패: {e}")

    # 3. 둘 다 없으면 에러
    raise RuntimeError(
        "Firebase 초기화 실패: FIREBASE_SERVICE_ACCOUNT_JSON 환경변수 또는 "
        "serviceAccountKey.json 파일이 필요합니다."
    )


initialize_firebase()
db = firestore.client()


def verify_firebase_token(id_token: str):
    return auth.verify_id_token(id_token)