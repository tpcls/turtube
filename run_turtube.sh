#!/bin/bash

echo "[i] Turtube 통합 설치 및 실행 도우미 (Linux/macOS)"
echo "========================================"

# 1. 가상환경 설정
if [ ! -d "venv_ascii" ]; then
    echo "[i] 가상환경(venv_ascii) 생성 중..."
    python3 -m venv venv_ascii
fi

echo "[i] 가상환경 활성화 및 라이브러리 업데이트..."
source venv_ascii/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    pip install requests opencv-python pillow numpy playwright yt-dlp
fi
python -m playwright install chromium

# 2. API 서버 실행 (Node.js)
if [ -f "package.json" ]; then
    echo "[i] API 서버(npm start) 실행 중..."
    npm start &
    sleep 5
fi

# 3. 터튜브 실행
echo "[i] 터튜브(Turtube)를 시작합니다..."
python3 src/youtube_html_ascii.py --interactive

deactivate
