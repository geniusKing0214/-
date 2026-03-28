# Scheduler App (1차 테스트)

FastAPI + SQLite + Jinja2 기반 스케줄 신청/승인 시스템입니다.

## 포함 기능
- 회원가입 / 로그인 / 로그아웃
- 관리자 / 일반회원 권한 분리
- 달력형 메인 화면
- 스케줄 생성 / 수정 / 삭제
- 정원 제한 (최대 100명)
- 신청 마감 기한 설정
- 회원 신청 / 취소
- 관리자 승인 / 거절
- 관리자 알림 / 유저 알림
- 내 신청 내역 페이지

## 실행 방법
```bash
python -m venv .venv
```

### Windows PowerShell
```powershell
.\.venv\Scripts\Activate.ps1
```

실행 정책 오류가 나면 PowerShell을 관리자 권한으로 열고 한 번만:
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 패키지 설치
```bash
pip install -r requirements.txt
```

### 서버 실행
```bash
uvicorn app.main:app --reload
```

브라우저에서 아래 주소 접속:
```text
http://127.0.0.1:8000
```

## 테스트 관리자 계정
- 아이디: `admin`
- 비밀번호: `admin1234`

## 주의
- 현재는 1차 테스트 버전이라 SQLite 단일 파일 DB를 사용합니다.
- 실제 운영 전에는 세션 시크릿키, 비밀번호 정책, CSRF, 예외처리, 배포용 DB 등을 강화해야 합니다.
- DB 파일은 `app/scheduler.db` 에 생성됩니다.
