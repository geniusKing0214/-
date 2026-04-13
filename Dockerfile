FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
# 반드시 영구 볼륨이 마운트된 경로 (docker-compose / fly.toml의 /data 와 일치)
ENV DATABASE_URL=sqlite:////data/scheduler.db

# Render 등 클라우드는 PORT 환경 변수로 포트를 지정합니다.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
