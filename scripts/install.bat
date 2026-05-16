@echo off
setlocal EnableDelayedExpansion

:: ====================================================================
::       Video ASCII Converter - Windows Auto Installer
:: ====================================================================

powershell -Command "Write-Host '  __________________________________________________________' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |                                                          |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |   _______ _    _ _____ _______ _    _ ____  ______      |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |  |__   __| |  | |  __ \__   __| |  | |  _ \|  ____|     |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |     | |  | |  | | |__) | | |  | |  | | |_) | |__        |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |     | |  | |  | |  _  /  | |  | |  | |  _ <|  __|       |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |     | |  | |__| | | \ \  | |  | |__| | |_) | |____      |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |     |_|   \____/|_|  \_\ |_|   \____/|____/|______|     |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |                                                          |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |    GPU-Accelerated Video -> ASCII Art  |  Installer     |' -ForegroundColor Cyan"
powershell -Command "Write-Host ' |__________________________________________________________|' -ForegroundColor Cyan"
echo.

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
pushd "%PROJECT_DIR%"
set "PROJECT_DIR=%CD%"
popd

set "VENV_DIR=%PROJECT_DIR%\venv_ascii"
set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "PYTHON_VENV=%VENV_DIR%\Scripts\python.exe"

:: 1. Python Check (3.9+)
echo [INFO] Checking Python version...

set "PYTHON="
:: Search for python in various names
for %%C in (python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3 python) do (
    if "!PYTHON!"=="" (
        for /f "tokens=*" %%P in ('where %%C 2^>nul') do (
            if "!PYTHON!"=="" (
                for /f "tokens=2" %%V in ('"%%P" -V 2^>nul') do (
                    for /f "tokens=1,2 delims=." %%A in ("%%V") do (
                        set "MAJ=%%A"
                        set "MIN=%%B"
                        if !MAJ! GEQ 3 (
                            if !MIN! GEQ 9 (
                                set "PYTHON=%%P"
                                echo [OK] Python !MAJ!.!MIN! found: %%P
                            )
                        )
                    )
                )
            )
        )
    )
)

if "!PYTHON!"=="" (
    echo [ERROR] Python 3.9+ not found.
    if exist "%SCRIPT_DIR%python_install.exe" (
        echo [INFO] scripts\python_install.exe found.
        set /p "DO_INSTALL=Install Python? [Y/n] "
        if /i not "!DO_INSTALL!"=="n" (
            start /wait "" "%SCRIPT_DIR%python_install.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
            echo [INFO] Python installed. Please run install.bat again.
            pause
            exit /b 0
        )
    )
    echo Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)

:: 2. venv Setup
echo.
echo [INFO] Setting up virtual environment (venv)...

if exist "%VENV_DIR%" (
    echo [WARN] Existing venv found: %VENV_DIR%
    set /p "REUSE=Reuse existing venv? [Y/n] "
    if /i "!REUSE!"=="n" (
        rmdir /s /q "%VENV_DIR%"
        "!PYTHON!" -m venv "%VENV_DIR%"
    )
) else (
    "!PYTHON!" -m venv "%VENV_DIR%"
)

if not exist "%PYTHON_VENV%" (
    echo [ERROR] Failed to create venv.
    exit /b 1
)

"%PIP%" install --upgrade pip setuptools wheel -q
echo [OK] venv ready.

:: 3. Dependencies
echo.
echo [INFO] Installing dependencies (opencv, numpy, Pillow, yt-dlp, numba)...

"%PIP%" install --upgrade ^
    "opencv-python-headless>=4.8" ^
    "numpy>=1.24" ^
    "Pillow>=10.0" ^
    "yt-dlp>=2024.1.1" ^
    "numba>=0.57" ^
    -q

echo [OK] Dependencies installed.

:: 4. ffmpeg Check
echo.
echo [INFO] Checking ffmpeg...

where ffmpeg >nul 2>&1
if !ERRORLEVEL! == 0 (
    for /f "tokens=*" %%V in ('ffmpeg -version 2^>^&1 ^| findstr /i "ffmpeg version"') do echo [OK] %%V
) else (
    echo [WARN] ffmpeg not found in PATH.
)

:: 5. GPU Backend (CUDA)
echo.
echo [INFO] Detecting GPU backend...

set "BACKEND_INSTALLED=cpu"
set "CUDA_MAJOR=0"

where nvidia-smi >nul 2>&1
if !ERRORLEVEL! == 0 (
    echo [INFO] NVIDIA GPU detected.
    for /f "tokens=*" %%L in ('nvidia-smi 2^>nul ^| findstr /i "CUDA Version"') do set "CUDA_LINE=%%L"
    if defined CUDA_LINE (
        for /f "tokens=3 delims=: " %%V in ("!CUDA_LINE!") do (
            for /f "tokens=1 delims=." %%M in ("%%V") do set "CUDA_MAJOR=%%M"
        )
        echo [INFO] CUDA version: !CUDA_LINE!
    )
    
    set "TORCH_INDEX="
    if !CUDA_MAJOR! GEQ 12 (
        set "TORCH_INDEX=https://download.pytorch.org/whl/cu126"
    ) else if !CUDA_MAJOR! EQU 11 (
        set "TORCH_INDEX=https://download.pytorch.org/whl/cu118"
    ) else (
        set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
    )

    echo [INFO] Installing PyTorch...
    "%PIP%" install torch torchvision --index-url "!TORCH_INDEX!" -q

    "%PYTHON_VENV%" -c "import torch; exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
    if !ERRORLEVEL! == 0 (
        for /f "tokens=*" %%D in ('"%PYTHON_VENV%" -c "import torch; print(torch.cuda.get_device_name(0))" 2^>nul') do echo [OK] PyTorch CUDA confirmed: %%D
        set "BACKEND_INSTALLED=cuda"
    )
)

:: 8. Runner script
echo.
echo [INFO] Creating runner script...
(
    echo @echo off
    echo setlocal
    echo REM video_ascii.py execution wrapper
    echo set "SCRIPT_DIR=%%~dp0"
    echo set "PROJECT_DIR=%%SCRIPT_DIR%%"
    echo call "%%PROJECT_DIR%%venv_ascii\Scripts\activate.bat"
    echo python "%%PROJECT_DIR%%src\video_ascii.py" %%*
    echo endlocal
) > "%PROJECT_DIR%\run_ascii.bat"

echo [OK] Created run_ascii.bat

echo.
echo [OK] Installation complete!
pause
endlocal
