# Turbe

YouTube 메타데이터 API 서버와 영상 ASCII 변환기를 함께 담은 프로젝트입니다.

이 저장소에는 두 가지 실행 도구가 있습니다.

- `src/server.js`: YouTube 공개 페이지와 oEmbed를 파싱해 영상 정보와 검색 결과를 JSON으로 반환하는 Node.js API 서버
- `src/video_ascii.py`: YouTube/로컬 파일/웹캠 영상을 터미널, HTML, MP4, TXT ASCII 아트로 변환하는 Python 도구
- `src/youtube_html_ascii.py`: 로컬 API 서버의 데이터를 사용해 YouTube 스타일 화면을 ASCII UI로 렌더링하는 보조 도구

YouTube 공식 Data API 키는 필요하지 않습니다.

## 주요 기능

- YouTube 영상 ID 또는 URL로 제목, 설명, 채널, 썸네일, 업로드일, 길이, 조회수 조회
- YouTube 검색 결과 조회
- YouTube, Twitch, TikTok 등 `yt-dlp`가 지원하는 영상 URL을 ASCII로 실시간 재생
- 로컬 영상 파일 또는 웹캠 입력을 ASCII로 출력
- ASCII 결과를 `.html`, `.mp4`, `.txt`로 저장
- ANSI 트루컬러/256색 출력 지원
- macOS Apple Silicon MLX, Linux NVIDIA CUDA, CuPy, CPU 백엔드 자동 선택
- C++ ASCII 엔진 빌드로 프레임 변환 가속

## 요구 사항

- Node.js 18 이상
- Python 3.9 이상
- `ffmpeg`
- macOS 또는 Linux 권장 (Windows 지원 포함)
- 선택 사항: Homebrew, NVIDIA GPU/CUDA, Apple Silicon MLX

## 설치

### macOS / Linux

Python ASCII 변환기를 쓰려면 설치 스크립트를 실행합니다.

```bash
./scripts/install.sh
```

설치 스크립트는 다음 작업을 수행합니다.

- `venv_ascii` 가상환경 생성
- `opencv-python-headless`, `numpy`, `Pillow`, `yt-dlp`, `numba` 설치
- 환경에 따라 `mlx`, `torch`, `cupy` 설치 시도
- `ffmpeg` 설치 여부 확인
- `src/ascii_engine.cpp`를 `build/libascii_engine.so`로 빌드
- `run_ascii.sh` 실행 래퍼 생성

### Windows

```bat
scripts\install.bat
```

Windows 설치 스크립트는 다음 작업을 수행합니다.

- `venv_ascii` 가상환경 생성
- `opencv-python-headless`, `numpy`, `Pillow`, `yt-dlp`, `numba` 설치
- NVIDIA GPU 감지 시 CUDA 버전에 맞는 `torch`/`cupy` 자동 설치
- `src/ascii_engine.cpp`를 `build/ascii_engine.dll`로 빌드 (MSVC 또는 MinGW)
- `run_ascii.bat` 실행 래퍼 생성

Node API 서버는 별도 패키지 의존성이 없습니다.

## 환경 변수

API 서버는 다음 환경 변수를 사용합니다.

```bash
PORT=3000
HOST=127.0.0.1
```

예시는 `.env.example`에 있습니다. 현재 서버 코드는 `.env` 파일을 자동으로 로드하지 않으므로 셸 환경 변수로 지정해 실행합니다.

## API 서버 실행

```bash
npm start
```

개발 중 파일 변경 감시는 다음 명령을 사용합니다.

```bash
npm run dev
```

포트와 호스트를 바꾸려면 다음처럼 실행합니다.

```bash
PORT=4000 HOST=127.0.0.1 npm start
```

## API 사용법

상태 확인:

```bash
curl http://127.0.0.1:3000/health
```

영상 정보 조회:

```bash
curl "http://127.0.0.1:3000/api/youtube/video?id=dQw4w9WgXcQ"
```

YouTube URL로도 조회할 수 있습니다.

```bash
curl "http://127.0.0.1:3000/api/youtube/video?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

검색:

```bash
curl "http://127.0.0.1:3000/api/youtube/search?q=nodejs&maxResults=5"
```

응답에는 영상 제목, 설명, 채널, 썸네일, 업로드일, 길이, 조회수, watch/embed 링크, oEmbed 정보가 포함됩니다.

## 영상 ASCII 변환기 사용법

### macOS / Linux

설치 후 `run_ascii.sh`를 사용합니다.

YouTube URL 실시간 재생:

```bash
./run_ascii.sh --url "https://youtu.be/dQw4w9WgXcQ" --width 160 --color
```

URL을 HTML로 저장:

```bash
./run_ascii.sh --url "https://youtu.be/dQw4w9WgXcQ" --width 200 --output out.html --color
```

URL을 MP4로 저장:

```bash
./run_ascii.sh --url "https://youtu.be/dQw4w9WgXcQ" --width 120 --output out.mp4 --color
```

로컬 파일 재생:

```bash
./run_ascii.sh --input video.mp4 --width 160 --color
```

웹캠 입력:

```bash
./run_ascii.sh --webcam --width 120 --color
```

### Windows

설치 후 `run_ascii.bat`를 사용합니다.

YouTube URL 실시간 재생:

```bat
run_ascii.bat --url "https://youtu.be/dQw4w9WgXcQ" --width 160 --color
```

URL을 HTML로 저장:

```bat
run_ascii.bat --url "https://youtu.be/dQw4w9WgXcQ" --width 200 --output out.html --color
```

로컬 파일 재생:

```bat
run_ascii.bat --input video.mp4 --width 160 --color
```

웹캠 입력:

```bat
run_ascii.bat --webcam --width 120 --color
```

주요 옵션:

- `--width`, `--height`: 출력 문자 크기 지정
- `--output`: `.html`, `.mp4`, `.avi`, `.mov`, `.txt` 저장
- `--quality`: URL 다운로드 화질 선택. `360`, `480`, `720`, `1080`, `1440`, `2160`
- `--keep-download`: 다운로드한 원본 파일 보존
- `--color`: 컬러 출력 사용
- `--color-mode true|256`: 24비트 트루컬러 또는 256색
- `--palette`: ASCII 문자 팔레트 선택
- `--invert`: 밝기 반전
- `--fps`: 재생 FPS 제한
- `--loop`: 터미널 반복 재생
- `--backend auto|cuda|mlx|cupy|cpu`: 변환 백엔드 선택

전체 옵션은 다음 명령으로 확인합니다.

```bash
./run_ascii.sh --help
```

## YouTube ASCII UI 렌더러

`src/youtube_html_ascii.py`는 API 서버 데이터를 받아 YouTube 스타일 화면을 ASCII로 렌더링합니다.

먼저 API 서버를 실행합니다.

```bash
npm start
```

검색 화면:

```bash
./run_youtube_ascii.sh --search "lofi hip hop" --max-results 9 --color always
```

영상 상세 화면:

```bash
./run_youtube_ascii.sh "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --color always
```

파일 저장:

```bash
./run_youtube_ascii.sh --search "nodejs" --output youtube_ascii.txt
```

API 서버 주소를 바꾼 경우:

```bash
./run_youtube_ascii.sh --api-base "http://127.0.0.1:4000" --search "nodejs"
```

브라우저에 로그인된 YouTube 쿠키로 추천 피드를 가져오려면:

```bash
./run_youtube_ascii --login chrome
./run_youtube_ascii --login safari
./run_youtube_ascii --cookies-from-browser "firefox:default"
```

인자 없이 실행한 뒤 상단 `[login]` 버튼에서 Enter를 누르면 기본 브라우저로 YouTube가 열립니다.
브라우저에서 로그인한 뒤 터미널에서 Enter를 누르면 쿠키를 읽고 `[user]` 상태로 바뀝니다.

Netscape 형식 `cookies.txt` 파일을 직접 지정할 수도 있습니다.

```bash
./run_youtube_ascii --cookies cookies.txt
```

## C++ 엔진만 다시 빌드

### macOS / Linux

```bash
./scripts/build_cpp.sh
```

macOS에서 OpenMP를 사용하려면 Homebrew와 `libomp`가 필요합니다. 스크립트가 가능한 경우 자동으로 설치를 시도합니다.

### Windows

```bat
scripts\build_cpp.bat
```

Windows에서는 다음 컴파일러 중 하나가 필요합니다:

- **Visual Studio 2019/2022** (C++ 빌드 도구 포함) — MSVC, 자동 감지
- **MSYS2 + MinGW-w64** — `pacman -S mingw-w64-x86_64-gcc` 후 `g++.exe`를 PATH에 추가
- **WinLibs** — https://winlibs.com/ 에서 독립 MinGW 다운로드

빌드 결과물: `build\ascii_engine.dll`

## 벤치마크

C++ 엔진 프레임 변환 성능을 확인하려면 다음 명령을 실행합니다.

```bash
source venv_ascii/bin/activate
python tests/bench_perf.py
```

## 프로젝트 구조

```text
.
├── src/
│   ├── server.js              # YouTube JSON API 서버
│   ├── video_ascii.py         # 영상 ASCII 변환기
│   ├── youtube_html_ascii.py  # YouTube 스타일 ASCII UI 렌더러
│   └── ascii_engine.cpp       # C++ 변환 엔진
├── scripts/
│   ├── install.sh             # 자동 설치 스크립트 (macOS/Linux)
│   ├── install.bat            # 자동 설치 스크립트 (Windows)
│   ├── build_cpp.sh           # C++ 엔진 빌드 (macOS/Linux)
│   └── build_cpp.bat          # C++ 엔진 빌드 (Windows)
├── tests/
│   └── bench_perf.py          # 성능 벤치마크
├── run_ascii.sh               # 영상 ASCII 변환기 실행 래퍼 (macOS/Linux)
├── run_ascii.bat              # 영상 ASCII 변환기 실행 래퍼 (Windows)
├── run_youtube_ascii.sh       # ASCII UI 렌더러 실행 래퍼 (macOS/Linux)
├── run_youtube_ascii.bat      # ASCII UI 렌더러 실행 래퍼 (Windows)
├── package.json
└── .env.example
```

## 주의 사항

- YouTube 공식 Data API를 사용하지 않고 공개 웹 페이지의 내장 JSON을 파싱합니다.
- YouTube 페이지 구조가 바뀌면 메타데이터 조회나 검색 파싱이 깨질 수 있습니다.
- 이 프로젝트의 API 서버는 영상 파일을 다운로드하지 않고 공개 메타데이터와 링크만 반환합니다.
- 영상 ASCII 변환기는 `yt-dlp`를 사용하므로 대상 사이트 정책과 네트워크 상태의 영향을 받습니다.
- `tests/test.py`는 범용 테스트가 아니라 별도 MLX VLM 실험 코드에 가깝습니다. 일반 검증은 `tests/bench_perf.py`를 사용하세요.
