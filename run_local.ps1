# 프로젝트 루트에서 로컬 서버 (같은 Wi‑Fi 휴대폰에서 PC_IP:8000 접속 가능)
Set-Location $PSScriptRoot
Write-Host "http://127.0.0.1:8000  |  모바일: http://<이-PC-IP>:8000" -ForegroundColor Cyan
py -3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
