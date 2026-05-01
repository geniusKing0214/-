#!/usr/bin/env bash
# 프로젝트 루트에서 실행. 같은 Wi‑Fi에서 <호스트IP>:8000 으로 접속 가능
cd "$(dirname "$0")"
echo "http://127.0.0.1:8000  |  mobile: http://<this-host-ip>:8000"
exec python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
