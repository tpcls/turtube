@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

echo [i] Turtube 통합 설치 및 실행 도우미
echo ========================================

REM 1. FFmpeg 확인
if not exist "ffmpeg.exe" (
    echo [!] ffmpeg.exe를 찾을 수 없습니다. 현재 폴더에 넣어주세요.
)
if not exist "ffplay.exe" (
    echo [!] ffplay.exe를 찾을 수 없습니다. 현재 폴더에 넣어주세요.
)

REM 2. Python 가상환경 설정
if not exist "venv_ascii" (
    echo [i] 가상환경 venv_ascii 생성 중...
    python -m venv venv_ascii
)

echo [i] 가상환경 활성화 및 라이브러리 업데이트...
call venv_ascii\Scripts\activate.bat
python -m pip install --upgrade pip
if exist "requirements.txt" (
    pip install -r requirements.txt
) else (
    pip install requests opencv-python pillow numpy playwright yt-dlp
)
python -m playwright install chromium

REM 3. API 서버 실행 (Node.js)
if exist "package.json" (
    REM 3000번 포트가 이미 사용 중인지 확인
    netstat -ano | findstr :3000 > nul
    if !errorlevel! equ 0 (
        echo [i] API 서버가 이미 실행 중입니다 (Port 3000). 실행을 건너뜁니다.
    ) else (
        echo [i] API 서버 npm start 실행 중...
        start /b npm start
        timeout /t 5 > nul
    )
)

REM 4. 터튜브 실행
echo [i] 터튜브 Turtube 를 시작합니다...
python src\youtube_html_ascii.py --interactive

deactivate
endlocal
