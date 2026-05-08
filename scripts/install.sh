#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║         Video ASCII Converter — 자동 환경 설치 스크립트           ║
# ║  NVIDIA GPU → CUDA (PyTorch)                                     ║
# ║  Apple Silicon → MLX                                             ║
# ║  그 외 → CPU (numpy + opencv)                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── 색상 ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[✓]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[✗]${RESET}    $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}▶ $*${RESET}"; }

# ── 배너 ──────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat << 'BANNER'
 ██╗   ██╗██╗██████╗ ███████╗ ██████╗      █████╗ ███████╗ ██████╗██╗██╗
 ██║   ██║██║██╔══██╗██╔════╝██╔═══██╗    ██╔══██╗██╔════╝██╔════╝██║██║
 ██║   ██║██║██║  ██║█████╗  ██║   ██║    ███████║███████╗██║     ██║██║
 ╚██╗ ██╔╝██║██║  ██║██╔══╝  ██║   ██║    ██╔══██║╚════██║██║     ██║██║
  ╚████╔╝ ██║██████╔╝███████╗╚██████╔╝    ██║  ██║███████║╚██████╗██║██║
   ╚═══╝  ╚═╝╚═════╝ ╚══════╝ ╚═════╝     ╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝╚═╝
              GPU-Accelerated Video → ASCII Art  |  Auto Installer
BANNER
echo -e "${RESET}"

# ══════════════════════════════════════════════════════════════════
# 1. OS / 아키텍처 감지
# ══════════════════════════════════════════════════════════════════
step "시스템 감지"

OS="$(uname -s)"
ARCH="$(uname -m)"
info "OS: ${OS} | ARCH: ${ARCH}"

# [버그수정] 'if $IS_MACOS' 패턴 제거 → [[ ]] 비교로 통일
# 이유: bash가 "true"/"false" 문자열을 외부 명령어로 실행하는 위험한 패턴
IS_MACOS=false; IS_LINUX=false; IS_APPLE_SILICON=false
[[ "$OS" == "Darwin" ]] && IS_MACOS=true
[[ "$OS" == "Linux"  ]] && IS_LINUX=true
[[ "$IS_MACOS" == true && "$ARCH" == "arm64" ]] && IS_APPLE_SILICON=true

info "macOS=$IS_MACOS | Linux=$IS_LINUX | Apple Silicon=$IS_APPLE_SILICON"

# ══════════════════════════════════════════════════════════════════
# 2. Python 확인 (3.9+)
# ══════════════════════════════════════════════════════════════════
step "Python 버전 확인"

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR="${VER%%.*}"; MINOR="${VER#*.}"
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 9 ]]; then
            PYTHON="$cmd"
            success "Python $VER 발견: $(command -v "$cmd")"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.9+ 없음 → 자동 설치 시도"
    if [[ "$IS_MACOS" == true ]]; then
        if command -v brew &>/dev/null; then
            brew install python@3.11
            PYTHON="python3.11"
        else
            error "Homebrew 없음. https://brew.sh 에서 먼저 설치하세요."
        fi
    elif [[ "$IS_LINUX" == true ]]; then
        sudo apt-get update -qq && sudo apt-get install -y python3.11 python3.11-venv python3-pip
        PYTHON="python3.11"
    else
        error "Windows는 WSL2 또는 Python 수동 설치 후 재실행하세요."
    fi
fi

# ══════════════════════════════════════════════════════════════════
# 3. pip / venv 준비
# ══════════════════════════════════════════════════════════════════
step "가상환경(venv) 설정"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv_ascii"

if [[ -d "$VENV_DIR" ]]; then
    warn "기존 venv 발견: $VENV_DIR"
    read -r -p "  재사용하시겠습니까? [Y/n] " REUSE
    REUSE="${REUSE:-Y}"
    if [[ "$REUSE" =~ ^[Nn] ]]; then
        info "기존 venv 삭제 후 재생성..."
        rm -rf "$VENV_DIR"
        "$PYTHON" -m venv "$VENV_DIR"
    fi
else
    "$PYTHON" -m venv "$VENV_DIR"
fi

# venv 활성화
source "$VENV_DIR/bin/activate"
PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

"$PIP" install --upgrade pip setuptools wheel -q
success "가상환경 준비 완료: $VENV_DIR"

# ══════════════════════════════════════════════════════════════════
# 4. 공통 의존성 설치
# ══════════════════════════════════════════════════════════════════
step "공통 패키지 설치 (opencv, numpy, Pillow, yt-dlp, numba)"

"$PIP" install --upgrade \
    "opencv-python-headless>=4.8" \
    "numpy>=1.24" \
    "Pillow>=10.0" \
    "yt-dlp>=2024.1.1" \
    "numba>=0.57" \
    -q

success "공통 패키지 설치 완료"

# ══════════════════════════════════════════════════════════════════
# 5. ffmpeg 설치 확인
# ══════════════════════════════════════════════════════════════════
step "ffmpeg 설치 확인"

if command -v ffmpeg &>/dev/null; then
    success "ffmpeg 이미 설치됨: $(ffmpeg -version 2>&1 | head -1)"
else
    warn "ffmpeg 없음 → 자동 설치 시도"
    if [[ "$IS_MACOS" == true ]]; then
        if command -v brew &>/dev/null; then
            brew install ffmpeg
            success "ffmpeg 설치 완료 (Homebrew)"
        else
            warn "Homebrew 없음. ffmpeg 수동 설치 권장: https://ffmpeg.org"
        fi
    elif [[ "$IS_LINUX" == true ]]; then
        if   command -v apt-get &>/dev/null; then
            sudo apt-get install -y ffmpeg -qq && success "ffmpeg 설치 완료 (apt)"
        elif command -v dnf     &>/dev/null; then
            sudo dnf install -y ffmpeg         && success "ffmpeg 설치 완료 (dnf)"
        elif command -v pacman  &>/dev/null; then
            sudo pacman -S --noconfirm ffmpeg  && success "ffmpeg 설치 완료 (pacman)"
        else
            warn "패키지 매니저 미확인. ffmpeg 수동 설치: https://ffmpeg.org"
        fi
    fi
fi

# ══════════════════════════════════════════════════════════════════
# 6. GPU 백엔드 자동 선택
# ══════════════════════════════════════════════════════════════════
step "GPU 백엔드 감지 및 설치"

BACKEND_INSTALLED="cpu"

# ── (A) Apple Silicon → MLX ───────────────────────────────────────
if [[ "$IS_APPLE_SILICON" == true ]]; then
    info "Apple Silicon 감지 → MLX 설치"
    "$PIP" install --upgrade mlx -q && {
        success "MLX 설치 완료"
        BACKEND_INSTALLED="mlx"
    } || warn "MLX 설치 실패 → CPU 폴백"
fi

# ── (B) NVIDIA GPU → CUDA (PyTorch) ──────────────────────────────
if [[ "$BACKEND_INSTALLED" == "cpu" && "$IS_LINUX" == true ]]; then
    if command -v nvidia-smi &>/dev/null; then
        info "NVIDIA GPU 감지됨"

        CUDA_VER_FULL=$(nvidia-smi 2>/dev/null | grep -oP 'CUDA Version: \K[0-9]+\.[0-9]+' || echo "")
        CUDA_MAJOR="${CUDA_VER_FULL%%.*}"
        info "감지된 CUDA 버전: ${CUDA_VER_FULL:-알 수 없음}"

        if   [[ "$CUDA_MAJOR" -ge 12 ]]; then TORCH_INDEX="https://download.pytorch.org/whl/cu124"
        elif [[ "$CUDA_MAJOR" -eq 11 ]]; then TORCH_INDEX="https://download.pytorch.org/whl/cu118"
        else
            warn "CUDA 11 미만 감지 → CPU PyTorch 설치"
            TORCH_INDEX="https://download.pytorch.org/whl/cpu"
        fi

        info "PyTorch 설치 중 (index: $TORCH_INDEX) ..."
        "$PIP" install torch torchvision --index-url "$TORCH_INDEX" -q && {
            if "$PYTHON" -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
                success "PyTorch CUDA 설치 확인"
                BACKEND_INSTALLED="cuda"

                # CuPy 추가 (선택)
                info "CuPy 설치 중 (선택 가속) ..."
                if   [[ "$CUDA_MAJOR" -ge 12 ]]; then CUPY_PKG="cupy-cuda12x"
                elif [[ "$CUDA_MAJOR" -eq 11 ]]; then CUPY_PKG="cupy-cuda11x"
                else CUPY_PKG=""; fi

                if [[ -n "$CUPY_PKG" ]]; then
                    "$PIP" install "$CUPY_PKG" -q \
                        && success "CuPy ($CUPY_PKG) 설치 완료" \
                        || warn "CuPy 설치 실패 (무시 가능)"
                fi
            else
                warn "torch.cuda.is_available() = False → CPU 모드로 진행"
            fi
        } || warn "PyTorch 설치 실패 → CPU 폴백"
    else
        info "NVIDIA GPU 없음 → CPU 모드"
    fi
fi

# ── (C) macOS Intel → CPU PyTorch ────────────────────────────────
if [[ "$IS_MACOS" == true && "$IS_APPLE_SILICON" == false && "$BACKEND_INSTALLED" == "cpu" ]]; then
    info "macOS Intel → CPU 전용 PyTorch"
    "$PIP" install torch torchvision --index-url https://download.pytorch.org/whl/cpu -q \
        && success "CPU PyTorch 설치 완료" || warn "PyTorch 설치 실패 (numpy로 동작)"
fi

# ══════════════════════════════════════════════════════════════════
# 7. C++ ASCII 엔진 빌드
# [버그수정] 완료 메시지 출력 전에 빌드 → 빌드 실패 시 성공 메시지 안 보임
# ══════════════════════════════════════════════════════════════════
step "C++ ASCII 엔진 빌드"

BUILD_SCRIPT="$SCRIPT_DIR/build_cpp.sh"

if [[ -f "$BUILD_SCRIPT" ]]; then
    bash "$BUILD_SCRIPT" || error "C++ 엔진 빌드 실패: $BUILD_SCRIPT"
    success "C++ 엔진 빌드 완료 (libascii_engine.so)"
else
    warn "build_cpp.sh 없음: C++ 엔진 빌드 스킵"
fi

# ══════════════════════════════════════════════════════════════════
# 8. 설치 검증
# ══════════════════════════════════════════════════════════════════
step "설치 검증"

"$PYTHON" - << 'PYCHECK'
import sys

results = {}

try:
    import numpy as np
    results['numpy'] = f"✓ {np.__version__}"
except ImportError:
    results['numpy'] = "✗ 미설치"

try:
    import cv2
    results['opencv'] = f"✓ {cv2.__version__}"
except ImportError:
    results['opencv'] = "✗ 미설치"

try:
    import PIL
    results['Pillow'] = f"✓ {PIL.__version__}"
except ImportError:
    results['Pillow'] = "✗ 미설치 (MP4 출력 불가)"

try:
    import numba
    results['numba'] = f"✓ {numba.__version__}"
except ImportError:
    results['numba'] = "- 미설치 (선택사항)"

# [버그수정] torch import 실패 시 cuda_ok 미정의 → try 블록 안에서 처리
cuda_ok = False
try:
    import torch
    cuda_ok = torch.cuda.is_available()
    device = torch.cuda.get_device_name(0) if cuda_ok else "없음"
    results['torch'] = f"✓ {torch.__version__} | CUDA: {cuda_ok} | GPU: {device}"
except ImportError:
    results['torch'] = "- 미설치"

try:
    import mlx.core as mx
    results['mlx'] = "✓ 설치됨 (Apple Silicon)"
except ImportError:
    results['mlx'] = "- 미설치"

try:
    import cupy as cp
    results['cupy'] = f"✓ {cp.__version__}"
except ImportError:
    results['cupy'] = "- 미설치"

try:
    import yt_dlp
    results['yt-dlp'] = f"✓ {yt_dlp.version.__version__}"
except ImportError:
    results['yt-dlp'] = "✗ 미설치"

print()
for k, v in results.items():
    color = '\033[32m' if v.startswith('✓') else ('\033[33m' if v.startswith('-') else '\033[31m')
    print(f"  {color}{k:<10}\033[0m {v}")

if cuda_ok:
    backend = "CUDA (PyTorch)"
elif results.get('mlx', '').startswith('✓'):
    backend = "MLX (Apple Silicon)"
elif results.get('cupy', '').startswith('✓'):
    backend = "CuPy"
else:
    backend = "CPU (numpy)"

print(f"\n  \033[1m\033[36m선택된 백엔드: {backend}\033[0m")
PYCHECK

# ══════════════════════════════════════════════════════════════════
# 9. 실행 스크립트 생성
# ══════════════════════════════════════════════════════════════════
step "실행 스크립트 생성"

cat > "$PROJECT_DIR/run_ascii.sh" << RUNSCRIPT
#!/usr/bin/env bash
# video_ascii.py 실행 래퍼 (venv 자동 활성화)
set -euo pipefail

PROJECT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
source "${VENV_DIR}/bin/activate"
python "\$PROJECT_DIR/src/video_ascii.py" "\$@"
RUNSCRIPT
chmod +x "$PROJECT_DIR/run_ascii.sh"
success "실행 스크립트 생성: run_ascii.sh"

# ══════════════════════════════════════════════════════════════════
# 10. 완료 메시지
# [버그수정] C++ 빌드 완료 후 출력 (이전: 빌드 전에 "설치 완료" 출력됨)
# ══════════════════════════════════════════════════════════════════

# [버그수정] ${VAR^^} → tr 사용 (bash 3 / macOS 기본 sh 호환)
BACKEND_UPPER="$(echo "$BACKEND_INSTALLED" | tr '[:lower:]' '[:upper:]')"

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✓ 설치 완료!  백엔드: ${BACKEND_UPPER}${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
echo -e "${BOLD}사용 예시:${RESET}"
echo -e "  ${CYAN}# YouTube URL 실시간 재생${RESET}"
echo -e "  ${CYAN}./run_ascii.sh --url \"https://youtu.be/dQw4w9WgXcQ\" --width 160 --color${RESET}"
echo ""
echo -e "  ${CYAN}# URL → HTML 저장${RESET}"
echo -e "  ${CYAN}./run_ascii.sh --url \"https://youtu.be/xxxxx\" --width 200 --output out.html --color${RESET}"
echo ""
echo -e "  ${CYAN}# 화질 지정 (480p)${RESET}"
echo -e "  ${CYAN}./run_ascii.sh --url \"https://youtu.be/xxxxx\" --quality 480 --width 120 --color${RESET}"
echo ""
echo -e "  ${CYAN}# 로컬 파일${RESET}"
echo -e "  ${CYAN}./run_ascii.sh --input video.mp4 --width 160 --color${RESET}"
echo ""
echo -e "  ${CYAN}# 웹캠 실시간${RESET}"
echo -e "  ${CYAN}./run_ascii.sh --webcam --width 120 --color${RESET}"
echo ""
echo -e "  또는 venv 직접 활성화:"
echo -e "  ${YELLOW}source ${VENV_DIR}/bin/activate${RESET}"
echo -e "  ${YELLOW}python src/video_ascii.py --help${RESET}"
echo ""
