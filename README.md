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
