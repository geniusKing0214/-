from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User


def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다."
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자 정보를 찾을 수 없습니다."
        )

    approval_status = getattr(user, "approval_status", "approved")
    if approval_status != "approved":
        if approval_status == "pending_approval":
            detail_message = "관리자 승인 대기 중입니다."
        elif approval_status == "rejected":
            detail_message = "관리자에 의해 계정이 거절되었습니다."
        else:
            detail_message = "계정 상태를 확인할 수 없습니다."

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail_message
        )

    return user