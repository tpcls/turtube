@echo off
setlocal
REM video_ascii.py execution wrapper
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%"
call "%PROJECT_DIR%venv_ascii\Scripts\activate.bat"
python "%PROJECT_DIR%src\video_ascii.py" %*
endlocal
