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
# SQLite: 영구 볼륨(/data). Render에서 PostgreSQL을 쓰면 대시보드의 DATABASE_URL이 이 기본값을 덮어씁니다.
ENV DATABASE_URL=sqlite:////data/scheduler.db
RUN mkdir -p /data

# Render 등 클라우드는 PORT 환경 변수로 포트를 지정합니다.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
