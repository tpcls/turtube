#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# C++ ASCII Engine 빌드 스크립트 (macOS/Linux)
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CPP_FILE="$PROJECT_DIR/src/ascii_engine.cpp"
OUT_FILE="$PROJECT_DIR/build/libascii_engine.so"

echo "[▶] C++ ASCII Engine 빌드 중..."

# 출력 디렉토리 생성
mkdir -p "$PROJECT_DIR/build"

# 입력 파일 존재 확인
if [[ ! -f "$CPP_FILE" ]]; then
    echo "[✗] 소스 파일 없음: $CPP_FILE"
    exit 1
fi

# ── 플랫폼 감지 ────────────────────────────────────────────────
OS="$(uname -s)"
echo "[i] OS: $OS"

# ── OpenMP 플래그 설정 ─────────────────────────────────────────
OPENMP_FLAGS=""

if [[ "$OS" == "Darwin" ]]; then
    if ! command -v brew &>/dev/null; then
        echo "[!] Homebrew 없음 → OpenMP 스킵 (단일 스레드로 동작)"
    else
        # [최적화] brew list 대신 brew --prefix로 설치 여부 확인 (훨씬 빠름)
        LIBOMP_PATH="$(brew --prefix libomp 2>/dev/null)" || {
            echo "[i] libomp 설치 중..."
            brew install libomp -q
            LIBOMP_PATH="$(brew --prefix libomp)"
        }
        OPENMP_FLAGS="-Xpreprocessor -fopenmp -I${LIBOMP_PATH}/include -L${LIBOMP_PATH}/lib -lomp"
        echo "[✓] OpenMP 경로: $LIBOMP_PATH"
    fi
else
    # Linux: gcc/clang 모두 -fopenmp 직접 지원
    OPENMP_FLAGS="-fopenmp"
fi

# ── 컴파일러 감지 ──────────────────────────────────────────────
# [버그수정] 두 컴파일러 모두 없는 경우 명확한 에러 출력
CXX="${CXX:-}"
if [[ -z "$CXX" ]]; then
    if   command -v clang++ &>/dev/null; then CXX="clang++"
    elif command -v g++     &>/dev/null; then CXX="g++"
    else
        echo "[✗] 컴파일러 없음: clang++ 또는 g++ 중 하나를 설치하세요."
        if [[ "$OS" == "Darwin" ]]; then
            echo "    → xcode-select --install"
        else
            echo "    → sudo apt-get install g++"
        fi
        exit 1
    fi
fi
echo "[i] 컴파일러: $CXX ($(command -v "$CXX"))"

# ── 컴파일 ─────────────────────────────────────────────────────
# [버그수정] -std=c++17 추가: chrono, auto, structured bindings 필요
# -O3 -march=native: 최대 로컬 최적화
# -DNDEBUG: assert 비활성화
# -ffast-math: 부동소수점 최적화 (결과 미세 차이 허용)
echo "[i] 컴파일: $CPP_FILE → $OUT_FILE"
"$CXX" \
    -std=c++17 \
    -fPIC -shared \
    -O3 -march=native -DNDEBUG \
    -ffast-math \
    $OPENMP_FLAGS \
    "$CPP_FILE" \
    -o "$OUT_FILE"

if [[ -f "$OUT_FILE" ]]; then
    SIZE=$(ls -lh "$OUT_FILE" | awk '{print $5}')
    echo "[✓] 빌드 완료: $OUT_FILE ($SIZE)"
    echo ""
    echo "[i] 사용 예시:"
    echo "    ./run_ascii.sh --url \"https://youtu.be/xxxxx\" --width 160 --color"
else
    echo "[✗] 빌드 실패: $OUT_FILE 생성되지 않음"
    exit 1
fi
