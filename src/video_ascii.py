#!/usr/bin/env python3
from __future__ import annotations

"""
██╗   ██╗██╗██████╗ ███████╗ ██████╗      █████╗ ███████╗ ██████╗██╗██╗
██║   ██║██║██╔══██╗██╔════╝██╔═══██╗    ██╔══██╗██╔════╝██╔════╝██║██║
██║   ██║██║██║  ██║█████╗  ██║   ██║    ███████║███████╗██║     ██║██║
╚██╗ ██╔╝██║██║  ██║██╔══╝  ██║   ██║    ██╔══██║╚════██║██║     ██║██║
 ╚████╔╝ ██║██████╔╝███████╗╚██████╔╝    ██║  ██║███████║╚██████╗██║██║
  ╚═══╝  ╚═╝╚═════╝ ╚══════╝ ╚═════╝     ╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝╚═╝

Video → ASCII Art Converter
- GPU 가속: CUDA (NVIDIA) / MLX (Apple Silicon) / CPU 멀티스레드 자동 선택
- 해상도 자유 조정 (width, height 독립 설정)
- 트루컬러 ANSI 색상 (256색 / 24비트)
- 실시간 터미널 출력 또는 HTML/MP4 파일 저장
- yt-dlp 지원: YouTube / Twitch / TikTok 등 1000+ 사이트 URL 직접 입력

사용법:
    python video_ascii.py --input video.mp4 --width 160 --height 45 --color
    python video_ascii.py --url "https://youtu.be/xxxxx" --width 160 --color
    python video_ascii.py --url "https://youtu.be/xxxxx" --width 200 --output out.html --color
    python video_ascii.py --url "https://youtu.be/xxxxx" --quality 720 --width 160 --color
    python video_ascii.py --webcam --width 120 --color   # 웹캠 실시간
"""

import argparse
import sys
import os
import time
import subprocess
import signal

# FFmpeg 멀티스레딩 충돌 방지 (Assertion fctx->async_lock failed 해결)
os.environ["OPENCV_FFMPEG_THREADS"] = "1"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "threads;1"
import threading
import queue
import ctypes
import importlib.util
import platform
import urllib.request
import re as _re
import numpy as np
import cv2
from pathlib import Path


class CppAsciiEngine:
    """ctypes wrapper around libascii_engine.so / ascii_engine.dll."""

    def __init__(self, lib_path: str | None = None):
        self.has_cpp = False
        self._lib = None

        if lib_path is None:
            src_dir = os.path.dirname(__file__)
            project_dir = os.path.dirname(src_dir)
            # Windows(.dll) 및 Linux/macOS(.so) 모두 탐색
            candidates = [
                os.path.join(project_dir, "build", "ascii_engine.dll"),      # Windows MSVC/MinGW
                os.path.join(project_dir, "build", "libascii_engine.so"),    # Linux
                os.path.join(project_dir, "build", "libascii_engine.dylib"), # macOS
                os.path.join(src_dir, "ascii_engine.dll"),
                os.path.join(src_dir, "libascii_engine.so"),
            ]
            lib_path = next((path for path in candidates if os.path.exists(path)), candidates[0])
        if not os.path.exists(lib_path):
            return

        try:
            lib = ctypes.CDLL(lib_path)
        except OSError:
            return

        u8_ptr = ctypes.POINTER(ctypes.c_uint8)
        char_ptr = ctypes.POINTER(ctypes.c_char)
        i_ptr = ctypes.POINTER(ctypes.c_int)

        lib.resize_frame_fast.argtypes = [
            u8_ptr, ctypes.c_int, ctypes.c_int,
            u8_ptr, ctypes.c_int, ctypes.c_int,
        ]
        lib.resize_frame_fast.restype = None

        lib.rgb_to_gray_index.argtypes = [
            u8_ptr, ctypes.c_int, ctypes.c_int,
            u8_ptr, ctypes.c_int,
        ]
        lib.rgb_to_gray_index.restype = None

        lib.build_ansi_string.argtypes = [
            u8_ptr, u8_ptr, char_ptr, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
            char_ptr, ctypes.c_int, ctypes.c_int, ctypes.c_int, i_ptr,
        ]
        lib.build_ansi_string.restype = None

        self._lib = lib
        self.has_cpp = True

    @staticmethod
    def _as_u8_ptr(arr: np.ndarray):
        return arr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))

    def resize_frame(self, frame: np.ndarray, dst_h: int, dst_w: int) -> np.ndarray:
        src = np.ascontiguousarray(frame, dtype=np.uint8)
        dst = np.empty((dst_h, dst_w, 3), dtype=np.uint8)
        self._lib.resize_frame_fast(
            self._as_u8_ptr(src), src.shape[0], src.shape[1],
            self._as_u8_ptr(dst), dst_h, dst_w,
        )
        return dst

    def rgb_to_gray_index(self, rgb: np.ndarray, palette_len: int) -> np.ndarray:
        src = np.ascontiguousarray(rgb, dtype=np.uint8)
        out = np.empty(src.shape[:2], dtype=np.uint8)
        self._lib.rgb_to_gray_index(
            self._as_u8_ptr(src), src.shape[0], src.shape[1],
            self._as_u8_ptr(out), palette_len,
        )
        return out

    def build_ansi(
        self,
        rgb: np.ndarray,
        idx: np.ndarray,
        palette: str,
        color_levels: int = 24,
        color_block_width: int = 2,
    ) -> str:
        rgb_c = np.ascontiguousarray(rgb, dtype=np.uint8)
        idx_c = np.ascontiguousarray(idx, dtype=np.uint8)
        h, w = idx_c.shape
        palette_bytes = palette.encode("ascii", errors="replace")
        max_output = h * w * 24 + h * 8 + 1
        out_buf = ctypes.create_string_buffer(max_output)
        out_len = ctypes.c_int(0)
        self._lib.build_ansi_string(
            self._as_u8_ptr(rgb_c),
            self._as_u8_ptr(idx_c),
            ctypes.cast(ctypes.c_char_p(palette_bytes), ctypes.POINTER(ctypes.c_char)),
            len(palette_bytes) - 1,
            h,
            w,
            ctypes.cast(out_buf, ctypes.POINTER(ctypes.c_char)),
            max_output,
            color_levels,
            color_block_width,
            ctypes.byref(out_len),
        )
        return ctypes.string_at(out_buf, out_len.value).decode("ascii", errors="replace")

# ──────────────────────────────────────────────
# 백엔드 자동 감지 (우선순위: C++ > CUDA > MLX > CuPy > CPU)
# ──────────────────────────────────────────────
BACKEND = "cpu"
HAS_CPP_ENGINE = False

# 1. C++ 엔진 (최우선 - 가장 빠름)
try:
    cpp_engine = CppAsciiEngine()
    if cpp_engine.has_cpp:
        BACKEND = "cpp"
        HAS_CPP_ENGINE = True
        print("[✓] C++ 엔진 로드됨 (최고 성능)")
except Exception as e:
    pass

# 2. CUDA (NVIDIA GPU) — Windows/Linux 공통 지원
if BACKEND == "cpu":
    try:
        import torch
        if torch.cuda.is_available():
            BACKEND = "cuda"
            print(f"[✓] CUDA 백엔드 감지: {torch.cuda.get_device_name(0)}")
        elif torch.cuda.device_count() == 0 and platform.system() == "Windows":
            # Windows에서 torch는 설치됐지만 CUDA 드라이버 없는 경우
            pass
    except ImportError:
        pass

# 3. MLX (Apple Silicon)
if BACKEND == "cpu":
    mlx_spec = importlib.util.find_spec("mlx.core")
    mlx_auto_enabled = os.environ.get("VIDEO_ASCII_ENABLE_MLX_AUTO") == "1"
    if mlx_spec is not None and platform.system() == "Darwin" and platform.machine() == "arm64" and mlx_auto_enabled:
        BACKEND = "mlx"
        print("[✓] MLX 백엔드 감지 (Apple Silicon)")

# 4. CuPy (NVIDIA GPU alternative)
if BACKEND == "cpu":
    try:
        import cupy as cp
        BACKEND = "cupy"
        print(f"[✓] CuPy 백엔드 감지")
    except ImportError:
        pass

if BACKEND == "cpu":
    print("[i] CPU 멀티스레드 백엔드 사용")

import numpy as np
import cv2
from concurrent.futures import ThreadPoolExecutor

# ──────────────────────────────────────────────
# Numba JIT 컴파일 (C 수준 성능, 안전한 모드)
# C++ 엔진이 없을 때만 로드 (우선순위: C++ > Numba > NumPy)
# ──────────────────────────────────────────────
HAS_NUMBA = False
if not HAS_CPP_ENGINE:
    try:
        from numba import jit
        HAS_NUMBA = True
        print("[✓] Numba 로드됨 (JIT 5-20배 향상)")
    except ImportError:
        print("[i] Numba 미설치: pip install numba (선택사항, 성능 향상)")
        HAS_NUMBA = False

if not HAS_NUMBA:
    # Dummy 데코레이터
    def jit(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

# ──────────────────────────────────────────────
# ASCII 문자 팔레트
# ──────────────────────────────────────────────
ASCII_PALETTES = {
    "standard":  " .:-=+*#%@",
    "detailed":  " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "blocks":    " ░▒▓█",
    "simple":    " -+#@",
    "braille":   " ⠁⠃⠇⠏⠟⠿⣿",
    "minimal":   " .:*#@",
}

# ──────────────────────────────────────────────
# ANSI 컬러 코드
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# ANSI 컬러 캐시 (벡터화 + LRU 제한)
# ──────────────────────────────────────────────
from functools import lru_cache

# LRU 캐시: 최대 8192개 색상만 메모리 유지 (일반적인 비디오는 충분)
@lru_cache(maxsize=8192)
def _rgb_to_ansi_true_cached(r: int, g: int, b: int) -> str:
    """24비트 트루컬러 ANSI 이스케이프 코드 (안전한 범위 검증)"""
    # 범위 强制: 0-255
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"\x1b[38;2;{r};{g};{b}m"

@lru_cache(maxsize=8192)
def _rgb_to_ansi_256_cached(r: int, g: int, b: int) -> str:
    """256색 팔레트 ANSI 이스케이프 코드 (정확한 양자화)"""
    # 범위 강제: 0-255
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    
    # 그레이스케일 감지 (R ≈ G ≈ B)
    if abs(r - g) <= 10 and abs(g - b) <= 10:
        # 그레이스케일: 232-255 패널 사용 (24단계 그레이)
        gray = (r + g + b) // 3
        if gray < 48:
            idx = 16  # 검정
        elif gray > 207:
            idx = 231  # 흰색
        else:
            # 24단계 정량화
            idx = 232 + (gray - 48) * 24 // (207 - 48)
            idx = max(232, min(255, idx))
    else:
        # 컬러: 6x6x6 큐브 팔레트 (16-231)
        # 각 채널을 0-5로 양자화 (균등 분포)
        r6 = (r * 6 + 127) // 256  # 더 정확한 양자화
        g6 = (g * 6 + 127) // 256
        b6 = (b * 6 + 127) // 256
        # 범위 강제
        r6 = max(0, min(5, r6))
        g6 = max(0, min(5, g6))
        b6 = max(0, min(5, b6))
        idx = 16 + 36 * r6 + 6 * g6 + b6
    
    return f"\x1b[38;5;{idx}m"

@lru_cache(maxsize=512)
def _ansi_256_code_cached(idx: int) -> str:
    idx = max(0, min(255, int(idx)))
    return f"\x1b[38;5;{idx}m"


def _rgb_to_ansi_256_index_array(rgb: np.ndarray) -> np.ndarray:
    """RGB 배열을 256색 ANSI 코드 인덱스로 벡터화 변환."""
    r = rgb[:, :, 2].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 0].astype(np.int16)

    idx = np.empty(rgb.shape[:2], dtype=np.uint16)
    gray_mask = (np.abs(r - g) <= 10) & (np.abs(g - b) <= 10)

    if np.any(gray_mask):
        gray = ((r + g + b) // 3).astype(np.int16)
        gray_idx = np.where(
            gray < 48,
            16,
            np.where(
                gray > 207,
                231,
                232 + ((gray - 48) * 24 // (207 - 48)),
            ),
        )
        idx[gray_mask] = gray_idx[gray_mask].astype(np.uint16)

    color_mask = ~gray_mask
    if np.any(color_mask):
        r6 = np.clip((r * 6 + 127) // 256, 0, 5)
        g6 = np.clip((g * 6 + 127) // 256, 0, 5)
        b6 = np.clip((b * 6 + 127) // 256, 0, 5)
        color_idx = 16 + 36 * r6 + 6 * g6 + b6
        idx[color_mask] = color_idx[color_mask].astype(np.uint16)

    return idx


def _quantize_truecolor_rgb(rgb: np.ndarray, color_levels: int) -> np.ndarray:
    """트루컬러를 적당히 양자화해 ANSI 색상 전환 수를 줄인다."""
    if color_levels >= 256:
        return rgb

    levels = max(2, int(color_levels))
    scale = levels - 1
    rgb16 = rgb.astype(np.uint16)
    buckets = (rgb16 * scale + 127) // 255
    quantized = (buckets * 255 + scale // 2) // scale
    return quantized.astype(np.uint8)


def _apply_color_block_width(rgb: np.ndarray, color_block_width: int) -> np.ndarray:
    """N칸 단위로 같은 색을 공유해 ANSI 색상 전환 횟수를 제한한다."""
    block_w = max(1, int(color_block_width))
    if block_w == 1 or rgb.shape[1] <= 1:
        return rgb

    block_colors = rgb[:, ::block_w, :]
    expanded = np.repeat(block_colors, block_w, axis=1)
    return expanded[:, :rgb.shape[1], :]

def rgb_to_ansi_true(r, g, b, char):
    """24비트 트루컬러 ANSI 이스케이프 (LRU 캐시, 8192 색상 제한)"""
    return _rgb_to_ansi_true_cached(int(r), int(g), int(b)) + char + "\x1b[0m"

def rgb_to_ansi_256(r, g, b, char):
    """256색 ANSI (LRU 캐시, 8192 색상 제한)"""
    return _rgb_to_ansi_256_cached(int(r), int(g), int(b)) + char + "\x1b[0m"

# ──────────────────────────────────────────────
# 고속 인덱스 클립핑 (안전한 Numba JIT)
# ──────────────────────────────────────────────
if HAS_NUMBA:
    # Numba 안전 모드 (nopython=False): Python과 호환 유지
    @jit(nopython=False, cache=True)
    def _clip_indices_fast(idx, pal_len):
        """빠른 인덱스 범위 클립핑 (제자리 수정)"""
        h, w = idx.shape
        for y in range(h):
            for x in range(w):
                val = idx[y, x]
                if val < 0:
                    idx[y, x] = 0
                elif val > pal_len:
                    idx[y, x] = pal_len
        return idx
else:
    # Fallback: NumPy 사용
    def _clip_indices_fast(idx, pal_len):
        return np.clip(idx, 0, pal_len)

# ──────────────────────────────────────────────
# 벡터화된 ANSI 생성 (배열 범위 전용)
# ──────────────────────────────────────────────
def build_ansi_lines_vec(rgb: np.ndarray, idx: np.ndarray, palette: str,
                         invert: bool = False, use_true_color: bool = True,
                         color_levels: int = 24,
                         color_block_width: int = 2) -> str:
    """고속: RGB/인덱스 배열 → ANSI 라인 직접 생성

    [최적화] 픽셀마다 개별 ansi_code + char + reset 조합 제거
      - 같은 색상이 연속될 때 escape 코드 생략 (행 내 색상 변경 시만 출력)
      - 팔레트 문자: np 룩업테이블로 벡터 인덱싱
      - reset은 행 끝에 한 번만
    """
    h, w = idx.shape
    pal_len = len(palette) - 1

    idx = np.clip(idx, 0, pal_len).astype(np.uint8)
    if invert:
        idx = (pal_len - idx).astype(np.uint8)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    rgb = _apply_color_block_width(rgb, color_block_width)
    if use_true_color:
        rgb = _quantize_truecolor_rgb(rgb, color_levels)

    # 팔레트 룩업: uint8 바이트 배열 → 벡터 인덱싱
    pal_bytes = palette.encode("ascii", errors="replace")
    pal_chars = np.frombuffer(pal_bytes, dtype=np.uint8)

    r_flat = rgb[:, :, 2].ravel()
    g_flat = rgb[:, :, 1].ravel()
    b_flat = rgb[:, :, 0].ravel()
    char_flat = pal_chars[idx.ravel()]

    color_fn = _rgb_to_ansi_true_cached if use_true_color else _ansi_256_code_cached

    # 완전 벡터화: 실제 ANSI 코드가 바뀌는 위치만 escape 코드 삽입
    if use_true_color:
        color_key_2d = (
            (rgb[:, :, 2].astype(np.uint32) << 16)
            | (rgb[:, :, 1].astype(np.uint32) << 8)
            | rgb[:, :, 0].astype(np.uint32)
        )
    else:
        color_key_2d = _rgb_to_ansi_256_index_array(rgb).astype(np.uint32)

    # 행 경계 포함 색상 변화 마스크: 첫 열은 항상 True
    diff = np.ones((h, w), dtype=bool)
    diff[:, 1:] = color_key_2d[:, 1:] != color_key_2d[:, :-1]

    # 2) escape 코드가 필요한 위치를 행별로 그룹화
    change_cols = [[] for _ in range(h)]
    ys, xs = np.where(diff)
    for y_i, x_i in zip(ys.tolist(), xs.tolist()):
        change_cols[y_i].append(x_i)

    # 3) 행별 문자열 조립 (escape 삽입 위치 사이 구간을 슬라이스 일괄 처리)
    char_2d = char_flat.reshape(h, w)
    lines = []
    for y in range(h):
        row_chars = char_2d[y]
        parts = []
        prev_x = 0
        for x_i in change_cols[y]:
            if x_i > prev_x:
                parts.append(row_chars[prev_x:x_i].tobytes().decode("ascii", errors="replace"))
            if use_true_color:
                flat_i = y * w + x_i
                parts.append(color_fn(int(r_flat[flat_i]), int(g_flat[flat_i]), int(b_flat[flat_i])))
            else:
                parts.append(color_fn(int(color_key_2d[y, x_i])))
            prev_x = x_i
        if prev_x < w:
            parts.append(row_chars[prev_x:w].tobytes().decode("ascii", errors="replace"))
        parts.append("\x1b[0m")
        lines.append("".join(parts))

    return "\n".join(lines)

# ──────────────────────────────────────────────
# 터미널 크기 감지 & 자동 해상도
# ──────────────────────────────────────────────
import signal
import shutil

def get_terminal_size() -> tuple[int, int]:
    """(columns, rows) — fallback: 80×24"""
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.columns, size.lines

def calc_auto_dims(frame_h: int, frame_w: int,
                   term_cols: int, term_rows: int,
                   char_aspect: float = 0.55,
                   margin_rows: int = 2) -> tuple[int, int]:
    """
    터미널 크기 안에서 영상 비율을 유지하는 최적 (w, h) 계산.
    margin_rows: FPS 오버레이 등 상태 줄 여유
    """
    max_w = term_cols
    max_h = max(1, term_rows - margin_rows)

    # 너비 기준으로 높이 계산
    h_from_w = max(1, int(max_w * frame_h / frame_w * char_aspect))

    if h_from_w <= max_h:
        return max_w, h_from_w
    else:
        # 높이 기준으로 너비 계산
        w_from_h = max(1, int(max_h * frame_w / frame_h / char_aspect))
        return min(w_from_h, max_w), max_h


# ──────────────────────────────────────────────
# 핵심 변환 엔진
# ──────────────────────────────────────────────
class AsciiConverter:
    def __init__(self, 
                 width: int = None,
                 height: int = None,
                 auto_size: bool = True,
                 palette: str = "detailed",
                 color: bool = True,
                 color_mode: str = "true",
                 color_levels: int = 24,
                 color_block_width: int = 2,
                 invert: bool = False,
                 char_aspect: float = 0.55):
        """
        auto_size=True  : 터미널 크기를 매 프레임마다 감지해 자동 조정
        width/height    : auto_size=False 또는 명시 지정 시 고정값 사용
        char_aspect     : 터미널 문자 가로/세로 비율 보정 (기본 0.55)
        """
        self._fixed_width  = width
        self._fixed_height = height
        self.auto_size     = auto_size and (width is None)
        self.width         = width  or 120
        self.height        = height
        self.palette       = ASCII_PALETTES.get(palette, ASCII_PALETTES["detailed"])
        self.palette_len   = len(self.palette) - 1
        self.color         = color
        self.color_mode    = color_mode
        self.color_levels  = max(2, int(color_levels))
        self.color_block_width = max(1, int(color_block_width))
        self.invert        = invert
        self.char_aspect   = char_aspect
        self._backend      = BACKEND
        self._color_fn     = rgb_to_ansi_true if color_mode == "true" else rgb_to_ansi_256
        # 팔레트를 numpy 배열로 미리 변환 (빠른 인덱싱)
        self._palette_arr  = np.array(list(self.palette), dtype=object)
        # 에지 검출 및 방향성 분석 옵션 (현재 비활성화: 클래식 모드)
        self.edge_mode = False 
        self.canny_low = 50
        self.canny_high = 150

        # 터미널 크기 변경 감지 (SIGWINCH)
        self._term_cols, self._term_rows = get_terminal_size()
        try:
            signal.signal(signal.SIGWINCH, self._on_resize)
        except (AttributeError, OSError):
            pass  # Windows 미지원

    def _on_resize(self, *_):
        """SIGWINCH: 터미널 리사이즈 시 즉시 치수 갱신"""
        self._term_cols, self._term_rows = get_terminal_size()

    def update_dims_for_frame(self, frame_h: int, frame_w: int):
        """프레임 해상도 기반으로 출력 치수 갱신 (auto_size 모드)"""
        if not self.auto_size:
            return
        # 매 프레임 터미널 크기 재확인 (SIGWINCH 없는 환경 대응)
        cols, rows = get_terminal_size()
        if cols != self._term_cols or rows != self._term_rows:
            self._term_cols, self._term_rows = cols, rows
        self.width, self.height = calc_auto_dims(
            frame_h, frame_w, self._term_cols, self._term_rows, self.char_aspect
        )

    def _calc_height(self, frame_h, frame_w):
        if self.height:
            return self.height
        return max(1, int(self.width * frame_h / frame_w * self.char_aspect))

    def get_direction_char(self, angle: float) -> str:
        """각도(0~360)에 따른 방향 문자 반환 (사용자 정의 매핑)"""
        angle = angle % 180
        if (0 <= angle < 22.5) or (157.5 <= angle <= 180):
            return "|" # 0/180도 부근
        elif 67.5 <= angle < 112.5:
            return "-" # 90도 부근
        elif 22.5 <= angle < 67.5:
            return "\\" # 45도 부근 -> \
        else:
            return "/" # 135도 부근 -> /

    def convert_frame(self, frame: np.ndarray) -> str:
        """프레임 → 에지 기반 ANSI ASCII 문자열 (벡터화 최적화)"""
        h, w = frame.shape[:2]
        self.update_dims_for_frame(h, w)
        th = self._calc_height(h, w)
        
        # 1. 기본 리사이징 (컬러 정보 보존)
        small_rgb = cv2.resize(frame, (self.width, th), interpolation=cv2.INTER_LINEAR)
        gray = cv2.cvtColor(small_rgb, cv2.COLOR_BGR2GRAY)
        
        # 2. 에지 및 방향성 분석 (Sobel & Canny)
        if self.edge_mode:
            # Canny 에지 검출
            edges = cv2.Canny(gray, self.canny_low, self.canny_high)
            
            # Sobel 그래디언트 분석
            grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
            _, angles = cv2.cartToPolar(grad_x, grad_y, angleInDegrees=True)
            
            # 3. 벡터화된 문자 매핑 (NumPy 활용)
            # 배경은 명암 팔레트로 미리 채움
            idx = (gray / 255.0 * self.palette_len).astype(np.uint8)
            char_map = np.array(list(self.palette), dtype='U1')
            result_chars = char_map[idx]
            
            # 에지 영역에 방향 문자 덮어쓰기
            edge_mask = edges > 0
            if np.any(edge_mask):
                ang = angles[edge_mask] % 180
                
                # 방향 판별 (사용자 요청 매핑: 45->\, 135->/)
                dir_chars = np.full(ang.shape, "|", dtype='U1') 
                dir_chars[(ang >= 67.5) & (ang < 112.5)] = "-"
                dir_chars[(ang >= 22.5) & (ang < 67.5)] = "\\"
                dir_chars[(ang >= 112.5) & (ang < 157.5)] = "/"
                
                result_chars[edge_mask] = dir_chars
            
            # 4. ANSI 컬러 조립
            return self.arrays_to_ansi_fast(small_rgb, result_chars)
        else:
            # 기존 명암 모드
            rgb, idx = self.frame_to_arrays(frame)
            return self.arrays_to_ansi_fast(rgb, idx)

    # ── CUDA (PyTorch) ──────────────────────────
    def _process_cuda(self, frame: np.ndarray) -> np.ndarray:
        import torch
        h, w = frame.shape[:2]
        th = self._calc_height(h, w)

        # PyTorch로 리사이징
        t = torch.from_numpy(frame).cuda().float()
        t = t.permute(2, 0, 1).unsqueeze(0)  # NCHW
        t = torch.nn.functional.interpolate(
            t, size=(th, self.width), mode='bilinear', align_corners=False)
        t = t.squeeze(0).permute(1, 2, 0)  # HWC
        rgb_small = t.clamp(0, 255).byte().cpu().numpy()

        # [버그수정] idx가 정의되지 않았던 문제 → GPU에서 직접 팔레트 인덱스 계산
        gray = 0.2126 * t[..., 2] + 0.7152 * t[..., 1] + 0.0722 * t[..., 0]
        idx = (gray / 255.0 * self.palette_len).long().clamp(0, self.palette_len)
        idx_np = idx.cpu().numpy().astype(np.int32)
        return rgb_small, idx_np

    # ── MLX (Apple Silicon) ─────────────────────
    def _process_mlx(self, frame: np.ndarray) -> tuple:
        import mlx.core as mx
        h, w = frame.shape[:2]
        th = self._calc_height(h, w)

        # MLX는 현재 bilinear resize를 직접 지원하므로 numpy fallback 최소화
        small = cv2.resize(frame, (self.width, th), interpolation=cv2.INTER_LINEAR)
        arr = mx.array(small.astype(np.float32))
        
        # 범위 보정
        arr = mx.clip(arr, 0, 255)

        r = arr[..., 2]; g = arr[..., 1]; b = arr[..., 0]
        gray = 0.2126 * r + 0.7152 * g + 0.0722 * b
        idx = (gray / 255.0 * self.palette_len).astype(mx.int32)
        idx = mx.clip(idx, 0, self.palette_len)  # 인덱스 범위 보정
        idx_np = np.array(idx)
        return small, idx_np

    # ── CuPy ────────────────────────────────────
    def _process_cupy(self, frame: np.ndarray) -> tuple:
        import cupy as cp
        h, w = frame.shape[:2]
        th = self._calc_height(h, w)

        small = cv2.resize(frame, (self.width, th), interpolation=cv2.INTER_LINEAR)
        gpu = cp.asarray(small, dtype=cp.float32)
        
        # 범위 보정 (부동소수점 오차 대응)
        gpu = cp.clip(gpu, 0, 255)

        gray = 0.2126 * gpu[..., 2] + 0.7152 * gpu[..., 1] + 0.0722 * gpu[..., 0]
        idx = (gray / 255.0 * self.palette_len).astype(cp.int32)
        # cp.clip 결과를 명시적으로 할당
        idx = cp.clip(idx, 0, self.palette_len)
        return small, cp.asnumpy(idx)

    # ── C++ (최고 성능) ─────────────────────────
    def _process_cpp(self, frame: np.ndarray) -> tuple:
        """C++ 엔진 활용 (OpenMP 병렬화)"""
        h, w = frame.shape[:2]
        th = self._calc_height(h, w)

        # C++ 리사이징
        small = cpp_engine.resize_frame(frame, th, self.width)
        
        # C++ 그레이스케일 변환 및 팔레트 인덱싱
        idx = cpp_engine.rgb_to_gray_index(small, self.palette_len)
        
        return small, idx

    # ── CPU (numpy + 멀티스레드) ─────────────────
    def _process_cpu(self, frame: np.ndarray) -> tuple:
        h, w = frame.shape[:2]
        th = self._calc_height(h, w)

        # cv2.resize 대신 과학적인 스킵 샘플링 사용 (더 빠름)
        small = cv2.resize(frame, (self.width, th), interpolation=cv2.INTER_LINEAR)
        
        # 벡터화된 색상 변환 (루프 제거)
        f = small.astype(np.float32)
        r = f[..., 2]
        g = f[..., 1]
        b = f[..., 0]
        
        # 그레이스케일 (벡터 연산)
        gray = 0.2126 * r + 0.7152 * g + 0.0722 * b
        
        # 인덱스 계산 (벡터 연산)
        idx = (gray / 255.0 * self.palette_len).astype(np.int32)
        np.clip(idx, 0, self.palette_len, out=idx)
        
        return small, idx

    # ── 통합 처리 ────────────────────────────────
    def frame_to_arrays(self, frame: np.ndarray) -> tuple:
        """(rgb_small, char_index) 반환"""
        if self._backend == "cpp":
            return self._process_cpp(frame)
        elif self._backend == "cuda":
            return self._process_cuda(frame)
        elif self._backend == "mlx":
            return self._process_mlx(frame)
        elif self._backend == "cupy":
            return self._process_cupy(frame)
        else:
            return self._process_cpu(frame)

    def arrays_to_ansi(self, rgb: np.ndarray, idx: np.ndarray) -> str:
        """(deprecated) numpy 배열 → ANSI 컬러 ASCII 문자열 (arrays_to_ansi_fast 사용 권장)"""
        # 호환성을 위해 fast 버전으로 위임
        return self.arrays_to_ansi_fast(rgb, idx)

    def arrays_to_ansi_fast(self, rgb: np.ndarray, chars_or_idx) -> str:
        """벡터화된 빠른 ANSI 변환 (문자 배열 또는 인덱스 배열 지원)"""
        if isinstance(chars_or_idx, np.ndarray) and chars_or_idx.dtype.kind == 'U':
            # 에지 모드용 문자 직접 배열 처리
            return self._build_ansi_from_chars(rgb, chars_or_idx)
        
        # 기존 인덱스 기반 처리
        idx = chars_or_idx
        if self.color and self._backend == "cpp" and HAS_CPP_ENGINE and not self.invert and self.color_mode == "true":
            return cpp_engine.build_ansi(
                rgb,
                idx,
                self.palette,
                self.color_levels,
                self.color_block_width,
            )

        if not self.color:
            # [최적화] numpy 룩업테이블로 이중 루프 제거
            pal_len = self.palette_len
            idx_c = np.clip(idx, 0, pal_len).astype(np.uint8)
            if self.invert:
                idx_c = (pal_len - idx_c).astype(np.uint8)
            pal_bytes = self.palette.encode("ascii", errors="replace")
            pal_arr = np.frombuffer(pal_bytes, dtype=np.uint8)
            char_arr = pal_arr[idx_c]  # (h, w) uint8
            # 행마다 bytes → str 변환
            return "\n".join(row.tobytes().decode("ascii", errors="replace") for row in char_arr)

        return build_ansi_lines_vec(
            rgb,
            idx,
            self.palette,
            self.invert,
            self.color_mode == "true",
            self.color_levels,
            self.color_block_width,
        )

    def _build_ansi_from_chars(self, rgb: np.ndarray, chars: np.ndarray) -> str:
        """[신규] 직접적인 문자 배열을 사용한 고속 ANSI 생성"""
        h, w = chars.shape
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        rgb = _apply_color_block_width(rgb, self.color_block_width)
        rgb = _quantize_truecolor_rgb(rgb, self.color_levels)
        
        color_fn = _rgb_to_ansi_true_cached if self.color_mode == "true" else _ansi_256_code_cached
        
        # 색상 변화 마스크
        color_key_2d = (
            (rgb[:, :, 2].astype(np.uint32) << 16)
            | (rgb[:, :, 1].astype(np.uint32) << 8)
            | rgb[:, :, 0].astype(np.uint32)
        )
        diff = np.ones((h, w), dtype=bool)
        diff[:, 1:] = color_key_2d[:, 1:] != color_key_2d[:, :-1]
        
        lines = []
        for y in range(h):
            row_chars = chars[y]
            row_keys = color_key_2d[y]
            parts = []
            prev_x = 0
            for x in range(w):
                if diff[y, x]:
                    if x > prev_x:
                        parts.append("".join(row_chars[prev_x:x]))
                    if self.color_mode == "true":
                        parts.append(color_fn(int(rgb[y, x, 2]), int(rgb[y, x, 1]), int(rgb[y, x, 0])))
                    else:
                        parts.append(color_fn(int(row_keys[x])))
                    prev_x = x
            if prev_x < w:
                parts.append("".join(row_chars[prev_x:w]))
            parts.append("\x1b[0m")
            lines.append("".join(parts))
        return "\n".join(lines)


def benchmark_frame_convert(converter: "AsciiConverter", frame: np.ndarray, iterations: int = 30) -> dict:
    """공정한 프레임 변환 벤치마크 — 매 이터레이션마다 LRU 캐시 초기화.

    Returns:
        dict: {"avg_ms": float, "fps": float, "backend": str}
    """
    times = []
    for _ in range(iterations):
        _rgb_to_ansi_true_cached.cache_clear()
        _rgb_to_ansi_256_cached.cache_clear()
        t0 = time.perf_counter()
        converter.convert_frame(frame)
        times.append(time.perf_counter() - t0)

    avg_ms = sum(times) / len(times) * 1000
    return {
        "avg_ms": avg_ms,
        "fps": 1000.0 / avg_ms if avg_ms > 0 else 0,
        "backend": BACKEND,
    }


# ──────────────────────────────────────────────
# HTML 출력 생성기
# ──────────────────────────────────────────────
class HtmlExporter:
    def __init__(self, fps: float, width: int):
        self.fps = fps
        self.width = width
        self.frames = []

    def add_frame(self, ansi_str: str):
        # ANSI → HTML span 변환
        self.frames.append(self._ansi_to_html(ansi_str))

    @staticmethod
    def _ansi_to_html(text: str) -> str:
        import re
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # 24비트 컬러 (미리 컴파일된 정규식 사용)
        def replace_color(m):
            r, g, b, content = m.group(1), m.group(2), m.group(3), m.group(4)
            return f'<span style="color:rgb({r},{g},{b})">{content}</span>'
        # 정규식 매번 컴파일하지 말고 함수 내에 캐시
        if not hasattr(HtmlExporter, '_color_re'):
            HtmlExporter._color_re = re.compile(r'\x1b\[38;2;(\d+);(\d+);(\d+)m(.*?)\x1b\[0m')
            HtmlExporter._ansi_re = re.compile(r'\x1b\[\d+m')
        text = HtmlExporter._color_re.sub(replace_color, text)
        text = HtmlExporter._ansi_re.sub('', text)
        text = text.replace("\n", "<br>")
        return text

    def save(self, path: str):
        frame_ms = int(1000 / self.fps)
        frames_json = "[" + ",".join(f'"{f}"' for f in self.frames) + "]"
        # 간단히 리스트로 직렬화 방지 → 별도 배열 방식
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>ASCII Art Video</title>
<style>
  body {{ background: #000; margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
  #canvas {{ font-family: monospace; font-size: 8px; line-height: 1.2; white-space: pre; letter-spacing: 0.05em; }}
  #controls {{ position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
               display: flex; gap: 12px; }}
  button {{ background: #222; color: #0f0; border: 1px solid #0f0; padding: 8px 20px;
            font-family: monospace; cursor: pointer; font-size: 14px; }}
  button:hover {{ background: #0f0; color: #000; }}
  #info {{ color: #0f0; font-family: monospace; font-size: 12px;
           position: fixed; top: 10px; right: 10px; }}
</style>
</head>
<body>
<div id="info">프레임: <span id="fnum">0</span> / {len(self.frames)} &nbsp; FPS: {self.fps:.1f}</div>
<div id="canvas"></div>
<div id="controls">
  <button onclick="toggle()">▶ / ⏸</button>
  <button onclick="stepFrame(-1)">◀ 이전</button>
  <button onclick="stepFrame(1)">다음 ▶</button>
  <button onclick="seek(0)">↩ 처음</button>
</div>
<script>
const frames = {frames_json};
let cur = 0, playing = true, timer = null;

function show(i) {{
  cur = ((i % frames.length) + frames.length) % frames.length;
  document.getElementById('canvas').innerHTML = frames[cur];
  document.getElementById('fnum').textContent = cur + 1;
}}

function play() {{
  timer = setInterval(() => show(++cur), {frame_ms});
}}

function toggle() {{
  if (playing) {{ clearInterval(timer); playing = false; }}
  else {{ play(); playing = true; }}
}}

function stepFrame(d) {{
  clearInterval(timer); playing = false;
  show(cur + d);
}}

function seek(i) {{
  clearInterval(timer); playing = false;
  show(i);
}}

show(0); play();
</script>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[✓] HTML 저장: {path} ({len(self.frames)} 프레임)")


# ──────────────────────────────────────────────
# 영상 → MP4 저장 (PIL + cv2)
# ──────────────────────────────────────────────
class VideoExporter:
    def __init__(self, path: str, fps: float, char_w: int, char_h: int,
                 font_size: int = 10):
        self.path = path
        self.fps = fps
        self.char_w = char_w
        self.char_h = char_h
        self.font_size = font_size
        self._frames = []

    def add_frame(self, rgb: np.ndarray, idx: np.ndarray, palette: str, invert: bool = False):
        self._frames.append((rgb.copy(), idx.copy(), palette, invert))

    def save(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[!] Pillow 미설치: pip install Pillow")
            return

        fs = self.font_size
        char_px_w = int(fs * 0.6)
        char_px_h = fs + 2
        img_w = self.char_w * char_px_w
        img_h = self.char_h * char_px_h

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(self.path, fourcc, self.fps, (img_w, img_h))

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", fs)
        except Exception:
            font = ImageFont.load_default()

        pal_len = len(self._frames[0][2]) - 1

        # [최적화] 픽셀마다 draw.text 호출 → 행별 일괄 처리
        # 팔레트 바이트 배열 미리 준비
        pal0 = self._frames[0][2]
        pal_bytes0 = pal0.encode("ascii", errors="replace")
        pal_np0 = np.frombuffer(pal_bytes0, dtype=np.uint8)

        for i, (rgb, idx, palette, invert) in enumerate(self._frames):
            img = Image.new("RGB", (img_w, img_h), (0, 0, 0))
            draw = ImageDraw.Draw(img)

            pal_bytes = palette.encode("ascii", errors="replace")
            pal_np = np.frombuffer(pal_bytes, dtype=np.uint8)
            idx_c = np.clip(idx, 0, pal_len).astype(np.uint8)
            if invert:
                idx_c = (pal_len - idx_c).astype(np.uint8)
            char_arr = pal_np[idx_c]   # (char_h, char_w) uint8

            char_h_n, char_w_n = idx_c.shape
            for y in range(char_h_n):
                # 행 전체를 하나의 문자열로 (개별 draw.text 호출 수 = 행 수)
                row_str = char_arr[y].tobytes().decode("ascii", errors="replace")
                # 색상이 다르므로 결국 픽셀별 처리가 필요 — 단, numpy 인덱싱으로 채널 추출
                r_row = rgb[y, :, 2]
                g_row = rgb[y, :, 1]
                b_row = rgb[y, :, 0]
                py = y * char_px_h
                for x in range(char_w_n):
                    px = x * char_px_w
                    draw.text((px, py), row_str[x],
                              fill=(int(r_row[x]), int(g_row[x]), int(b_row[x])), font=font)

            frame_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            writer.write(frame_bgr)
            if (i + 1) % 30 == 0:
                print(f"\r  렌더링 중... {i+1}/{len(self._frames)}", end="", flush=True)

        writer.release()
        print(f"\n[✓] MP4 저장: {self.path}")


# ──────────────────────────────────────────────
# 파이프라인 (프리페치 + 멀티스레드)
# ──────────────────────────────────────────────
class AsciiVideoPipeline:
    def __init__(self, converter: AsciiConverter, prefetch: int = 4, buffer_seconds: float = 1.5):
        self.conv = converter
        # 프리페치: 최소 버퍼 수량 보장
        self.prefetch = max(2, prefetch)
        self.buffer_seconds = max(0.1, float(buffer_seconds))
        self._q: queue.Queue = queue.Queue(maxsize=self.prefetch)
        self._stop = threading.Event()
        self._render_blocked = 0

    def _queue_capacity(self, fps_target: float, webcam: bool) -> int:
        if webcam:
            buffered_frames = max(2, int(max(0.2, min(self.buffer_seconds, 0.5)) * fps_target))
        else:
            buffered_frames = max(2, int(self.buffer_seconds * fps_target))
        return max(self.prefetch, buffered_frames)

    def _reset_queue(self, fps_target: float, webcam: bool):
        self._q = queue.Queue(maxsize=self._queue_capacity(fps_target, webcam))

    def _drain_render_queue(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _producer(self, cap: cv2.VideoCapture, fps_target: float):
        """별도 스레드에서 프레임을 미리 ASCII로 변환해 버퍼링."""
        frame_idx = 0
        try:
            while not self._stop.is_set():
                # [동적 스킵] 재생이 시작되었고 변환 속도가 뒤처지면 입력 단계에서 스킵
                if getattr(self, "t_start", None) is not None:
                    elapsed = time.perf_counter() - self.t_start
                    expected_idx = int(elapsed * fps_target)
                    if frame_idx < expected_idx:
                        skip_count = expected_idx - frame_idx
                        for _ in range(skip_count):
                            if not cap.grab():
                                break
                        frame_idx = expected_idx

                ret, frame = cap.read()
                if not ret:
                    if frame_idx == 0:
                        print("\n[!] 첫 프레임을 읽지 못했습니다. (비디오 스트림 오류)")
                    try:
                        self._q.put(None, timeout=1)
                    except: pass
                    break

                ascii_str = self.conv.convert_frame(frame)
                frame_idx += 1
                out_lines = ascii_str.count("\n") + 1
                payload = (ascii_str, out_lines, self.conv.width, out_lines)

                while not self._stop.is_set():
                    try:
                        if self._q.put(payload, timeout=0.1):
                            break
                    except queue.Full:
                        if getattr(self, "t_start", None) is not None:
                            break
                        continue
                    break
        except Exception as e:
            print(f"\n[!] 렌더링 스레드 오류: {e}")
            try:
                self._q.put(None, timeout=1)
            except: pass

    def _visual_len(self, s):
        """ANSI 코드를 제외한 문자열의 실제 출력 너비 계산 (한글 2칸 고려)"""
        clean = _re.sub(r'\x1b\[[0-9;]*m', '', s)
        length = 0
        for char in clean:
            if ord(char) > 0x7F: # 대략적인 한글/특수문자 판별
                length += 2
            else:
                length += 1
        return length

    def _visual_ljust(self, s, width):
        """실제 출력 너비 기준 ljust 구현"""
        vlen = self._visual_len(s)
        return s + " " * max(0, width - vlen)

    def _set_cursor_visible(self, visible):
        """커서 가시성 제어 (ANSI + Windows API)"""
        if visible:
            sys.stdout.write("\x1b[?25h")
            if platform.system() == "Windows":
                import ctypes
                class CursorInfo(ctypes.Structure):
                    _fields_ = [("size", ctypes.c_int), ("visible", ctypes.c_byte)]
                handle = ctypes.windll.kernel32.GetStdHandle(-11)
                cursor = CursorInfo()
                ctypes.windll.kernel32.GetConsoleCursorInfo(handle, ctypes.byref(cursor))
                cursor.visible = True
                ctypes.windll.kernel32.SetConsoleCursorInfo(handle, ctypes.byref(cursor))
        else:
            sys.stdout.write("\x1b[?25l")
            if platform.system() == "Windows":
                import ctypes
                class CursorInfo(ctypes.Structure):
                    _fields_ = [("size", ctypes.c_int), ("visible", ctypes.c_byte)]
                handle = ctypes.windll.kernel32.GetStdHandle(-11)
                cursor = CursorInfo()
                ctypes.windll.kernel32.GetConsoleCursorInfo(handle, ctypes.byref(cursor))
                cursor.visible = False
                ctypes.windll.kernel32.SetConsoleCursorInfo(handle, ctypes.byref(cursor))
        sys.stdout.flush()

    def run_terminal(self, source, fps_target: float = 30.0,
                     hide_cursor: bool = True, loop: bool = False, 
                     suggestions: list = None, audio_url: str = None):
        """실시간 터미널 출력 (사이드바 상호작용 추가)"""
        if hide_cursor:
            self._set_cursor_visible(False)
            
        webcam = isinstance(source, int)
        frame_delay = 1.0 / fps_target

        # 상호작용 상태
        focus_sidebar = False
        selected_sidebar_idx = 0
        sidebar_len = len(suggestions) if suggestions else 0

        # 키보드 입력 설정 (Non-blocking)
        old_settings = None
        if platform.system() != "Windows":
            import tty, termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)

        def get_key():
            if platform.system() == "Windows":
                import msvcrt
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b'\x00', b'\xe0'): # 특수키
                        ch2 = msvcrt.getch()
                        if ch2 == b'H': return "up"
                        if ch2 == b'P': return "down"
                        if ch2 == b'M': return "right"
                        if ch2 == b'K': return "left"
                    if ch == b'\r': return "enter"
                    if ch == b'\t': return "tab"
                    if ch == b'q': return "quit"
                    return ch.decode('ascii', errors='ignore')
            else:
                import select
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':
                        res = sys.stdin.read(2)
                        if res == '[A': return "up"
                        if res == '[B': return "down"
                        if res == '[C': return "right"
                        if res == '[D': return "left"
                    if ch == '\n': return "enter"
                    if ch == '\t': return "tab"
                    if ch == 'q': return "quit"
                    return ch
            return None

        while True:
            # 윈도우 환경에서 FFmpeg 안정성을 위해 속성 직접 지정
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                # 실패 시 기본 백엔드로 재시도
                cap = cv2.VideoCapture(source)
            
            if not cap.isOpened():
                print(f"[!] 소스를 열 수 없음: {source}")
                return
            
            # 가속 및 지연 방지 설정
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except: pass

            fps_real = cap.get(cv2.CAP_PROP_FPS) or fps_target
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if not webcam else -1

            self._stop.clear()
            self._render_blocked = 0
            self._reset_queue(fps_target, webcam)
            self.t_start = None # 초기화 (재생 전)
            
            t = threading.Thread(target=self._producer, args=(cap, fps_target), daemon=True)
            t.start()

            # ── 프리버퍼링 (웹캠이 아닐 때만) ───────────────────────────────
            if not webcam:
                sys.stdout.write(f"\n[i] 버퍼링 중 ({self.buffer_seconds}초)... ")
                sys.stdout.flush()
                # 버퍼가 50% 이상 찰 때까지 최대 5초 대기
                wait_start = time.perf_counter()
                while self._q.qsize() < self._q.maxsize * 0.5 and (time.perf_counter() - wait_start < 5.0):
                    time.sleep(0.1)
                sys.stdout.write("완료!\n")
                sys.stdout.flush()

            # ── 재생 시작 및 오디오 동기화 ──────────────────────────────────
            # ffplay startup delay 보정 (약 0.15초)
            audio_delay_compensation = 0.15
            self.t_start = time.perf_counter() + audio_delay_compensation
            
            audio_proc = None
            if audio_url:
                if not shutil.which("ffplay"):
                    print("\n[!] 경고: ffplay를 찾을 수 없습니다. 소리를 재생하려면 FFmpeg를 설치해 주세요.")
                    time.sleep(2)
                else:
                    try:
                        cmd = ["ffplay", "-nodisp", "-autoexit", "-vn", "-loglevel", "quiet", audio_url]
                        startupinfo = None
                        if platform.system() == "Windows":
                            startupinfo = subprocess.STARTUPINFO()
                            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            startupinfo.wShowWindow = 0 
                        audio_proc = subprocess.Popen(cmd, startupinfo=startupinfo)
                    except Exception as e:
                        print(f"\n[!] 오디오 재생 오류: {e}")
                        time.sleep(1)

            if hide_cursor:
                sys.stdout.write("\x1b[?25l")

            prev_term_size = get_terminal_size()
            prev_out_lines = 0   

            try:
                frame_count = 0
                fps_display = fps_target
                
                while True:
                    # 0. 키보드 입력 처리 (Non-blocking)
                    key = get_key()
                    if key == "quit":
                        self._stop.set()
                        break
                    elif key in ("tab", "right") and not focus_sidebar and sidebar_len > 0:
                        focus_sidebar = True
                        self._sidebar_cache = None # 레드라이 유도
                    elif key in ("tab", "left") and focus_sidebar:
                        focus_sidebar = False
                        self._sidebar_cache = None
                    elif focus_sidebar:
                        if key == "up":
                            selected_sidebar_idx = (selected_sidebar_idx - 1) % min(6, sidebar_len)
                            self._sidebar_cache = None
                        elif key == "down":
                            selected_sidebar_idx = (selected_sidebar_idx + 1) % min(6, sidebar_len)
                            self._sidebar_cache = None
                        elif key == "enter":
                            # 추천 영상 재생 요청
                            if 0 <= selected_sidebar_idx < sidebar_len:
                                target_vid = suggestions[selected_sidebar_idx]
                                self._stop.set()
                                if audio_proc: audio_proc.terminate()
                                if old_settings: 
                                    import termios
                                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                                # 100 + 인덱스 코드로 종료하여 부모 프로세스에 알림
                                sys.exit(100 + selected_sidebar_idx)
                    # 1. 동기화: 현재 시각에 출력되어야 할 '목표 프레임 번호' 계산
                    now = time.perf_counter()
                    
                    # 아직 재생 시작 전(보정 시간)이면 대기
                    if now < self.t_start:
                        time.sleep(0.01)
                        continue

                    elapsed = now - self.t_start
                    expected_frame_idx = int(elapsed * fps_target)

                    # 2. 소비자 사이드 프레임 스킵 (너무 늦은 프레임 버림)
                    # 단, 너무 많이 버리지 않도록 큐 상황에 따라 조절
                    while frame_count < expected_frame_idx:
                        try:
                            temp = self._q.get_nowait()
                            if temp is None: break 
                            frame_count += 1
                        except queue.Empty:
                            break

                    # 3. 현재 출력할 프레임 가져오기
                    try:
                        # 큐가 비어있으면 아주 잠깐 기다림
                        item = self._q.get(timeout=0.1)
                    except queue.Empty:
                        if not webcam and frame_count >= total and total > 0:
                            break
                        continue

                    if item is None:
                        break

                    # [추가] 영상이 오디오보다 너무 빨리 왔을 경우 대기
                    # 목표 시간보다 현재 시간이 빠르면 그만큼 쉰다
                    target_time = self.t_start + (frame_count / fps_target)
                    wait_before_render = target_time - time.perf_counter()
                    if wait_before_render > 0.005:
                        time.sleep(wait_before_render)

                    ascii_str, out_lines, frame_w, frame_h = item
                    
                    # 4. 터미널 리사이즈 및 출력
                    cur_term_size = get_terminal_size()
                    if cur_term_size != prev_term_size:
                        sys.stdout.write("\x1b[2J\x1b[H")
                        prev_term_size = cur_term_size
                        self._drain_render_queue()

                    # 출력 스트링 조립
                    cols, rows = cur_term_size
                    mode_text = "추천영상 선택" if focus_sidebar else "영상 재생"
                    status = (
                        f" [{mode_text}] | "
                        f"FPS:{fps_display:4.1f} | "
                        f"프레임:{frame_count}" + (f"/{total}" if total > 0 else "") +
                        f" | {frame_w}x{frame_h} | {BACKEND.upper()}"
                    )
                    status = status[:cols-1].ljust(cols-1)
                    
                    # 화면 하단에 상태바 배치
                    max_rows = rows - 2
                    v_lines = ascii_str.splitlines()[:max_rows]
                    
                    # 1. 메인 영상 출력 (줄바꿈 포함)
                    output = ["\x1b[H"]
                    for line in v_lines:
                        # 영상 너비가 터미널보다 좁을 경우 배경을 지우며 출력
                        output.append(line + "\x1b[K\n")
                    
                    # 2. 사이드바 (추천 영상) 출력 - 절대 좌표 사용
                    if (suggestions is not None) and cols > (frame_w + 20):
                        sidebar_w = min(60, cols - frame_w - 6)
                        if sidebar_w > 15:
                            if not hasattr(self, '_sidebar_cache') or self._sidebar_cache is None or self._sidebar_cache_w != sidebar_w:
                                self._sidebar_cache = self._render_sidebar_lines(
                                    suggestions, sidebar_w,
                                    highlight_idx=selected_sidebar_idx if focus_sidebar else -1
                                )
                                self._sidebar_cache_w = sidebar_w
                            
                            s_lines = self._sidebar_cache
                            for i, sl in enumerate(s_lines[:len(v_lines)]):
                                # 영상 옆 공간으로 점프하여 출력
                                output.append(f"\x1b[{i+1};{frame_w+3}H{sl}")

                    # 상태바 추가
                    status_y = len(v_lines) + 1
                    output.append(f"\x1b[{status_y};1H\x1b[32m{status}\x1b[0m\x1b[K")
                    
                    sys.stdout.write("".join(output))
                    sys.stdout.flush()

                    # 5. 다음 프레임까지 대기 (이미 늦었으면 대기 없이 즉시 루프)
                    frame_count += 1
                    next_frame_time = self.t_start + (frame_count * frame_delay)
                    wait_time = next_frame_time - time.perf_counter()
                    
                    if wait_time > 0.002:
                        time.sleep(wait_time)
                    
                    # FPS 계산 (디스플레이용)
                    if frame_count % 10 == 0:
                        fps_display = frame_count / (time.perf_counter() - self.t_start)


            except KeyboardInterrupt:
                print("\n[i] 중단됨")
                self._stop.set()
            finally:
                self._stop.set()
                # ── 안전한 종료 순서 보장 (Race Condition 방지) ──
                # 1. 쓰레드가 종료될 때까지 대기
                if t.is_alive():
                    t.join(timeout=1)
                # 2. 그 다음 비디오 캡처 객체 해제
                cap.release()
                
                if hide_cursor:
                    self._set_cursor_visible(True)
                    sys.stdout.write("\x1b[?25h")
                
                # 3. 오디오 프로세스 종료
                if audio_proc:
                    try:
                        audio_proc.terminate()
                        audio_proc.wait(timeout=1)
                    except: pass
            
            if not loop or webcam:
                break

    def _render_sidebar_lines(self, suggestions, width, highlight_idx=-1):
        """추천 영상 목록을 ASCII 줄 리스트로 변환 (작은 컬러 섬네일 포함)"""
        lines = []
        if not suggestions or width < 30: return lines
        
        # 섬네일 캐시 및 로딩 상태 초기화
        if not hasattr(self, '_thumb_ascii_cache'):
            self._thumb_ascii_cache = {}
            self._thumb_loading_started = set()

        # 아직 로딩을 시작하지 않은 영상이 있다면 스레드 시작
        has_new = any(s.get("id") and s.get("id") not in getattr(self, '_thumb_loading_started', set()) for s in suggestions[:8])
        if has_new:
            threading.Thread(target=self._preload_thumbnails, args=(suggestions,), daemon=True).start()

        # 사이드바 각 라인을 조립하고 너비를 강제로 맞추는 도우미
        def finalize_line(content_with_borders):
            # 내용에서 ANSI 코드를 제외한 실제 시각적 길이를 계산
            vlen = self._visual_len(content_with_borders)
            # 부족한 만큼 공백을 채워 넣음 (오른쪽 테두리 바로 앞에 삽입)
            if vlen < width:
                gap = " " * (width - vlen)
                # 마지막 '|' 앞에 공백 삽입
                if content_with_borders.endswith("|\x1b[0m"):
                    return content_with_borders[:-5] + gap + "|\x1b[0m"
                return content_with_borders + gap
            return content_with_borders

        # 1. 헤더 렌더링
        title_text = "추천 영상"
        border = "\x1b[97m+" + "-" * (width - 2) + "+\x1b[0m"
        lines.append(border)
        header = "\x1b[97;1m| " + self._visual_ljust(title_text, width - 4) + " |\x1b[0m"
        lines.append(finalize_line(header))
        lines.append(border)
        
        # 섬네일 크기 설정
        thumb_w = 12
        thumb_h = 5
        
        for idx, s in enumerate(suggestions[:6]):
            vid_id = s.get("id")
            title = s.get("title", "Untitled")
            # 다양한 필드명에 대응하는 유연한 추출
            channel = s.get("channelTitle") or s.get("author") or s.get("uploader") or "YouTube"
            duration = s.get("duration") or s.get("lengthText") or s.get("length") or ""
            
            # 섬네일 ASCII 가져오기 (없으면 로딩 표시)
            if vid_id in getattr(self, '_thumb_ascii_cache', {}):
                t_ascii = self._thumb_ascii_cache[vid_id]
            else:
                # 고정 폭 로딩 애니메이션
                dots = "." * (1 + (int(time.time() * 2) % 3))
                t_ascii = [f" \x1b[90m[{'로딩중' + dots.ljust(3)}]\x1b[0m "] * thumb_h
            
            # 제목 줄바꿈
            t_clean = _re.sub(r'\x1b\[[0-9;]*m', '', title)
            t_rows = []
            curr = ""; curr_vlen = 0
            max_t_w = width - thumb_w - 8
            for char in t_clean:
                char_vlen = 2 if ord(char) > 0x7F else 1
                if curr_vlen + char_vlen < max_t_w:
                    curr += char; curr_vlen += char_vlen
                else:
                    t_rows.append(curr); curr = char; curr_vlen = char_vlen
            if curr: t_rows.append(curr)
            
            # 1~3행: 섬네일 + 제목
            is_selected = (idx == highlight_idx)
            border_color = "\x1b[92;1m" if is_selected else "\x1b[97m"
            
            for i in range(3):
                row_text = t_rows[i] if i < len(t_rows) else ""
                content = self._visual_ljust(row_text, width - thumb_w - 6)
                lines.append(finalize_line(f"{border_color}| \x1b[0m{t_ascii[i]}  {content} {border_color}|\x1b[0m"))
            
            # 4행: 섬네일 + 채널 & 길이
            c_clean = _re.sub(r'\x1b\[[0-9;]*m', '', channel)
            if duration:
                avail_w = width - thumb_w - 15
                info_text = f"{c_clean[:avail_w]} \x1b[93m[{duration}]\x1b[0m"
            else:
                info_text = c_clean
            c_content = self._visual_ljust(info_text, width - thumb_w - 6)
            lines.append(finalize_line(f"{border_color}| \x1b[0m{t_ascii[3]}  \x1b[36m{c_content}\x1b[0m {border_color}|\x1b[0m"))
            
            # 5행: 섬네일 나머지
            empty_space = self._visual_ljust("", width - thumb_w - 6)
            lines.append(finalize_line(f"{border_color}| \x1b[0m{t_ascii[4]}  {empty_space} {border_color}|\x1b[0m"))
                
            lines.append(finalize_line(f"{border_color}|" + ("=" if is_selected else " ") * (width - 2) + f"|\x1b[0m"))
            
        lines.append(border)
        return lines

    def _preload_thumbnails(self, suggestions):
        """백그라운드에서 추천 영상들의 섬네일을 ASCII로 변환하여 캐싱 (더 강력한 다운로드 로직)"""
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        thumb_w = 12
        thumb_h = 5
        
        if not hasattr(self, '_thumb_loading_started'):
            self._thumb_loading_started = set()

        for s in suggestions[:8]:
            vid_id = s.get("id")
            if not vid_id or vid_id in getattr(self, '_thumb_ascii_cache', {}) or vid_id in self._thumb_loading_started:
                # 이미 캐시에 있거나 현재 로딩 중이면 건너뛰되, 캐시에 진짜 있는지는 다시 확인
                if vid_id in getattr(self, '_thumb_ascii_cache', {}):
                    continue
                # 로딩 중인데 너무 오래 걸리면(예: 30초) 다시 시도할 수 있게 할 수도 있음
                if vid_id in self._thumb_loading_started and vid_id not in self._thumb_ascii_cache:
                    # 일단은 계속 진행
                    pass
                else:
                    continue
            
            self._thumb_loading_started.add(vid_id)
            
            # 여러 품질의 썸네일 URL 후보군 생성
            urls = []
            thumbs = s.get("thumbnails", [])
            if thumbs:
                urls.extend([t.get("url") for t in thumbs if t.get("url")])
            urls.append(f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg")
            urls.append(f"https://i.ytimg.com/vi/{vid_id}/default.jpg")
            
            for url in urls:
                if not url: continue
                try:
                    # SSL 검증 우회 및 타임아웃/헤더 최적화
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
                    })
                    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                        if resp.status != 200: continue
                        data = resp.read()
                        if not data or len(data) < 100: continue
                        
                        img_array = np.asarray(bytearray(data), dtype=np.uint8)
                        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                        if img is None: continue
                        
                        # 사이드바용 12x5 고품질 리사이징
                        small = cv2.resize(img, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
                        ascii_rows = []
                        for y in range(thumb_h):
                            row_ansi = ""
                            for x in range(thumb_w):
                                b, g, r = small[y, x]
                                gray = int(0.299 * r + 0.587 * g + 0.114 * b)
                                char = self.palette[min(self.palette_len, int(gray / 255.0 * self.palette_len))]
                                # 각 픽셀에 True Color 입히기
                                row_ansi += f"\x1b[38;2;{r};{g};{b}m{char}"
                            ascii_rows.append(row_ansi + "\x1b[0m")
                        
                        self._thumb_ascii_cache[vid_id] = ascii_rows
                        break 
                except Exception:
                    continue
            time.sleep(0.1) # 서버 부하 방지 및 안정성

    def run_export_html(self, source, output_path: str):
        """HTML 파일로 내보내기 (병렬 처리 최적화)"""
        cap = cv2.VideoCapture(source)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        exporter = HtmlExporter(fps, self.conv.width)

        print(f"[▶] HTML 변환 시작: {total} 프레임, {fps:.1f} FPS")

        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            futures = []
            count = 0
            
            # 프레임 읽기 + 제출
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                futures.append(pool.submit(self.conv.convert_frame, frame))
                count += 1
                if count % 50 == 0:
                    print(f"\r  변환 중... {count}/{total}", end="", flush=True)

            # 결과 순서대로 수집 (결과 순서 보장)
            print(f"\r  수집 중...                 ", end="", flush=True)
            for i, f in enumerate(futures):
                exporter.add_frame(f.result())
                if (i + 1) % 50 == 0:
                    print(f"\r  수집 중... {i+1}/{count}", end="", flush=True)

        print(f"\r  변환 완료: {count} 프레임         ")
        cap.release()
        exporter.save(output_path)

    def run_export_mp4(self, source, output_path: str, font_size: int = 10):
        """MP4 파일로 내보내기 (Pillow 필요)"""
        cap = cv2.VideoCapture(source)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # 첫 프레임으로 출력 해상도 계산
        ret, first = cap.read()
        if not ret:
            print("[!] 첫 프레임 읽기 실패")
            return
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        h0, w0 = first.shape[:2]
        char_h = self.conv._calc_height(h0, w0)
        exporter = VideoExporter(output_path, fps, self.conv.width, char_h, font_size)

        print(f"[▶] MP4 변환 시작: {total} 프레임, {fps:.1f} FPS")
        count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            rgb_s, idx = self.conv.frame_to_arrays(frame)
            exporter.add_frame(rgb_s, idx, self.conv.palette, self.conv.invert)
            count += 1
            if count % 30 == 0:
                print(f"\r  처리 중... {count}/{total}", end="", flush=True)

        cap.release()
        print(f"\r  처리 완료: {count} 프레임         ")
        exporter.save()


# ──────────────────────────────────────────────
# yt-dlp URL 처리
# ──────────────────────────────────────────────
import re as _re
import tempfile
import shutil

_URL_PATTERN = _re.compile(
    r'^(https?://|www\.)'
    r'(youtube\.com|youtu\.be|twitch\.tv|tiktok\.com|twitter\.com|'
    r'x\.com|instagram\.com|facebook\.com|vimeo\.com|dailymotion\.com|'
    r'nicovideo\.jp|bilibili\.com|reddit\.com|streamable\.com|.*)',
    _re.IGNORECASE
)

def is_url(s: str) -> bool:
    return bool(_URL_PATTERN.match(s.strip()))


class YtDlpSource:
    """yt-dlp를 사용해 URL을 영상 소스(파일 경로 또는 스트림 URL)로 변환"""

    def __init__(self, url: str, quality: int = 1080,
                 keep_file: bool = False, download_dir: str = None,
                 terminal_mode: bool = False,
                 cookies: str = None,
                 cookies_from_browser: str = None):
        self.url = url
        # 터미널 실시간 모드면 네트워크 차단여 화질 자동 성정
        if terminal_mode:
            self.quality = min(quality, 1080)  # 스트리밍은 최대 1080p
        else:
            self.quality = quality
        self.keep_file = keep_file
        self.download_dir = download_dir or tempfile.gettempdir()
        self._tmp_path: str = None
        self.cookies = cookies
        self.cookies_from_browser = cookies_from_browser

        try:
            import yt_dlp
            self._ydl = yt_dlp
        except ImportError:
            print("[!] yt-dlp 미설치: pip install yt-dlp")
            sys.exit(1)

    def _ydl_opts(self, **opts) -> dict:
        opts.setdefault("ignoreconfig", True)
        if self.cookies:
            opts["cookiefile"] = self.cookies
        if self.cookies_from_browser:
            browser, _, profile = self.cookies_from_browser.partition(":")
            opts["cookiesfrombrowser"] = (browser, profile or None, None, None)
        return opts

    # ── 정보 조회 ─────────────────────────────
    def get_info(self) -> dict:
        formats = [
            f"bv*[height<={self.quality}]+ba/b[height<={self.quality}]/bv*+ba/best",
            "best",
            None,
        ]
        last_error = None
        for fmt in formats:
            opts = self._ydl_opts(
                quiet=True,
                no_warnings=True,
                skip_download=True,
                ignore_no_formats_error=True,
            )
            if fmt:
                opts["format"] = fmt
            try:
                with self._ydl.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(self.url, download=False)
                if info:
                    return info
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("yt-dlp가 영상 정보를 반환하지 않았습니다.")

    def print_info(self, info: dict):
        title    = info.get("title", "알 수 없음")
        uploader = info.get("uploader", "알 수 없음")
        duration = info.get("duration", 0)
        mins, secs = divmod(int(duration), 60)
        print(f"\n  제목   : {title}")
        print(f"  업로더 : {uploader}")
        print(f"  길이   : {mins}분 {secs}초\n")

    # ── 직접 스트리밍 URL 획득 (다운로드 없음) ──
    def get_stream_url(self, info: dict = None) -> str:
        """
        yt-dlp가 선택한 best 포맷의 직접 URL 반환.
        OpenCV는 이 URL을 VideoCapture로 바로 열 수 있음.
        """
        if info is None:
            info = self.get_info()

        # 해상도 우선 포맷 선택
        fmts = info.get("formats", [])
        # 비디오 있고 height ≤ quality인 포맷만 추려서 height 내림차순
        # [안정성 패치] H.264(avc1) 코덱을 최우선으로 선택 (VP9/AV1 충돌 방지)
        candidates = [
            f for f in fmts
            if f.get("vcodec", "none") != "none"
            and f.get("height") is not None
            and f.get("height") <= self.quality
            and f.get("url")
        ]
        
        if candidates:
            # 코덱 안정성 가중치 부여 (h264/avc1 > 그 외)
            def codec_priority(f):
                vcodec = f.get("vcodec", "").lower()
                ext = f.get("ext", "").lower()
                p = 0
                if "avc1" in vcodec or "h264" in vcodec: p += 100
                if ext == "mp4": p += 10
                return (p, f.get("height", 0), f.get("tbr", 0))
            
            best = max(candidates, key=codec_priority)
        else:
            # quality보다 큰 것 중 가장 작은 것
            over = [f for f in fmts if f.get("vcodec","none") != "none" and f.get("url")]
            best = min(over, key=lambda f: f.get("height", 9999)) if over else None

        if not best:
            # 진짜 아무것도 없으면 그냥 첫 번째 URL이 있는 포맷이라도 잡음
            any_with_url = [f for f in fmts if f.get("url")]
            if any_with_url:
                best = any_with_url[0]

        if best and best.get("url"):
            h = best.get("height", "?")
            w = best.get("width", "?")
            print(f"[✓] 스트리밍 URL 획득: {w}×{h}  ({best.get('ext','?')})")
            return best["url"]

        # 폴백: yt-dlp 추출 URL 그대로
        if info.get("url"):
            return info["url"]

        # 디버깅용 정보 출력
        print(f"[!] 디버그: 발견된 포맷 수 = {len(fmts)}")
        if fmts:
            print(f"    첫 번째 포맷 예시: {fmts[0].get('format_id')} (vcodec: {fmts[0].get('vcodec')}, url: {'있음' if fmts[0].get('url') else '없음'})")

        raise RuntimeError("재생 가능한 스트림 URL을 찾지 못했습니다. (yt-dlp가 직접 URL을 반환하지 않음)")
    def get_audio_url(self, info: dict = None) -> str:
        """
        오디오 전용 스트림 URL 반환 (ffplay 재생용).
        """
        if info is None:
            info = self.get_info()

        fmts = info.get("formats", [])
        # 오디오만 있고 url이 있는 포맷 중 가장 좋은 것 선택
        audio_fmts = [
            f for f in fmts
            if f.get("vcodec") == "none" and f.get("acodec") != "none" and f.get("url")
        ]
        
        if audio_fmts:
            # m4a(aac) 선호 (ffplay 호환성)
            best_audio = max(audio_fmts, key=lambda f: (f.get("ext") == "m4a", f.get("abr", 0)))
            return best_audio["url"]
        
        # 없으면 비디오 포함 포맷이라도 사용
        return info.get("url")

    # ── 파일 다운로드 (HTML/MP4 내보내기용) ────
    def download(self, info: dict = None) -> str:
        """
        영상을 로컬에 다운로드하고 파일 경로 반환.
        keep_file=False이면 프로그램 종료 시 자동 삭제.
        """
        if info is None:
            info = self.get_info()

        # 파일명 안전 처리
        safe_title = _re.sub(r'[\\/:*?"<>|]', '_', info.get("title", "video"))[:60]
        out_tmpl = os.path.join(self.download_dir, f"{safe_title}.%(ext)s")

        fmt = (
            f"bestvideo[height<={self.quality}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={self.quality}]+bestaudio/"
            f"best[height<={self.quality}]/best"
        )

        opts = self._ydl_opts(
            format=fmt,
            outtmpl=out_tmpl,
            quiet=False,
            no_warnings=True,
            merge_output_format="mp4",
        )

        print(f"[↓] 다운로드 중 ({self.quality}p 이하 최고화질)...")
        with self._ydl.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(self.url, download=True)
            final_path = ydl.prepare_filename(result)
            # 병합 후 확장자가 mp4로 바뀔 수 있으므로 실제 파일 탐색
            stem = os.path.splitext(final_path)[0]
            for ext in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
                candidate = stem + ext
                if os.path.exists(candidate):
                    final_path = candidate
                    break

        self._tmp_path = final_path
        print(f"[✓] 다운로드 완료: {final_path}")
        return final_path

    def cleanup(self):
        """임시 다운로드 파일 삭제"""
        if self._tmp_path and not self.keep_file and os.path.exists(self._tmp_path):
            os.remove(self._tmp_path)
            print(f"[i] 임시 파일 삭제: {self._tmp_path}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cleanup()


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="🎬 Video → ASCII Art Converter (GPU 가속 + yt-dlp)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # YouTube URL 실시간 재생 (720p 스트리밍, 색상)
  python video_ascii.py --url "https://youtu.be/dQw4w9WgXcQ" --width 160 --color

  # URL → HTML 저장
  python video_ascii.py --url "https://youtu.be/xxxxx" --width 200 --output out.html --color

  # URL → MP4 저장 (파일 다운로드 후 렌더링)
  python video_ascii.py --url "https://youtu.be/xxxxx" --width 120 --output out.mp4 --color

  # 화질 지정 (기본 720p)
  python video_ascii.py --url "https://youtu.be/xxxxx" --quality 480 --width 160 --color

  # 다운로드 파일 보존 (재사용)
  python video_ascii.py --url "https://youtu.be/xxxxx" --keep-download --width 160 --color

  # 로컬 파일
  python video_ascii.py --input video.mp4 --width 160 --color

  # 웹캠
  python video_ascii.py --webcam --width 120 --color
        """
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", "-u", metavar="URL",
                     help="YouTube / Twitch / TikTok 등 영상 URL (yt-dlp 지원 사이트)")
    src.add_argument("--input", "-i", metavar="FILE", help="로컬 입력 영상 파일")
    src.add_argument("--webcam", action="store_true", help="웹캠 입력 (기본 카메라)")
    src.add_argument("--webcam-id", type=int, metavar="N", help="웹캠 ID 지정")

    parser.add_argument("--width", "-W", type=int, default=None,
                        help="출력 문자 너비 (기본: 터미널 너비 자동)")
    parser.add_argument("--height", "-H", type=int, default=None,
                        help="출력 문자 높이 (기본: 비율 자동)")
    parser.add_argument("--no-auto-size", action="store_true",
                        help="터미널 크기 자동 조정 비활성화 (--width 고정 사용)")
    parser.add_argument("--output", "-o", metavar="FILE", help="저장 경로 (.html / .mp4 / .txt)")
    parser.add_argument("--quality", "-q", type=int, default=1080,
                        choices=[360, 480, 720, 1080, 1440, 2160],
                        help="URL 다운로드 화질 (기본: 1080p). 터미널 실시간은 네트워크 차단여 자동 선택")
    parser.add_argument("--keep-download", action="store_true",
                        help="URL 다운로드 파일을 종료 후 보존 (기본: 임시 파일 자동 삭제)")
    parser.add_argument("--color", "-c", action="store_true", help="컬러 출력 (ANSI 트루컬러)")
    parser.add_argument("--color-mode", choices=["true", "256"], default="true",
                        help="컬러 모드: true=24비트, 256=256색 (기본: true)")
    parser.add_argument("--color-levels", type=int, default=24,
                        help="트루컬러 양자화 단계 수. 낮을수록 색상 전환이 줄어 더 빠름 (기본: 24)")
    parser.add_argument("--color-block-width", type=int, default=2,
                        help="가로 N칸마다 같은 색을 공유. 높을수록 ANSI 색상 전환이 줄어 더 빠름 (기본: 2)")
    parser.add_argument("--palette", "-p", choices=list(ASCII_PALETTES.keys()),
                        default="detailed", help="ASCII 팔레트 (기본: detailed)")
    parser.add_argument("--invert", action="store_true", help="밝기 반전")
    parser.add_argument("--fps", type=float, default=None, help="재생 FPS 제한 (기본: 원본 FPS)")
    parser.add_argument("--loop", action="store_true", help="반복 재생 (터미널 모드)")
    parser.add_argument("--font-size", type=int, default=10, help="MP4 출력 폰트 크기 (기본: 10)")
    parser.add_argument("--char-aspect", type=float, default=0.55,
                        help="문자 가로/세로 비율 보정 (기본: 0.55)")
    parser.add_argument("--prefetch", type=int, default=4, help="프레임 프리페치 수 (기본: 4)")
    parser.add_argument("--buffer-seconds", type=float, default=1.5,
                        help="터미널 출력 전 ASCII로 선변환해 둘 버퍼 길이(초). 파일 재생 권장 1~5초 (기본: 1.5)")
    parser.add_argument("--backend", choices=["auto", "cuda", "mlx", "cupy", "cpu"],
                        default="auto", help="강제 백엔드 선택")
    parser.add_argument("--suggestions", help="JSON string of suggested videos")
    parser.add_argument("--cookies", metavar="FILE", help="yt-dlp에 전달할 Netscape cookies.txt 파일")
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="yt-dlp가 브라우저 로그인 쿠키를 읽게 함. 예: chrome, safari, firefox, brave:Default",
    )

    args = parser.parse_args()

    # 백엔드 강제 지정
    global BACKEND
    if args.backend != "auto":
        BACKEND = args.backend
        print(f"[i] 백엔드 강제 설정: {BACKEND.upper()}")

    # ── 입력 소스 결정 ─────────────────────────────────────────────
    yt_source: YtDlpSource = None
    source = None
    needs_cleanup = False

    if args.webcam:
        source = 0
    elif args.webcam_id is not None:
        source = args.webcam_id
    elif args.url:
        # 터미널 실시간 모드인지 파일 저장 모드인지 판단
        is_export = args.output and Path(args.output).suffix.lower() in (".html", ".mp4", ".avi", ".mov", ".txt")
        
        yt_source = YtDlpSource(
            url=args.url,
            quality=args.quality,
            keep_file=args.keep_download,
            terminal_mode=not is_export,  # 터미널 모드면 화질 자동 최적화
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
        )
        print(f"\n[yt-dlp] URL 분석 중...")
        try:
            info = yt_source.get_info()
        except Exception as exc:
            print(f"[!] URL 분석 실패: {exc}")
            sys.exit(1)
        yt_source.print_info(info)

        output_ext = Path(args.output).suffix.lower() if args.output else ""
        export_mode = output_ext in (".html", ".mp4", ".avi", ".mov", ".txt")

        if export_mode:
            # 내보내기: 완전한 파일 다운로드 필요
            source = yt_source.download(info)
            needs_cleanup = not args.keep_download
        else:
            # 터미널 실시간: 직접 스트리밍 URL (다운로드 없음)
            source = yt_source.get_stream_url(info)
            print(f"[✓] 스트리밍 모드 (다운로드 없음)")
    else:
        source = args.input
        if not os.path.exists(source):
            print(f"[!] 파일 없음: {source}")
            sys.exit(1)

    # 컨버터 초기화
    auto_size = not args.no_auto_size and args.width is None
    conv = AsciiConverter(
        width=args.width,
        height=args.height,
        auto_size=auto_size,
        palette=args.palette,
        color=args.color,
        color_mode=args.color_mode,
        color_levels=args.color_levels,
        color_block_width=args.color_block_width,
        invert=args.invert,
        char_aspect=args.char_aspect,
    )
    if auto_size:
        cols, rows = get_terminal_size()
        
        # 추천 영상이 있을 경우 사이드바 공간(약 60~70칸)을 미리 확보
        if args.suggestions:
            try:
                import json
                s_list = json.loads(args.suggestions)
                if s_list and cols > 120:
                    conv.width = cols - 72 # 마진 추가
                elif s_list and cols > 90:
                    conv.width = cols - 52 # 마진 추가
                else:
                    conv.width = cols - 2 # 기본 마진
            except:
                conv.width = cols - 2
        else:
            conv.width = cols - 2

        print(f"[✓] 자동 해상도 모드: 터미널 {cols}×{rows} 에 맞춤 (영상 너비: {conv.width})")
    
    # 화질 정보 출력
    if args.url:
        if not (args.output and Path(args.output).suffix.lower() in (".html", ".mp4", ".avi", ".mov", ".txt")):
            print(f"[✓] 터미널 실시간 모드: 화질 {yt_source.quality}p로 자동 최적화 (네트워크 효율)")
        else:
            print(f"[✓] 파일 저장 모드: 최고 화질 {yt_source.quality}p로 다운로드")

    # FPS 결정
    fps_target = args.fps
    if fps_target is None and not (args.webcam or args.webcam_id):
        cap_tmp = cv2.VideoCapture(source)
        fps_target = cap_tmp.get(cv2.CAP_PROP_FPS) or 30.0
        cap_tmp.release()
    fps_target = fps_target or 30.0

    pipeline = AsciiVideoPipeline(conv, prefetch=args.prefetch, buffer_seconds=args.buffer_seconds)

    try:
        # 출력 모드
        if args.output:
            ext = Path(args.output).suffix.lower()
            if ext == ".html":
                pipeline.run_export_html(source, args.output)
            elif ext in (".mp4", ".avi", ".mov"):
                pipeline.run_export_mp4(source, args.output, args.font_size)
            elif ext == ".txt":
                # 첫 프레임만 텍스트 저장
                cap = cv2.VideoCapture(source)
                ret, frame = cap.read()
                cap.release()
                if ret:
                    text = conv.convert_frame(frame)
                    import re
                    clean = re.sub(r'\x1b\[[^m]*m', '', text)
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(clean)
                    print(f"[✓] TXT 저장: {args.output}")
            else:
                print(f"[!] 지원하지 않는 출력 형식: {ext}")
        else:
            # 터미널 실시간 출력
            print(
                f"\n[설정] 너비:{args.width} | 팔레트:{args.palette} | 색상:{args.color} | "
                f"백엔드:{BACKEND.upper()} | 선버퍼:{args.buffer_seconds:.1f}s"
            )
            
            suggestions = []
            if args.suggestions:
                try:
                    import json
                    suggestions = json.loads(args.suggestions)
                except: pass

            audio_url = None
            if args.url:
                try:
                    # 정보(info)는 이미 앞에서 로딩됨
                    audio_url = yt_source.get_audio_url(info)
                except: pass

            print("Ctrl+C 로 종료\n")
            pipeline.run_terminal(source, fps_target, loop=args.loop, suggestions=suggestions, audio_url=audio_url)
    finally:
        # URL 다운로드 임시 파일 정리
        if needs_cleanup and yt_source:
            yt_source.cleanup()


if __name__ == "__main__":
    main()
