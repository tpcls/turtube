@echo off
setlocal EnableDelayedExpansion
:: ──────────────────────────────────────────────────────────────────
:: C++ ASCII Engine 빌드 스크립트 (Windows)
:: 컴파일러 우선순위: MSVC cl.exe > MinGW g++
:: 출력: build\ascii_engine.dll
:: ──────────────────────────────────────────────────────────────────

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
pushd "%PROJECT_DIR%"
set "PROJECT_DIR=%CD%"
popd

set "CPP_FILE=%PROJECT_DIR%\src\ascii_engine.cpp"
set "BUILD_DIR=%PROJECT_DIR%\build"
set "OUT_DLL=%BUILD_DIR%\ascii_engine.dll"

echo [^>] C++ ASCII Engine 빌드 중...

:: 소스 파일 존재 확인
if not exist "%CPP_FILE%" (
    echo [X] 소스 파일 없음: %CPP_FILE%
    exit /b 1
)

:: build 디렉터리 생성
if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

:: ── 컴파일러 감지 ──────────────────────────────────────────────
set "COMPILER="
set "COMPILER_TYPE="

:: 1) MSVC (Visual Studio) 확인 ? vswhere 사용
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" set "VSWHERE=%ProgramFiles%\Microsoft Visual Studio\Installer\vswhere.exe"

if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (
        `"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe 2^>nul`
    ) do (
        if exist "%%i" (
            set "COMPILER=%%i"
            set "COMPILER_TYPE=msvc"
        )
    )
)

:: MSVC 환경 설정을 통해 cl.exe 직접 시도
if "!COMPILER_TYPE!"=="" (
    where cl.exe >nul 2>&1
    if !ERRORLEVEL! == 0 (
        set "COMPILER=cl.exe"
        set "COMPILER_TYPE=msvc"
    )
)

:: 2) MinGW/MSYS2 g++ 확인
if "!COMPILER_TYPE!"=="" (
    where g++ >nul 2>&1
    if !ERRORLEVEL! == 0 (
        set "COMPILER=g++"
        set "COMPILER_TYPE=mingw"
    )
)

if "!COMPILER_TYPE!"=="" (
    echo [X] 컴파일러를 찾을 수 없습니다.
    echo     다음 중 하나를 설치하세요:
    echo       1. Visual Studio 2019/2022 (C++ 빌드 도구 포함)
    echo          https://visualstudio.microsoft.com/
    echo       2. MSYS2 + MinGW-w64
    echo          https://www.msys2.org/
    echo          설치 후: pacman -S mingw-w64-x86_64-gcc
    echo       3. WinLibs (독립 MinGW)
    echo          https://winlibs.com/
    exit /b 1
)

echo [i] 컴파일러: !COMPILER_TYPE! (!COMPILER!)
echo [i] 소스: %CPP_FILE%
echo [i] 출력: %OUT_DLL%

:: ── MSVC 빌드 ──────────────────────────────────────────────────
if "!COMPILER_TYPE!"=="msvc" (

    :: MSVC 환경 변수 초기화 (vcvarsall.bat 실행)
    if "!COMPILER!" neq "cl.exe" (
        :: vswhere로 찾은 경우 vcvarsall.bat 경로 추출
        for /f "usebackq tokens=*" %%i in (
            `"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`
        ) do set "VS_INSTALL_PATH=%%i"

        if exist "!VS_INSTALL_PATH!\VC\Auxiliary\Build\vcvars64.bat" (
            call "!VS_INSTALL_PATH!\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
            set "COMPILER=cl.exe"
        )
    )

    :: OpenMP 지원 확인 (/openmp 플래그)
    set "OPENMP_FLAG=/openmp"

    cl.exe ^
        /std:c++17 ^
        /O2 /GL /DNDEBUG ^
        /fp:fast ^
        !OPENMP_FLAG! ^
        /LD ^
        "%CPP_FILE%" ^
        /Fe:"%OUT_DLL%" ^
        /link /DLL /OUT:"%OUT_DLL%"

    if !ERRORLEVEL! neq 0 (
        echo [X] MSVC 빌드 실패
        exit /b 1
    )
)

:: ── MinGW 빌드 ─────────────────────────────────────────────────
if "!COMPILER_TYPE!"=="mingw" (

    :: OpenMP 지원 확인
    echo. | g++ -fopenmp -x c++ - -o nul >nul 2>&1
    if !ERRORLEVEL! == 0 (
        set "OPENMP_FLAG=-fopenmp"
        echo [i] OpenMP 지원 확인됨
    ) else (
        set "OPENMP_FLAG="
        echo [!] OpenMP 미지원 (단일 스레드 모드)
    )

    g++ ^
        -std=c++17 ^
        -shared -fPIC ^
        -O3 -march=native -DNDEBUG ^
        -ffast-math ^
        !OPENMP_FLAG! ^
        "%CPP_FILE%" ^
        -o "%OUT_DLL%"

    if !ERRORLEVEL! neq 0 (
        echo [X] MinGW 빌드 실패
        exit /b 1
    )
)

:: ── 결과 확인 ──────────────────────────────────────────────────
if exist "%OUT_DLL%" (
    for %%A in ("%OUT_DLL%") do set "FILE_SIZE=%%~zA"
    echo.
    echo [v] 빌드 완료: %OUT_DLL%
    echo [i] 파일 크기: !FILE_SIZE! bytes
    echo.
    echo [i] 사용 예시:
    echo     python src\video_ascii.py --url "https://youtu.be/xxxxx" --width 160 --color
) else (
    echo [X] 빌드 실패: %OUT_DLL% 생성되지 않음
    exit /b 1
)

endlocal
