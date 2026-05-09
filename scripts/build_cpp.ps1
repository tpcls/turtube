# build_cpp.ps1 - C++ ASCII Engine Build Script for Windows
# Run: powershell -ExecutionPolicy Bypass -File scripts\build_cpp.ps1

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
$CppFile    = Join-Path $ProjectDir "src\ascii_engine.cpp"
$BuildDir   = Join-Path $ProjectDir "build"
$OutDll     = Join-Path $BuildDir "ascii_engine.dll"

Write-Host "[>>] Building C++ ASCII Engine..." -ForegroundColor Cyan

if (-not (Test-Path $CppFile)) {
    Write-Host "[FAIL] Source not found: $CppFile" -ForegroundColor Red; exit 1
}

if (-not (Test-Path $BuildDir)) { New-Item -ItemType Directory -Path $BuildDir | Out-Null }

# ── 컴파일러 탐색 ──────────────────────────────────────────
$CompilerType = $null
$CompilerPath = $null

# 1) MSVC (vswhere)
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { $vswhere = "${env:ProgramFiles}\Microsoft Visual Studio\Installer\vswhere.exe" }

if (Test-Path $vswhere) {
    # cl.exe 직접 탐색
    $clPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -find "VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe" 2>$null | Select-Object -Last 1
    if ($clPath -and (Test-Path $clPath)) {
        $CompilerType = "msvc"
        $CompilerPath = $clPath
    }
}

# 2) cl.exe in PATH
if (-not $CompilerType) {
    $cl = Get-Command cl.exe -ErrorAction SilentlyContinue
    if ($cl) { $CompilerType = "msvc"; $CompilerPath = $cl.Source }
}

# 3) MinGW g++
if (-not $CompilerType) {
    $gpp = Get-Command g++ -ErrorAction SilentlyContinue
    if ($gpp) { $CompilerType = "mingw"; $CompilerPath = $gpp.Source }
}

if (-not $CompilerType) {
    Write-Host "[FAIL] No compiler found." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install one of:"
    Write-Host "    1. Visual Studio 2019/2022 with C++ Build Tools"
    Write-Host "       https://visualstudio.microsoft.com/"
    Write-Host "    2. MSYS2 + MinGW-w64"
    Write-Host "       https://www.msys2.org/  then: pacman -S mingw-w64-x86_64-gcc"
    Write-Host "    3. WinLibs (standalone MinGW)"
    Write-Host "       https://winlibs.com/"
    exit 1
}

Write-Host "[INFO] Compiler: $CompilerType ($CompilerPath)" -ForegroundColor DarkCyan
Write-Host "[INFO] Source  : $CppFile"
Write-Host "[INFO] Output  : $OutDll"

# ── MSVC 빌드 ─────────────────────────────────────────────
if ($CompilerType -eq "msvc") {
    # vcvarsall 환경 초기화 (cl.exe가 PATH에 없을 경우)
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        if (Test-Path $vswhere) {
            $vsPath = & $vswhere -latest -products * `
                -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
                -property installationPath 2>$null | Select-Object -Last 1
            $vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $vcvars) {
                Write-Host "[INFO] Initializing MSVC environment..." -ForegroundColor DarkCyan
                # vcvars64.bat 환경 변수 적용
                $tempFile = [System.IO.Path]::GetTempFileName() + ".bat"
                @"
@echo off
call "$vcvars" >nul 2>&1
set
"@ | Set-Content $tempFile -Encoding ASCII
                $envLines = cmd /c $tempFile 2>$null
                Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
                foreach ($line in $envLines) {
                    if ($line -match "^([^=]+)=(.*)$") {
                        [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
                    }
                }
            }
        }
    }

    Write-Host "[INFO] Building with MSVC..." -ForegroundColor DarkCyan
    $clArgs = @(
        "/std:c++17",
        "/O2", "/GL", "/DNDEBUG",
        "/fp:fast",
        "/openmp",
        "/LD",
        "`"$CppFile`"",
        "/Fe:`"$OutDll`""
    )
    $result = Start-Process -FilePath "cl.exe" -ArgumentList $clArgs `
        -WorkingDirectory $BuildDir -Wait -PassThru -NoNewWindow
    if ($result.ExitCode -ne 0) {
        Write-Host "[FAIL] MSVC build failed (exit $($result.ExitCode))" -ForegroundColor Red; exit 1
    }
}

# ── MinGW 빌드 ──────────────────────────────────────────────
if ($CompilerType -eq "mingw") {
    # OpenMP 지원 확인
    $openmpFlag = ""
    $testResult = echo "" | & $CompilerPath -fopenmp -x c++ - -o "$env:TEMP\omp_test.exe" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $openmpFlag = "-fopenmp"
        Write-Host "[INFO] OpenMP supported" -ForegroundColor DarkCyan
        Remove-Item "$env:TEMP\omp_test.exe" -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "[WARN] OpenMP not available (single-threaded mode)" -ForegroundColor Yellow
    }

    Write-Host "[INFO] Building with MinGW g++..." -ForegroundColor DarkCyan
    $gppArgs = @(
        "-std=c++17",
        "-shared", "-fPIC",
        "-O3", "-DNDEBUG",
        "-ffast-math"
    )
    if ($openmpFlag) { $gppArgs += $openmpFlag }
    $gppArgs += @("`"$CppFile`"", "-o", "`"$OutDll`"")

    & $CompilerPath @gppArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] MinGW build failed" -ForegroundColor Red; exit 1
    }
}

# ── 결과 확인 ──────────────────────────────────────────────
if (Test-Path $OutDll) {
    $size = (Get-Item $OutDll).Length
    Write-Host ""
    Write-Host "[OK]  Build complete: $OutDll ($size bytes)" -ForegroundColor Green
    Write-Host ""
    Write-Host "[INFO] Usage:"
    Write-Host "       run_ascii.bat --url `"https://youtu.be/xxxxx`" --width 160 --color"
} else {
    Write-Host "[FAIL] Build failed: $OutDll not created" -ForegroundColor Red; exit 1
}
