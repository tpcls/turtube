@echo off
call "%~dp0venv_ascii\Scripts\activate.bat"
python "%~dp0src\video_ascii.py" %*
