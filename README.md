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
