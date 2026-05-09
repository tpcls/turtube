# install.ps1 - Video ASCII Converter Windows Setup Script
$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvDir    = Join-Path $ProjectDir "venv_ascii"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPy     = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "`n--- Video ASCII Converter Setup ---"

# 1. Python Discovery
Write-Host ">> Checking Python (3.9+)"
$PythonExe = $null

# A. Try Python Launcher for Windows (py)
if (Get-Command py -ErrorAction SilentlyContinue) {
    # Try to find the latest version >= 3.9
    $pyVers = & py -0p 2>$null
    foreach ($line in $pyVers) {
        if ($line -match "-V:(\d+)\.(\d+).*\s+(.*)$") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            $path  = $Matches[3].Trim()
            if ($major -eq 3 -and $minor -ge 9) {
                if (Test-Path $path) {
                    $PythonExe = $path
                    Write-Host "[INFO] Found Python $major.$minor via Launcher: $PythonExe"
                    break
                }
            }
        }
    }
}

# B. Check common paths if not found via launcher
if (-not $PythonExe) {
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )

    foreach ($path in $commonPaths) {
        if (Test-Path $path) {
            $PythonExe = $path
            Write-Host "[INFO] Found Python in common path: $PythonExe"
            break
        }
    }
}

# C. Check PATH candidates
if (-not $PythonExe) {
    $candidates = @("python3.14","python3.13","python3.12","python3.11","python3.10","python3.9","python3","python")
    foreach ($cmd in $candidates) {
        if ($PythonExe) { break }
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $found -or $found.Source -like "*WindowsApps*") { continue }
        $PythonExe = $found.Source
        Write-Host "[OK] Python found in PATH: $PythonExe"
    }
}

if (-not $PythonExe) {
    Write-Host "[FAIL] Python 3.9+ not found. Please install it from python.org"; exit 1
}

# 2. venv Setup
Write-Host ">> Setting up virtual environment"
if (-not (Test-Path $VenvDir)) { & $PythonExe -m venv $VenvDir }
& $VenvPip install --upgrade pip setuptools wheel -q
Write-Host "[OK] Virtual environment ready"

# 3. Dependencies
Write-Host ">> Installing dependencies"
& $VenvPip install --upgrade "opencv-python-headless>=4.8" "numpy>=1.24" "Pillow>=10.0" "yt-dlp>=2024.1.1" "numba>=0.57" -q
Write-Host "[OK] Common packages installed"

# 4. CUDA Detection & PyTorch
Write-Host ">> Detecting GPU / Installing PyTorch"
$Backend = "cpu"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $smi = (nvidia-smi 2>$null) -join "`n"
    if ($smi -match "CUDA Version:\s*(\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        Write-Host "[INFO] CUDA $major detected"
        $Index = if ($major -ge 12) { "https://download.pytorch.org/whl/cu124" } elseif ($major -eq 11) { "https://download.pytorch.org/whl/cu118" } else { "https://download.pytorch.org/whl/cpu" }
        Write-Host "[INFO] Installing PyTorch from $Index"
        & $VenvPip install torch torchvision --index-url $Index -q
        $cudaOk = & $VenvPy -c "import torch; print(torch.cuda.is_available())" 2>$null
        if ($cudaOk -eq "True") {
            Write-Host "[OK] CUDA PyTorch verified"
            $Backend = "cuda"
            $Cupy = if ($major -ge 12) { "cupy-cuda12x" } elseif ($major -eq 11) { "cupy-cuda11x" } else { "" }
            if ($Cupy) { 
                Write-Host "[INFO] Installing CuPy: $Cupy"
                & $VenvPip install $Cupy -q 
            }
        } else {
            Write-Host "[WARN] PyTorch installed but CUDA is not available"
        }
    }
} else { Write-Host "[INFO] No NVIDIA GPU" }

# 5. Verification
Write-Host ">> Verification"
& $VenvPy -c "import torch, numpy, cv2; print(f'PyTorch: {torch.__version__} | CUDA: {torch.cuda.is_available()}'); print(f'NumPy: {numpy.__version__} | OpenCV: {cv2.__version__}')"

# 6. Run script
$run = "@echo off`ncall `"%~dp0venv_ascii\Scripts\activate.bat`"`npython `"%~dp0src\video_ascii.py`" %*`n"
$enc = [System.Text.Encoding]::GetEncoding(949)
[System.IO.File]::WriteAllText((Join-Path $ProjectDir "run_ascii.bat"), $run, $enc)

Write-Host "[OK] Setup complete! Backend: $Backend"
