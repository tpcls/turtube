@echo off
setlocal
:: Change code page to UTF-8 for better Unicode support
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%venv_ascii\Scripts\activate.bat" (
    echo [ERROR] venv not found. Run: scripts\install.ps1
    pause
    exit /b 1
)
call "%SCRIPT_DIR%venv_ascii\Scripts\activate.bat"
python "%SCRIPT_DIR%src\youtube_html_ascii.py" %* <nul
endlocal