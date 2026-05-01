# HAN Scheduler

FastAPI 기반 스케줄 신청 웹앱입니다.

## GitHub에 올리기

```bash
cd scheduler_app
git init   # 이미 되어 있으면 생략
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<본인아이디>/<저장소이름>.git
git push -u origin main
```

GitHub 웹에서 **빈 저장소**를 먼저 만든 뒤 위 주소를 넣으면 됩니다.

## GitHub + Render.com (서버 배포)

코드를 GitHub에 올린 뒤 Render가 빌드·호스팅합니다. 저장소 루트에 `render.yaml` 이 있으면 **Blueprint** 로 한 번에 생성할 수 있습니다.

1. [Render](https://render.com) 가입 후 **Dashboard → New → Blueprint**.
2. **GitHub 계정 연결** 후 이 프로젝트 저장소를 선택합니다.
3. `render.yaml` 이 인식되면 서비스 이름·리전 등을 확인하고 **Apply** 합니다.
4. 배포가 끝나면 표시되는 **`https://han-scheduler.onrender.com`** 형태의 URL로 접속합니다. (이름은 `render.yaml`의 `name` 또는 대시보드에서 변경 가능)
5. **`SESSION_SECRET_KEY`** 는 Blueprint 적용 시 `generateValue: true` 로 자동 생성됩니다. 필요하면 대시보드 **Environment** 에서 다시 설정할 수 있습니다.

### 비용·DB 유지 안내

- 이 Blueprint는 **SQLite 파일을 유지**하려고 **Persistent Disk (`/data`)** 를 붙입니다. Render 정책상 **Free 인스턴스에는 디스크를 붙일 수 없어**, `plan: starter` 로 되어 있습니다. (요금은 [Render 요금](https://render.com/pricing) 기준)
- **Free 웹 서비스만** 쓰고 싶다면 `render.yaml` 에서 `disk:` 블록 전체와 필요 시 `plan: free` 로 바꾸세요. 이 경우 **재배포할 때마다 DB가 비어 있을 수 있습니다.**

## GitHub Actions → Fly.io 자동 배포

1. [Fly.io](https://fly.io) 가입 후 PC에서 `flyctl` 설치 및 `fly auth login`
2. **앱 이름**은 전 세계에서 유일해야 합니다. `fly.toml`의 `app = "han-scheduler"`를 본인 값으로 바꾼 뒤:

   ```bash
   fly apps create <fly.toml에-적은-이름>
   fly volumes create scheduler_data --region nrt --size 1
   fly secrets set SESSION_SECRET_KEY=$(openssl rand -hex 32)
   ```

3. 배포용 토큰 발급: `fly tokens create deploy -x 999999h` (또는 대시보드에서 Deploy Token)
4. GitHub 저장소 **Settings → Secrets and variables → Actions** 에 `FLY_API_TOKEN` 추가
5. `main` 브랜치에 푸시하면 `.github/workflows/deploy-fly.yml`이 `fly deploy`를 실행합니다.

## 로컬 Docker

```bash
docker compose up --build -d
```

브라우저: http://localhost:8000

## 로컬 터미널에서 실행 (`uvicorn`)

저장소 **루트**에서:

```bash
pip install -r requirements.txt
```

권장 — **모든 네트워크 인터페이스**에 바인딩해, 같은 Wi‑Fi의 휴대폰에서 `http://<PC의-IP>:8000` 으로도 접속할 수 있습니다.

- **Windows (PowerShell):** `.\run_local.ps1`
- **macOS / Linux / Git Bash:** `chmod +x run_local.sh && ./run_local.sh`

수동 실행 예:

```bash
py -3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 127.0.0.1` 만 쓰면 **그 PC 안의 브라우저**에서만 열리고, 터미널에 “접속이 안 된다”고 느끼는 경우(휴대폰·다른 기기)는 `0.0.0.0` 이 필요합니다.

Google 로그인을 쓰려면 **프로젝트 루트에 `.env`** 파일이 필요합니다. `.env.example`을 복사한 뒤 값을 채우세요 (커밋하지 마세요).

- **`FIREBASE_WEB_API_KEY`**, **`FIREBASE_AUTH_DOMAIN`**, **`FIREBASE_PROJECT_ID`** — Firebase Console → 프로젝트 설정 → 일반 → 웹 앱 SDK 설정  
- **서비스 계정 JSON** — `FIREBASE_CREDENTIALS_JSON`(전체 JSON 문자열) 또는 `GOOGLE_APPLICATION_CREDENTIALS`(파일 경로)

앱은 기동 시 **프로젝트 루트의 `.env`** 를 자동으로 읽습니다 (`python-dotenv`). `.env`가 없거나 Firebase 변수가 비어 있으면 로그인 화면에 설정 안내가 뜹니다.

세션 쿠키용으로 **`SESSION_SECRET_KEY`** 또는 **`SECRET_KEY`** 도 로컬에서 설정하는 것을 권장합니다.

**서버에서는 되는데 터미널(로컬)만 안 되는 경우:** Render `.env`에 **`SESSION_COOKIE_SECURE=true`** 가 있으면 로컬 **`http://`** 에서 세션 쿠키가 막힙니다. 앱은 **`RENDER` 등 배포 환경 변수가 없을 때** 이 값을 **자동으로 무시**합니다. 그래도 안 되면 `.env`에 **`LOCAL_DEV=1`** 또는 **`SESSION_COOKIE_SECURE=false`** 를 넣으세요. 로컬에서만 HTTPS+Secure 쿠키를 꼭 써야 하면 **`FORCE_SECURE_SESSION=1`** 과 함께 `SESSION_COOKIE_SECURE=true` 를 설정하세요.

**Google 팝업 후 오류:** Firebase Console → Authentication → 설정 → **승인된 도메인**에 `localhost` 와 `127.0.0.1` 추가. (Google Cloud에서 API 키를 **HTTP 리퍼러로만** 제한했다면 `localhost` 출처를 허용하는지도 확인하세요.)

## DB(회원·스케줄)가 모이는 위치

| 실행 방식 | 데이터가 쌓이는 곳 | 배포·재시작 후 유지 |
|-----------|---------------------|---------------------|
| **로컬** (`uvicorn`만, `DATABASE_URL` 없음) | 프로젝트 루트 **`data/scheduler.db`** | PC에 파일이 남으면 유지. 예전 루트 `scheduler.db`만 있으면 첫 기동 시 `data/`로 복사 시도. |
| **로컬·단일 파일 고정** | **`HAN_SQLITE_FILE`**(또는 `SCHEDULER_SQLITE_FILE`)에 **절대 경로** | `DATABASE_URL`을 안 주고 이것만 주면 그 파일 하나를 DB로 씁니다. **프로젝트 밖**(예: 내 문서 폴더)에 두면 소스 업데이트와 분리됩니다. `DATABASE_URL`을 같이 쓰면 **항상 `DATABASE_URL`이 우선**입니다. |
| **Docker Compose** | 호스트 **`./data/scheduler.db`** ↔ 컨테이너 `/data/scheduler.db` | `./data`를 마운트하므로 **이미지를 다시 빌드해도 DB는 유지**됩니다. |
| **Fly.io + SQLite** | 볼륨에 붙은 **`/data/scheduler.db`** | **`fly.toml`의 `[mounts]` + `fly volumes create`** 가 없으면 매 배포마다 새 컨테이너 디스크라 **데이터가 비는 것과 같습니다.** |
| **Render + PostgreSQL** | 관리형 Postgres (파일 경로 아님) | Blueprint의 `DATABASE_URL`이 Postgres를 가리키면 **재배포해도 데이터 유지**가 기본입니다. |
| **Render + SQLite만** | 디스크에 마운트한 경로만 안전 | 무료 웹만 쓰면 디스크를 못 붙이는 경우가 많아 **DB가 비는 것**이 나올 수 있습니다. Postgres 권장. |

앱 기동 시 로그에 **`SQLite 실제 파일`** 과 **`SQLite 백업 폴더`** (`…/backups`) 경로가 찍힙니다.  
SQLite 사용 시 일정 간격으로 `backups/scheduler-날짜.db` 자동 백업(기본 켜짐), **프로세스 종료 시** `scheduler-shutdown-*.db` 백업(기본 켜짐), 연결 시 **WAL 모드**로 손상 위험을 줄입니다. (`.env.example`의 `SCHEDULER_SQLITE_*` 참고)

**Docker / Fly / Render** 에서 SQLite `DATABASE_URL`이 **`sqlite:////data/...` 또는 `sqlite:////app/data/...`** 가 아니면 **앱이 기동하지 않습니다**(`SystemExit`). 배포 한 번에 DB가 통째로 사라지는 설정을 막기 위함입니다. 정말 임시 디스크만 써야 하면 `ALLOW_EPHEMERAL_SQLITE=1` (비권장).

## 스케줄·회원 데이터가 배포 후 사라질 때 (SQLite)

원인은 거의 항상 **SQLite 파일이 “영구 볼륨”이 아니라 컨테이너 안 임시 디스크(예: `/app`)에만 생긴 경우**입니다. 이미지가 바뀌면 그 레이어는 초기화됩니다.

- **Docker Compose**: `DATABASE_URL=sqlite:////data/scheduler.db` 이고, 호스트의 **`./data` 폴더가 컨테이너 `/data`에 마운트**됩니다. DB 파일은 **`프로젝트/data/scheduler.db`** 에 보이므로 백업·복사가 쉽습니다.  
  - 예전에 쓰던 **이름 있는 Docker 볼륨**을 쓰던 경우와 달리, `docker compose down`만으로는 `./data` 안의 파일이 지워지지 않습니다. (`docker compose down -v`는 **이름 있는 볼륨**이 있을 때만 해당)
- **Dockerfile 기본값**은 `/data/scheduler.db` 로 맞춰 두었습니다 (이전의 `/app` 기본값은 제거됨).
- **Fly.io**: `fly.toml`의 `[mounts]` + `fly volumes create scheduler_data` 가 필수입니다.  
  - SQLite는 **한 앱에 Machine 1대**만 권장: `fly scale count 1`
- 배포 로그에 `SQLite가 /data 볼륨이 아닌 URL을 사용 중` 경고가 나오면, 플랫폼의 환경 변수·볼륨 마운트를 다시 확인하세요.

### 예전에 쓰던 `scheduler.db` 옮기기 (로컬 → Docker)

프로젝트 루트에 예전 `scheduler.db`가 있으면 Compose로 올리기 **전에** `data` 폴더에 넣어두면 됩니다.

```bash
mkdir -p data
copy /Y .\scheduler.db .\data\scheduler.db
docker compose up -d
```

(Windows PowerShell이면 `Copy-Item .\scheduler.db .\data\scheduler.db`.)  
이미 컨테이너가 돌고 있으면 `docker cp ./scheduler.db <컨테이너명>:/data/scheduler.db` 후 `docker compose restart web` 도 가능합니다 (`docker compose ps`로 컨테이너 이름 확인).
