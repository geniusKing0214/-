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

- **Docker Compose**: `docker-compose.yml`이 `DATABASE_URL=sqlite:////data/scheduler.db` 와 **볼륨 `scheduler_data:/data`** 를 씁니다.  
  - `docker compose down -v` 를 쓰면 **볼륨까지 지워져 DB가 통째로 삭제**됩니다. (`-v` 없이 내리기)
- **Dockerfile 기본값**은 `/data/scheduler.db` 로 맞춰 두었습니다 (이전의 `/app` 기본값은 제거됨).
- **Fly.io**: `fly.toml`의 `[mounts]` + `fly volumes create scheduler_data` 가 필수입니다.  
  - SQLite는 **한 앱에 Machine 1대**만 권장: `fly scale count 1`
- 배포 로그에 `SQLite가 /data 볼륨이 아닌 URL을 사용 중` 경고가 나오면, 플랫폼의 환경 변수·볼륨 마운트를 다시 확인하세요.

### 예전에 쓰던 `scheduler.db` 옮기기 (로컬 → Docker 볼륨)

로컬 프로젝트 루트에 데이터가 있으면, 컨테이너 기동 후 한 번 복사할 수 있습니다.

```bash
docker compose up -d
docker cp ./scheduler.db scheduler_app-web-1:/data/scheduler.db
docker compose restart web
```

(컨테이너 이름은 `docker compose ps` 로 확인)
