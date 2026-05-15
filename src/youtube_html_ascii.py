#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.cookiejar
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import threading
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import termios
    import tty
    import select

def get_input():
    """크로스 플랫폼 키 입력 감지 (non-blocking)"""
    if sys.platform == "win32":
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        # Windows handles arrows as prefix + key
        if ch in (b'\x00', b'\xe0'):
            ch2 = msvcrt.getch()
            if ch2 == b'H': return b'up'
            if ch2 == b'P': return b'down'
            if ch2 == b'K': return b'left'
            if ch2 == b'M': return b'right'
            if ch2 == b'I': return b'pageup'
            if ch2 == b'Q': return b'pagedown'
            if ch2 == b'G': return b'home'
            if ch2 == b'O': return b'end'
        # Windows Enter is \r
        if ch == b'\r': return b'enter'
        return ch
    else:
        # Unix/macOS handles arrows as escape sequences
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        
        try:
            # sys.stdin.read(1) can buffer, so use os.read
            ch = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='ignore')
        except Exception:
            return None

        if ch == '\x1b':
            # Check if it's an escape sequence (arrow) or just Esc key
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch2 = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='ignore')
                if ch2 == '[':
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        ch3 = os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='ignore')
                        if ch3 == 'A': return b'up'
                        if ch3 == 'B': return b'down'
                        if ch3 == 'C': return b'right'
                        if ch3 == 'D': return b'left'
                        if ch3 == 'H': return b'home'
                        if ch3 == 'F': return b'end'
                        if ch3.isdigit():
                            seq = ch3
                            while select.select([sys.stdin], [], [], 0.01)[0]:
                                seq += os.read(sys.stdin.fileno(), 1).decode('utf-8', errors='ignore')
                                if seq.endswith("~"):
                                    break
                            if seq == "5~": return b'pageup'
                            if seq == "6~": return b'pagedown'
                            if seq == "1~": return b'home'
                            if seq == "4~": return b'end'
            return b'esc'
        
        # Unix Enter can be \n (LF) or \r (CR)
        if ch == '\n' or ch == '\r':
            return b'enter'
            
        return ch.encode('utf-8')

try:
    from PIL import Image
except ImportError:
    Image = None


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
ANSI_RE = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")
TEXT_BORDER_CODEPOINTS = {0x7C, 0x2223, 0x2502, 0x2503, 0xFF5C}
TEXT_RIGHT_GUARD = 4


RESET = "\033[0m"
COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "bright_black": "\033[90m",
    "bright_red": "\033[91m",
    "bright_white": "\033[97m",
}
_THUMBNAIL_ASCII_CACHE: dict[tuple[str, int, int, str], list[str]] = {}
REQUEST_COOKIE_JAR: http.cookiejar.CookieJar | None = None


def paint(text: str, color: str) -> str:
    return COLORS[color] + text + RESET


def colorize_ascii(ascii_ui: str) -> str:
    # Pre-compiled combined regex for common YouTube UI elements
    # Using a single regex to match multiple tokens at once for speed
    UI_TOKENS = re.compile(
        r"(\[>\] YouTube|\[YOUTUBE\]|YouTube)|"
        r"(\[play\]|\[ PLAY \])|"
        r"(\[user\]|\[login\])|"
        r"(\[\+\]|\[Upload\]|\[ Like \]|\[ Share \]|\[ Save \]|\[ Subscribe \])|"
        r"(\d[\d,.KM]* views?|\d+:\d+|LIVE|Live|Metadata unavailable)|"
        r"(@[\w_]+)"
    )

    def token_replacer(match):
        if match.group(1): return paint(match.group(1), "bright_red")
        if match.group(2): return paint(match.group(2), "green")
        if match.group(3): return paint(match.group(3), "cyan")
        if match.group(4): return paint(match.group(4), "blue")
        if match.group(5): return paint(match.group(5), "magenta")
        if match.group(6): return paint(match.group(6), "cyan")
        return match.group(0)

    colored_lines = []
    for line in ascii_ui.splitlines():
        if set(line.strip()) <= {"+", "-", "=", "|"}:
            colored_lines.append(paint(line, "bright_black"))
            continue

        if "\033[90m" in line: # bright_black (selection)
            # Still apply some colors inside selection? 
            # For now keep it simple to avoid over-complicating selection logic
            colored_lines.append(line)
            continue
            
        colored_lines.append(UI_TOKENS.sub(token_replacer, line))
    return "\n".join(colored_lines)


def should_use_color(mode: str, output_path: str | None) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return output_path is None and sys.stdout.isatty()


def configure_cookies(cookies_file: str | None = None, cookies_from_browser: str | None = None) -> None:
    global REQUEST_COOKIE_JAR
    if cookies_file:
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(cookies_file, ignore_discard=True, ignore_expires=True)
        REQUEST_COOKIE_JAR = jar
        return

    if cookies_from_browser:
        try:
            import yt_dlp.cookies
        except ImportError as exc:
            raise RuntimeError("yt-dlp is required for --login/--cookies-from-browser") from exc

        browser, _, profile = cookies_from_browser.partition(":")
        REQUEST_COOKIE_JAR = yt_dlp.cookies.extract_cookies_from_browser(
            browser,
            profile=profile or None,
        )


def add_request_cookies(req: urllib.request.Request) -> None:
    if REQUEST_COOKIE_JAR is not None:
        REQUEST_COOKIE_JAR.add_cookie_header(req)


def configure_first_available_browser_cookies() -> str:
    errors: list[str] = []
    for browser in ("chrome", "safari", "firefox", "brave", "edge"):
        try:
            configure_cookies(None, browser)
            return browser
        except Exception as exc:
            errors.append(f"{browser}: {exc}")
    raise RuntimeError("; ".join(errors))


def fetch_html(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    add_request_cookies(req)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    add_request_cookies(req)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def fetch_thumbnail_ascii(url: str, width: int, height: int) -> list[str]:
    if not url or width <= 0 or height <= 0 or Image is None:
        return []
    cache_key = (url, width, height, "color")
    if cache_key in _THUMBNAIL_ASCII_CACHE:
        return _THUMBNAIL_ASCII_CACHE[cache_key]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        add_request_cookies(req)
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            image = Image.open(io.BytesIO(resp.read())).convert("RGB")
        image = image.resize((width, height), Image.Resampling.LANCZOS)

        ramp = " .:-=+*#%@"
        rows = []
        for y in range(height):
            line_parts = []
            for x in range(width):
                r, g, b = image.getpixel((x, y))
                gray = int(0.299 * r + 0.587 * g + 0.114 * b)
                char = ramp[gray * (len(ramp) - 1) // 255]
                line_parts.append(f"\033[38;2;{r};{g};{b}m{char}\033[0m")
            rows.append("".join(line_parts))
    except Exception:
        rows = []
    _THUMBNAIL_ASCII_CACHE[cache_key] = rows
    return rows


def api_url(api_base: str, path: str, params: dict[str, str | int]) -> str:
    base = api_base.rstrip("/")
    query = urllib.parse.urlencode(params)
    return f"{base}{path}?{query}"


def best_thumbnail(thumbnails: list[dict[str, Any]] | None) -> str:
    if not thumbnails:
        return ""
    sorted_thumbnails = sorted(
        (thumb for thumb in thumbnails if isinstance(thumb, dict) and thumb.get("url")),
        key=lambda thumb: int(thumb.get("width") or 0) * int(thumb.get("height") or 0),
        reverse=True,
    )
    if not sorted_thumbnails:
        return ""
    return clean_text(sorted_thumbnails[0].get("url", "")).split("?", 1)[0]


def format_duration(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_view_count(value: Any) -> str:
    try:
        # Handle strings with commas or already formatted strings
        if isinstance(value, str):
            value = "".join(filter(str.isdigit, value))
        count = int(value)
    except (TypeError, ValueError):
        return str(value) if value else ""

    if count >= 100_000_000:
        return f"{count / 100_000_000:.1f}억"
    if count >= 10_000:
        return f"{count / 10_000:.1f}만"
    if count >= 1_000:
        return f"{count / 1_000:.1f}천"
    return str(count)


def normalize_api_video(item: dict[str, Any]) -> dict[str, str]:
    raw_views = item.get("viewCountText", "") or str(item.get("viewCount") or "")
    views = format_view_count(raw_views)
    if "시청" in str(item.get("viewCountText", "")):
        views += "명 시청 중"
    elif views:
        views = "조회수 " + views + "회"
        
    published = clean_text(item.get("publishedText", ""))
    meta = " | ".join(part for part in [views, published] if part)
    return {
        "title": clean_text(item.get("title", "")) or "Untitled",
        "channel": clean_text(item.get("channelTitle", "")) or "YouTube",
        "meta": meta,
        "views": views,
        "length": (
            clean_text(item.get("lengthText", ""))
            or clean_text(item.get("duration", ""))
            or format_duration(item.get("durationSeconds"))
        ),
        "thumbnail": best_thumbnail(item.get("thumbnails")),
        "url": clean_text(item.get("links", {}).get("watch", "")) if isinstance(item.get("links"), dict) else "",
    }


def fetch_api_search(api_base: str, query: str, max_results: int) -> dict[str, Any]:
    payload = fetch_json(
        api_url(api_base, "/api/youtube/search", {"q": query, "maxResults": max_results}),
    )
    videos = [normalize_api_video(item) for item in payload.get("videos", []) if isinstance(item, dict)]
    return {
        "chips": [],
        "videos": videos,
    }


def normalize_renderer_video(item: dict[str, Any]) -> dict[str, str]:
    video_id = clean_text(item.get("videoId", ""))
    view_text = first_text(item.get("viewCountText")) or first_text(item.get("shortViewCountText"))
    published = first_text(item.get("publishedTimeText"))
    length = first_text(item.get("lengthText"))
    overlays = item.get("thumbnailOverlays")
    if not length and isinstance(overlays, list) and overlays:
        length = first_text(overlays[0].get("thumbnailOverlayTimeStatusRenderer", {}).get("text", {}))
    meta = " | ".join(part for part in [view_text, published] if part)
    return {
        "title": first_text(item.get("title")) or first_text(item.get("headline")) or "Untitled",
        "channel": (
            first_text(item.get("ownerText"))
            or first_text(item.get("shortBylineText"))
            or first_text(item.get("longBylineText"))
            or "YouTube"
        ),
        "meta": meta,
        "views": view_text,
        "length": length,
        "thumbnail": best_thumbnail(item.get("thumbnail", {}).get("thumbnails")),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
    }


def first_content(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        direct = first_text(value)
        if direct:
            return direct
        for key in ("content", "text", "title", "label"):
            if key in value:
                direct = first_content(value[key])
                if direct:
                    return direct
    return ""


def first_key_value(node: Any, wanted_key: str) -> Any:
    if isinstance(node, dict):
        if wanted_key in node:
            return node[wanted_key]
        for value in node.values():
            found = first_key_value(value, wanted_key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = first_key_value(item, wanted_key)
            if found is not None:
                return found
    return None


def first_watch_video_id(node: Any) -> str:
    endpoint = first_key_value(node, "watchEndpoint")
    if isinstance(endpoint, dict):
        video_id = clean_text(endpoint.get("videoId", ""))
        if video_id:
            return video_id
    video_id = clean_text(first_key_value(node, "videoId") or "")
    return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else ""


def is_probable_watch_video_id(video_id: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return False
    # Playlist/channel IDs are sometimes embedded near recommendations and can
    # look like 11-character video IDs when blindly scraped from page JSON.
    return not video_id.startswith(("PL", "UU", "LL", "RD"))


def best_thumbnail_from_any(node: Any) -> str:
    thumbnails: list[dict[str, Any]] = []
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("url"), str):
            thumbnails.append(item)
        for key in ("thumbnails", "sources"):
            value = item.get(key)
            if isinstance(value, list):
                thumbnails.extend(thumb for thumb in value if isinstance(thumb, dict) and thumb.get("url"))
    return best_thumbnail(thumbnails)


def lockup_metadata_parts(item: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    rows = first_key_value(item, "metadataRows")
    if not isinstance(rows, list):
        return parts
    for row in rows:
        row_parts = row.get("metadataParts", []) if isinstance(row, dict) else []
        for part in row_parts:
            text = first_content(part.get("text", {}) if isinstance(part, dict) else part)
            if text and text not in parts:
                parts.append(text)
    return parts


def normalize_lockup_video(item: dict[str, Any]) -> dict[str, str]:
    video_id = clean_text(item.get("contentId", "")) or first_watch_video_id(item)
    metadata = item.get("metadata", {})
    lockup_metadata = metadata.get("lockupMetadataViewModel", {}) if isinstance(metadata, dict) else {}
    title = (
        first_content(lockup_metadata.get("title", {}))
        or first_content(item.get("title", {}))
        or (f"YouTube video {video_id}" if video_id else "Untitled")
    )
    parts = lockup_metadata_parts(item)
    channel = parts[0] if parts else "YouTube"
    meta = " | ".join(parts[1:4])
    return {
        "title": title,
        "channel": channel,
        "meta": meta,
        "views": "",
        "length": "",
        "thumbnail": best_thumbnail_from_any(item),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
    }


def collect_html_video_ids(page_html: str, max_results: int) -> list[dict[str, str]]:
    videos: list[dict[str, str]] = []
    seen: set[str] = set()
    watch_patterns = [
        r'(?:/|\\/|\\u002F)watch(?:\?|\\u003F)v(?:=|\\u003D)([A-Za-z0-9_-]{11})',
        r'"watchEndpoint"\s*:\s*\{[^{}]*"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
    ]
    matches = []
    for pattern in watch_patterns:
        matches.extend(re.finditer(pattern, page_html))

    for match in sorted(matches, key=lambda item: item.start()):
        video_id = match.group(1)
        if video_id in seen or not is_probable_watch_video_id(video_id):
            continue
        seen.add(video_id)
        start = max(0, match.start() - 3000)
        end = min(len(page_html), match.end() + 3000)
        context = page_html[start:end]
        title = ""
        for pattern in (
            r'"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
            r'"title"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"',
            r'"content"\s*:\s*"([^"]+)"',
        ):
            title_match = re.search(pattern, context)
            if title_match:
                title = clean_text(title_match.group(1).encode("utf-8").decode("unicode_escape", errors="ignore"))
                break
        videos.append(
            {
                "title": title or f"YouTube video {video_id}",
                "channel": "YouTube",
                "meta": "",
                "views": "",
                "length": "",
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
        if len(videos) >= max_results:
            break
    return videos


def fetch_ytdlp_recommendations(
    max_results: int,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[dict[str, str]]:
    try:
        import yt_dlp
    except ImportError:
        return []

    query = f"ytsearch{max_results}:인기 동영상"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    if cookies_from_browser:
        browser, _, profile = cookies_from_browser.partition(":")
        opts["cookiesfrombrowser"] = (browser, profile or None, None, None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception:
        return []

    videos: list[dict[str, str]] = []
    for entry in info.get("entries", []) if isinstance(info, dict) else []:
        if not isinstance(entry, dict):
            continue
        video_id = clean_text(entry.get("id", ""))
        url = clean_text(entry.get("url", ""))
        if not is_probable_watch_video_id(video_id):
            parsed_id = first_youtube_id_from_url(url)
            video_id = parsed_id if is_probable_watch_video_id(parsed_id) else ""
        if not video_id:
            continue
        videos.append(
            {
                "title": clean_text(entry.get("title", "")) or f"YouTube video {video_id}",
                "channel": clean_text(entry.get("uploader", "") or entry.get("channel", "")) or "YouTube",
                "meta": "",
                "views": "",
                "length": format_duration(entry.get("duration")),
                "thumbnail": clean_text(entry.get("thumbnail", "")) or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    return videos


def first_youtube_id_from_url(url: str) -> str:
    if not url:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "youtu.be":
            return parsed.path.strip("/").split("/", 1)[0]
        if parsed.hostname and parsed.hostname.endswith("youtube.com"):
            return urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    except Exception:
        return ""
    return ""


def write_recommendation_debug(page_html: str, initial_data: Any, reason: str) -> None:
    try:
        debug_dir = Path(__file__).resolve().parents[1] / "bebug"
        debug_dir.mkdir(exist_ok=True)
        (debug_dir / "youtube_recommendations_debug.html").write_text(page_html, encoding="utf-8")
        if initial_data:
            (debug_dir / "youtube_recommendations_initial_data.json").write_text(
                json.dumps(initial_data, ensure_ascii=False, indent=2)[:2_000_000],
                encoding="utf-8",
            )
        key_counts: dict[str, int] = {}
        for item in walk(initial_data):
            for key in item:
                if key.endswith("Renderer") or key.endswith("ViewModel") or key == "watchEndpoint":
                    key_counts[key] = key_counts.get(key, 0) + 1
        summary = {
            "reason": reason,
            "html_bytes": len(page_html),
            "has_initial_data": bool(initial_data),
            "marker_positions": {
                key: page_html.find(key)
                for key in ["ytInitialData", "richItemRenderer", "videoRenderer", "lockupViewModel", "watchEndpoint"]
            },
            "renderer_counts": dict(sorted(key_counts.items(), key=lambda item: item[1], reverse=True)[:50]),
            "video_ids_in_html": re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', page_html)[:50],
        }
        (debug_dir / "youtube_recommendations_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def collect_renderer_videos(node: Any, videos: list[dict[str, str]], seen: set[str]) -> None:
    if not node or not isinstance(node, (dict, list)):
        return

    if isinstance(node, list):
        for child in node:
            collect_renderer_videos(child, videos, seen)
        return

    renderer = (
        node.get("videoRenderer")
        or node.get("gridVideoRenderer")
        or node.get("compactVideoRenderer")
    )
    if not renderer and isinstance(node.get("richItemRenderer"), dict):
        content = node["richItemRenderer"].get("content", {})
        renderer = content.get("videoRenderer") if isinstance(content, dict) else None

    if isinstance(renderer, dict):
        video_id = clean_text(renderer.get("videoId", ""))
        if is_probable_watch_video_id(video_id) and video_id not in seen:
            seen.add(video_id)
            videos.append(normalize_renderer_video(renderer))

    lockup = node.get("lockupViewModel")
    if isinstance(lockup, dict):
        video_id = clean_text(lockup.get("contentId", "")) or first_watch_video_id(lockup)
        if is_probable_watch_video_id(video_id) and video_id not in seen:
            seen.add(video_id)
            videos.append(normalize_lockup_video(lockup))

    for key, value in node.items():
        if key in {"playerResponse", "playerConfig", "responseContext"}:
            continue
        collect_renderer_videos(value, videos, seen)


def fetch_youtube_recommendations(
    max_results: int,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    urls = [
        "https://www.youtube.com/?hl=ko&gl=KR",
        "https://www.youtube.com/feed/trending?hl=ko&gl=KR",
        "https://www.youtube.com/results?search_query=%EC%9D%B8%EA%B8%B0%20%EB%8F%99%EC%98%81%EC%83%81&hl=ko&gl=KR",
        "https://www.youtube.com/results?search_query=popular%20videos&hl=ko&gl=KR",
    ]
    videos: list[dict[str, str]] = []
    seen: set[str] = set()
    last_html = ""
    last_initial_data: Any = None
    for url in urls:
        page_html = fetch_html(url)
        last_html = page_html
        initial_data = extract_json_blob(page_html, "var ytInitialData")
        if not initial_data:
            initial_data = extract_json_blob(page_html, "ytInitialData")
        last_initial_data = initial_data
        collect_renderer_videos(initial_data, videos, seen)
        if not videos:
            videos.extend(collect_html_video_ids(page_html, max_results))
        if videos:
            break
    if not videos:
        videos = fetch_ytdlp_recommendations(max_results, cookies_file, cookies_from_browser)
    if not videos:
        write_recommendation_debug(last_html, last_initial_data, "No YouTube recommendations found in page data.")
        raise ValueError("No YouTube recommendations found in the page data.")
    return {
        "chips": ["All", "Recommended", "Music", "Live", "Gaming"],
        "videos": videos[:max_results],
    }


def fetch_api_video(api_base: str, raw_input: str) -> dict[str, Any]:
    key = "url" if raw_input.startswith(("http://", "https://")) else "id"
    payload = fetch_json(api_url(api_base, "/api/youtube/video", {key: raw_input}))
    video = payload.get("video", {})
    if not isinstance(video, dict):
        return sample_watch_data()
    normalized = normalize_api_video(video)
    return {
        "title": normalized["title"],
        "channel": normalized["channel"],
        "views": normalized["views"],
        "length": normalized["length"],
        "description": clean_text(video.get("description", "")),
        "thumbnail": normalized["thumbnail"] or clean_text(video.get("oembed", {}).get("thumbnailUrl", "")),
        "suggestions": video.get("suggestions", []),
    }


def extract_json_blob(page_html: str, marker: str) -> dict[str, Any]:
    needle = marker + " = "
    start = page_html.find(needle)
    if start < 0:
        return {}

    i = start + len(needle)
    while i < len(page_html) and page_html[i].isspace():
        i += 1
    if i >= len(page_html) or page_html[i] != "{":
        return {}

    depth = 0
    in_string = False
    escaped = False
    begin = i

    while i < len(page_html):
        ch = page_html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(page_html[begin : i + 1])
                    except json.JSONDecodeError:
                        return {}
        i += 1
    return {}


def meta_content(page_html: str, key: str, attr: str = "property") -> str:
    pattern = rf'<meta[^>]+{attr}="{re.escape(key)}"[^>]+content="([^"]*)"'
    match = re.search(pattern, page_html, re.IGNORECASE)
    return html.unescape(match.group(1)).strip() if match else ""


def clean_text(value: str) -> str:
    if not value:
        return ""
    # If it contains ANSI escape codes, don't unescape/sub space to avoid mangling
    if "\033[" in value:
        return value
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_cell_text(value: str) -> str:
    text = clean_text(value)
    text = "".join(" / " if ord(char) in TEXT_BORDER_CODEPOINTS else char for char in text)
    return re.sub(r"\s+", " ", text).strip()


def first_text(run_container: Any) -> str:
    if isinstance(run_container, dict):
        if "simpleText" in run_container:
            return clean_text(run_container.get("simpleText", ""))
        if "runs" in run_container:
            parts = [clean_text(run.get("text", "")) for run in run_container["runs"] if isinstance(run, dict)]
            return clean_text("".join(parts))
    return ""


def walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def parse_watch_page(page_html: str) -> dict[str, Any]:
    initial_data = extract_json_blob(page_html, "var ytInitialData")
    player_response = extract_json_blob(page_html, "var ytInitialPlayerResponse")

    title = (
        first_text(player_response.get("videoDetails", {}).get("title"))
        or meta_content(page_html, "og:title")
        or meta_content(page_html, "title", attr="name")
    )
    if not title:
        title_match = re.search(r"<title>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
        title = clean_text(title_match.group(1)) if title_match else "Untitled YouTube Page"

    details = player_response.get("videoDetails", {})
    channel = clean_text(details.get("author", "")) or meta_content(page_html, "og:site_name") or "YouTube"
    views = clean_text(details.get("viewCount", ""))
    length = clean_text(details.get("lengthSeconds", ""))
    description = clean_text(meta_content(page_html, "og:description") or details.get("shortDescription", ""))

    suggestions: list[dict[str, str]] = []
    seen = set()
    for node in walk(initial_data):
        compact = node.get("compactVideoRenderer")
        if not isinstance(compact, dict):
            continue
        suggestion = {
            "title": first_text(compact.get("title")) or "Untitled",
            "channel": first_text(compact.get("shortBylineText")) or "Unknown channel",
            "meta": " | ".join(
                part
                for part in [
                    first_text(compact.get("viewCountText")),
                    first_text(compact.get("lengthText")),
                    first_text(compact.get("publishedTimeText")),
                ]
                if part
            ),
        }
        key = (suggestion["title"], suggestion["channel"])
        if key not in seen:
            seen.add(key)
            suggestions.append(suggestion)
        if len(suggestions) >= 8:
            break

    return {
        "title": title,
        "channel": channel,
        "views": views,
        "length": length,
        "description": description,
        "suggestions": suggestions,
    }


def get_char_width(char: str) -> int:
    cp = ord(char)
    # Non-spacing marks, Variation selectors, etc.
    if unicodedata.category(char) in ("Mn", "Me", "Cf") or 0xFE00 <= cp <= 0xFE0F:
        return 0
    # East Asian Wide or Fullwidth
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    # Common Emoji ranges & Symbols (Miscellaneous Symbols, Dingbats, etc.)
    if 0x1F300 <= cp <= 0x1F9FF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF:
        return 2
    # Mathematical Alphanumeric Symbols (e.g. 𝐏𝐥𝐚𝐲𝐥𝐢𝐬𝐭)
    # Often rendered as 2-width in CJK terminals due to font substitution
    if 0x1D400 <= cp <= 0x1D7FF:
        return 2
    # Check if it's a known narrow character that might be misclassified
    if cp < 128:
        return 1
    return 1


def get_display_width(text: str) -> int:
    # Strip ANSI codes before calculating width
    clean_text_for_width = ANSI_RE.sub("", text)
    return sum(get_char_width(char) for char in clean_text_for_width)


def visual_crop(text: str, max_width: int) -> str:
    current_width = 0
    result = []
    
    # Process characters, but handle ANSI escape sequences separately
    i = 0
    while i < len(text):
        if text[i : i + 2] == "\033[":
            # Find end of ANSI sequence
            end = text.find("m", i)
            if end != -1:
                result.append(text[i : end + 1])
                i = end + 1
                continue
            elif (end := text.find("K", i)) != -1:
                result.append(text[i : end + 1])
                i = end + 1
                continue
        
        char = text[i]
        w = get_char_width(char)
        if current_width + w > max_width:
            break
        result.append(char)
        current_width += w
        i += 1
        
    # Ensure we don't leave a dangling ANSI sequence and always end with RESET if ANSI was present
    res = "".join(result)
    if "\033[" in text and not res.endswith("\033[0m"):
        res += "\033[0m"
    return res


def box_line(width: int, fill: str = " ") -> str:
    return "|" + (fill * max(0, width - 2)) + "|"


def fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = clean_text(text)
    visible_text = visual_crop(text, width)
    visible_w = get_display_width(visible_text)
    return visible_text + (" " * (width - visible_w))


def fit_left_right(left: str, right: str, width: int) -> str:
    if width <= 0:
        return ""
    left = clean_text(left)
    right = clean_text(right)
    
    right_w = get_display_width(right)
    if right_w >= width:
        return visual_crop(right, width)
    
    left_max_w = width - right_w - 1
    left = visual_crop(left, left_max_w)
    left_w = get_display_width(left)
    
    gap = width - left_w - right_w
    return left + (" " * max(1, gap)) + right


def wrap_lines(text: str, width: int, max_lines: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    
    # Custom wrap for visual width
    lines = []
    current_line = []
    current_w = 0
    
    words = text.split(" ")
    for word in words:
        word_w = get_display_width(word)
        if not current_line:
            if word_w > width:
                # Long word, break it
                temp_word = ""
                temp_w = 0
                for char in word:
                    cw = get_char_width(char)
                    if temp_w + cw > width:
                        lines.append(temp_word)
                        temp_word = char
                        temp_w = cw
                    else:
                        temp_word += char
                        temp_w += cw
                current_line = [temp_word]
                current_w = temp_w
            else:
                current_line = [word]
                current_w = word_w
        else:
            if current_w + 1 + word_w > width:
                lines.append(" ".join(current_line))
                current_line = [word]
                current_w = word_w
            else:
                current_line.append(word)
                current_w += 1 + word_w
                
    if current_line:
        lines.append(" ".join(current_line))

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if width > 3:
            last = lines[-1]
            while get_display_width(last) > width - 3:
                last = last[:-1]
            lines[-1] = last.rstrip() + "..."
    return lines


def sample_home_data() -> dict[str, Any]:
    return {
        "chips": ["All", "Music", "Live", "Gaming", "Korea", "Mixes", "Recently uploaded"],
        "videos": [
            {
                "title": "Building a fast ASCII video renderer",
                "channel": "Terminal Lab",
                "meta": "184K views | 2 days ago",
                "length": "12:48",
            },
            {
                "title": "Lo-fi coding radio - beats to ship bugs to",
                "channel": "Night Compile",
                "meta": "3.1M views | Live",
                "length": "LIVE",
            },
            {
                "title": "The quiet design details behind YouTube",
                "channel": "Interface Notes",
                "meta": "421K views | 1 week ago",
                "length": "09:31",
            },
            {
                "title": "CLI tools that changed my workflow",
                "channel": "Dev Desk",
                "meta": "88K views | 5 days ago",
                "length": "18:02",
            },
            {
                "title": "A tiny terminal browser from scratch",
                "channel": "Low Level Seoul",
                "meta": "64K views | 3 weeks ago",
                "length": "24:17",
            },
            {
                "title": "Korean street food night walk",
                "channel": "City Camera",
                "meta": "2.4M views | 8 months ago",
                "length": "31:45",
            },
            {
                "title": "Terminal setup for focused work",
                "channel": "Shell Notes",
                "meta": "132K views | 6 days ago",
                "length": "14:20",
            },
            {
                "title": "Why text UIs still feel fast",
                "channel": "Latency Lab",
                "meta": "256K views | 2 weeks ago",
                "length": "11:07",
            },
            {
                "title": "Building a tiny recommendation feed",
                "channel": "Backend Bits",
                "meta": "77K views | month ago",
                "length": "22:36",
            },
        ],
    }


def sample_watch_data() -> dict[str, Any]:
    return {
        "title": "YouTube interface rendered as ASCII art",
        "channel": "ASCII Studio",
        "views": "1,204,881 views",
        "length": "10:24",
        "description": (
            "A terminal-first mockup of the familiar video page: top navigation, "
            "video player, actions, channel block, comments, and recommendations."
        ),
        "suggestions": [
            {"title": "How terminals draw boxes", "channel": "Text Mode", "meta": "98K views | 4 days ago"},
            {"title": "Designing dense dashboards", "channel": "UI Systems", "meta": "211K views | 2 weeks ago"},
            {"title": "ASCII art animation tricks", "channel": "Frame by Frame", "meta": "54K views | 1 month ago"},
            {"title": "Python scripts worth keeping", "channel": "Pragmatic Dev", "meta": "703K views | 6 months ago"},
        ],
    }


def border(width: int, char: str = "-", color: str | None = None) -> str:
    b = "+" + char * max(0, width - 2) + "+"
    return paint(b, color) if color else b


def nav_bar(width: int, selected: str = "Home", selected_id: str = "", logged_in: bool = False) -> list[str]:
    logo = " [>] YouTube "
    inner_w = max(1, width - 2)
    account_label = "[user]" if logged_in else "[login]"
    if selected_id == "account":
        account_label = paint(account_label, "bright_black")
    right = "[+] " + account_label
    available_search_w = inner_w - len(logo) - len(right) - 2
    search = ""
    if available_search_w >= 12:
        search_w = min(42, available_search_w)
        search_text = " Search " + "_" * max(2, search_w - 10) + " "
        if selected_id == "search":
            search_text = paint(search_text, "bright_black")
        search = "[" + search_text + "]"
    line = fit_left_right(logo + search, right, inner_w)
    tab_list = ["Home", "Subscriptions", "Library"]
    tab_parts = []
    for i, item in enumerate(tab_list):
        text = item
        if selected_id == f"tab_{i}":
            text = paint(text, "bright_black")
        if item == selected:
            tab_parts.append("[" + text + "]")
        else:
            tab_parts.append(text)
    tabs = "  ".join(tab_parts)
    return [
        border(width, "="),
        "|" + line + "|",
        "|" + fit(" " + tabs, width - 2) + "|",
        border(width, "="),
    ]


def render_ascii_thumbnail(
    width: int,
    height: int,
    label: str,
    thumbnail_url: str = "",
    selected: bool = False,
) -> list[str]:
    b_color = "bright_black" if selected else None
    lines = [border(width, color=b_color)]
    inner_w = max(1, width - 2)
    label = clean_text(label)
    if selected:
        label = paint(label, "bright_black")
    if label:
        label = label[: max(1, inner_w - 2)]
    overlay: dict[int, str] = {}
    if thumbnail_url:
        thumbnail_rows = fetch_thumbnail_ascii(thumbnail_url, inner_w, max(1, height - 2))
        if thumbnail_rows:
            for offset, thumbnail_line in enumerate(thumbnail_rows):
                overlay[1 + offset] = thumbnail_line
        else:
            overlay[1] = "[thumbnail]"
            overlay[2] = thumbnail_url
    for row in range(1, height - 1):
        side = paint("|", "bright_black") if selected else "|"
        if row in overlay:
            content = overlay[row]
            lines.append(side + fit(content, inner_w) + side)
        elif label and row == height - 2:
            lines.append(side + fit(label.rjust(inner_w), inner_w) + side)
        else:
            lines.append(side + (" " * inner_w) + side)
    lines.append(border(width, color=b_color))
    return lines


def render_video_card(item: dict[str, str], width: int, index: int, thumb_height: int = 9, selected: bool = False) -> list[str]:
    lines = render_ascii_thumbnail(
        width,
        thumb_height,
        item.get("length", ""),
        item.get("thumbnail", ""),
        selected=selected
    )
    inner_w = max(1, width - 2)
    title = clean_cell_text(item.get("title", "Untitled"))
    channel = clean_cell_text(item.get("channel", "YouTube"))
    meta = clean_cell_text(item.get("views", "") or item.get("meta", ""))
    
    side = paint("|", "bright_black") if selected else "|"
    text_w = max(1, inner_w - TEXT_RIGHT_GUARD)
    channel = visual_crop(channel, text_w)
    meta = visual_crop(meta, text_w)
    title_w = text_w
    title_rows = wrap_lines(title, title_w, 3)
    
    if selected:
        title_rows = [paint(row, "bright_black") for row in title_rows]
        channel = paint(channel, "bright_black")
        meta = paint(meta, "bright_black")
        
    for row in title_rows:
        lines.append(side + fit(row, inner_w) + side)
    for _ in range(max(0, 3 - len(title_rows))):
        lines.append(side + (" " * inner_w) + side)
        
    lines.append(side + fit(channel, inner_w) + side)
    lines.append(side + fit(meta, inner_w) + side)
    lines.append(border(width, color="bright_black" if selected else None))
    return lines


def merge_grid(cards: list[list[str]], columns: int, gap: int = 2) -> str:
    if not cards:
        return ""
    rows = []
    for start in range(0, len(cards), columns):
        chunk = cards[start : start + columns]
        heights = [len(card) for card in chunk]
        max_h = max(heights)
        # Calculate visual widths of each column in the chunk
        widths = [max(get_display_width(line) for line in card) for card in chunk]
        for y in range(max_h):
            parts = []
            for card, card_w in zip(chunk, widths):
                line = card[y] if y < len(card) else ""
                line_w = get_display_width(line)
                parts.append(line + (" " * (card_w - line_w)))
            rows.append((" " * gap).join(parts))
    return "\n".join(rows).rstrip()


def trim_to_height(lines: list[str], height: int, width: int) -> list[str]:
    flattened: list[str] = []
    for line in lines:
        flattened.extend(line.splitlines() or [""])
    if len(flattened) <= height:
        return flattened
    if height <= 1:
        return [fit("...", width)]
    return flattened[: height - 1] + [fit("...", width)]


def render_home_interface(
    data: dict[str, Any] | None = None,
    width: int = 120,
    height: int = 40,
    selected_id: str = "",
    scroll_row: int = 0,
    logged_in: bool = False,
) -> str:
    width = max(60, width)
    height = max(15, height)
    data = data or sample_home_data()
    sidebar_w = 20 if width >= 100 else 0
    content_w = width - sidebar_w - (2 if sidebar_w else 0)

    header = nav_bar(width, "Home", selected_id, logged_in=logged_in)
    content_lines: list[str] = []

    # Force 3x3 grid
    columns = 3
    card_w = (content_w - ((columns - 1) * 2)) // columns
    
    # Dynamic sizing based on terminal height to fill the screen
    header_h = len(header)
    available_h = height - header_h - 1
    
    # We want to show exactly 3 rows if possible
    visible_rows = 3
    
    card_h = available_h // visible_rows
    thumb_h = card_h - 6 # 6 is the extra height for title/meta
    thumb_h = max(4, min(40, thumb_h))
    
    # Recalculate based on clamped thumb_h
    card_h = thumb_h + 6
    max_videos = columns * visible_rows

    start_idx = scroll_row * columns
    end_idx = start_idx + max_videos
    cards = [
        render_video_card(item, card_w, index + start_idx, thumb_h, selected=(selected_id == f"video_{index + start_idx}"))
        for index, item in enumerate(data.get("videos", [])[start_idx:end_idx])
    ]
    grid = merge_grid(cards, columns).splitlines()
    content_lines.extend(grid)

    if not sidebar_w:
        return "\n".join(trim_to_height(header + content_lines, height, width))

    sidebar = [
        border(sidebar_w),
        "|" + fit("  Home", sidebar_w - 2) + "|",
        "|" + fit("  Subscriptions", sidebar_w - 2) + "|",
        "|" + fit("  History", sidebar_w - 2) + "|",
        "|" + fit("  Playlists", sidebar_w - 2) + "|",
        "|" + fit("  Downloads", sidebar_w - 2) + "|",
        border(sidebar_w),
    ]
    if len(sidebar) < len(content_lines):
        sidebar.extend(box_line(sidebar_w) for _ in range(len(content_lines) - len(sidebar)))
    return "\n".join(trim_to_height(header + [merge_columns(sidebar, content_lines)], height, width))


def render_player(
    width: int,
    height: int,
    title: str = "",
    meta: str = "",
    thumbnail_url: str = "",
    length: str = "",
) -> list[str]:
    lines = ["+" + "-" * (width - 2) + "+"]
    inner_w = max(1, width - 2)
    overlay: dict[int, str] = {}
    if thumbnail_url:
        overlay[1] = "[thumbnail]"
        overlay[2] = thumbnail_url
    title_rows = wrap_lines(title, inner_w, 2)
    title_start = max(1, height - 5)
    for offset, title_line in enumerate(title_rows):
        overlay[title_start + offset] = title_line
    if meta:
        overlay[height - 3] = meta
    for row in range(1, height - 1):
        if row in overlay:
            lines.append("|" + fit(overlay[row], inner_w) + "|")
        elif length and row == height - 2:
            lines.append("|" + clean_text(length)[:inner_w].rjust(inner_w) + "|")
        else:
            lines.append(box_line(width))
    lines.append("+" + "-" * (width - 2) + "+")
    return lines


def render_sidebar(suggestions: list[dict[str, str]], width: int) -> list[str]:
    lines = ["+" + "-" * (width - 2) + "+"]
    lines.append("|" + fit(" Up next", width - 2) + "|")
    lines.append("+" + "-" * (width - 2) + "+")

    inner_w = max(10, width - 2)
    for item in suggestions[:9]:
        title_lines = wrap_lines(item["title"], inner_w - 2, 2) or ["Untitled"]
        meta_line = clean_text(item["channel"])
        if item["meta"]:
            meta_line += " | " + item["meta"]
        lines.append("|" + fit("> " + title_lines[0], inner_w) + "|")
        for extra in title_lines[1:]:
            lines.append("|" + fit("  " + extra, inner_w) + "|")
        lines.append("|" + fit("  " + meta_line, inner_w) + "|")
        lines.append("|" + fit("", inner_w) + "|")

    lines.append("+" + "-" * (width - 2) + "+")
    return lines


def merge_columns(left: list[str], right: list[str], gap: int = 2) -> str:
    left_w = max(get_display_width(line) for line in left) if left else 0
    right_w = max(get_display_width(line) for line in right) if right else 0
    rows = max(len(left), len(right))
    merged = []
    for i in range(rows):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        l_w = get_display_width(l)
        r_w = get_display_width(r)
        merged.append(l + (" " * (left_w - l_w)) + (" " * gap) + r)
    return "\n".join(merged)


def resolve_output_size(requested_width: int | None, requested_height: int | None) -> tuple[int, int]:
    terminal = shutil.get_terminal_size(fallback=(120, 40))
    width = requested_width if requested_width is not None else terminal.columns - 1
    height = requested_height if requested_height is not None else terminal.lines - 1
    return max(40, width), max(12, height)


def render_ascii_interface(data: dict[str, Any], width: int = 120, height: int = 40) -> str:
    width = max(40, width)
    height = max(12, height)
    compact_mode = width < 100

    if compact_mode:
        left_w = width
        right_w = 0
    else:
        left_w = max(48, int(width * 0.67))
        right_w = max(28, width - left_w - 2)

    header_left = " [YOUTUBE] "
    header_right = "[user]"
    header_upload = " [Upload]"
    header_search_w = width - 2 - len(header_left) - len(header_upload) - len(header_right) - 2
    if header_search_w >= 12:
        header_left += "[ Search " + "_" * max(2, min(28, header_search_w) - 10) + " ]" + header_upload
    elif len(header_left) + len(header_upload) + len(header_right) + 1 <= width - 2:
        header_left += header_upload

    page_header = [
        "+" + "=" * (width - 2) + "+",
        "|" + fit(" YouTube ASCII Interface Preview", width - 2) + "|",
        "|" + fit_left_right(header_left, header_right, width - 2) + "|",
        "+" + "=" * (width - 2) + "+",
    ]

    player_h = max(10, min(18, height // 2))
    player_meta = " | ".join(part for part in [data.get("views", ""), data.get("length", "")] if part)
    player = render_player(
        left_w,
        player_h,
        data.get("title", ""),
        player_meta,
        data.get("thumbnail", ""),
        data.get("length", ""),
    )
    if compact_mode:
        top = "\n".join(player)
    else:
        suggestions = render_sidebar(data.get("suggestions", []), right_w)
        top = merge_columns(player, suggestions)

    metadata = " | ".join(part for part in [data.get("views", ""), data.get("length", "")] if part) or "Metadata unavailable"
    info_lines = []
    
    title_rows = wrap_lines(data.get("title", "Untitled"), width - 4, 3)
    for row in title_rows:
        info_lines.append("| " + fit(row, width - 4) + " |")
        
    info_lines.append("| " + fit(f"Channel: {data.get('channel', 'YouTube')}", width - 4) + " |")
    info_lines.append("| " + fit(metadata, width - 4) + " |")
    info_lines.append("| " + fit("[ Like ] [ Share ] [ Save ] [ Subscribe ]", width - 4) + " |")
    info_lines.append("|" + (" " * (width - 2)) + "|")
    
    desc_rows = wrap_lines(data.get("description", ""), width - 4, 5)
    for row in desc_rows:
        info_lines.append("| " + fit(row, width - 4) + " |")
        
    info_lines.extend(
        [
            "|" + (" " * (width - 2)) + "|",
            "|" + border(width - 2) + "|",
            "| " + fit(" Comments", width - 4) + " |",
            "|" + border(width - 2) + "|",
            "| " + fit("@viewer  This looks like YouTube escaped into the terminal.", width - 4) + " |",
            "| " + fit("@ascii_fan  The recommendation rail is the best part.", width - 4) + " |",
            "|" + border(width - 2) + "|",
        ]
    )

    if compact_mode and data.get("suggestions"):
        info_lines.extend(
            [
                "",
                "+" + "-" * (width - 2) + "+",
                "|" + fit(" Up next", width - 2) + "|",
                "+" + "-" * (width - 2) + "+",
            ]
        )
        for item in data["suggestions"][:4]:
            info_lines.extend(wrap_lines("> " + item["title"], width - 2, 2))
            meta = clean_text(item["channel"])
            if item.get("meta"):
                meta += " | " + item["meta"]
            info_lines.extend(wrap_lines("  " + meta, width - 2, 1))
            info_lines.append("")

    return "\n".join(trim_to_height(page_header + [top] + info_lines, height, width))


def run_interactive(
    data: dict[str, Any],
    width: int,
    height: int,
    color_mode: str,
    api_base: str = None,
    query: str = None,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
    logged_in: bool = False,
    max_results: int = 9,
):
    # Clear screen and move cursor to home
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    
    selected_id = "video_0"
    scroll_row = 0
    current_max_results = len(data.get("videos", []))
    num_videos = current_max_results
    need_redraw = True
    is_fetching = False

    # Set terminal to cbreak mode on Unix to capture keys immediately
    old_settings = None
    if sys.platform != "win32":
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            pass

    # Pre-calculate layout parameters
    def calc_layout(w, h):
        sidebar_w = 20 if w >= 100 else 0
        content_w = w - sidebar_w - (2 if sidebar_w else 0)
        cols = 3
        hdr_h = 4
        avail_h = h - hdr_h - 1
        vis_rows = 3
        c_h = avail_h // vis_rows
        t_h = max(4, min(40, c_h - 6))
        actual_vis_rows = max(1, min(3, avail_h // (t_h + 6)))
        return sidebar_w, content_w, cols, hdr_h, avail_h, t_h, actual_vis_rows

    sidebar_w, content_w, columns, header_h, available_h, thumb_h, visible_rows = calc_layout(width, height)

    def max_scroll_row() -> int:
        total_rows = math.ceil(len(data.get("videos", [])) / columns) if columns else 0
        return max(0, total_rows - visible_rows)

    def clamp_selection_to_scroll() -> None:
        nonlocal selected_id, scroll_row
        videos = data.get("videos", [])
        if not videos:
            selected_id = "tab_0"
            scroll_row = 0
            return
        scroll_row = max(0, min(scroll_row, max_scroll_row()))
        if not selected_id.startswith("video_"):
            return
        idx = max(0, min(int(selected_id.split("_")[1]), len(videos) - 1))
        top = scroll_row * columns
        bottom = min(len(videos) - 1, top + (columns * visible_rows) - 1)
        if idx < top:
            idx = top
        elif idx > bottom:
            idx = bottom
        selected_id = f"video_{idx}"

    def scroll_page(delta_rows: int) -> None:
        nonlocal selected_id, scroll_row
        videos = data.get("videos", [])
        if not videos:
            return
        scroll_row = max(0, min(scroll_row + delta_rows, max_scroll_row()))
        if not selected_id.startswith("video_"):
            selected_id = f"video_{scroll_row * columns}"
        else:
            idx = int(selected_id.split("_")[1])
            col = idx % columns
            new_idx = min(len(videos) - 1, (scroll_row * columns) + col)
            selected_id = f"video_{new_idx}"
        clamp_selection_to_scroll()
    
    def fetch_more_bg():
        nonlocal is_fetching, current_max_results, num_videos, need_redraw
        try:
            new_max = current_max_results + 18
            new_data = fetch_api_search(api_base, query, min(100, new_max))
            if len(new_data.get("videos", [])) > current_max_results:
                data["videos"] = new_data["videos"]
                current_max_results = len(data["videos"])
                num_videos = current_max_results
                need_redraw = True
        except:
            pass
        finally:
            is_fetching = False

    try:
        while True:
            try:
                if need_redraw:
                    # Move cursor to top
                    sys.stdout.write("\033[H")
                    
                    # Use pre-calculated values
                    ascii_ui = render_home_interface(
                        data,
                        width,
                        height - 1,
                        selected_id=selected_id,
                        scroll_row=scroll_row,
                        logged_in=logged_in,
                    )
                    if should_use_color(color_mode, None):
                        ascii_ui = colorize_ascii(ascii_ui)
                    
                    # Single atomic write to stdout
                    sys.stdout.write("\n" + ascii_ui)
                    sys.stdout.flush()
                    need_redraw = False
                
                # Wait for key
                key = get_input()
                if key is None:
                    time.sleep(0.01)
                    continue
                    
                need_redraw = True
                
                # Use 'q' or 'esc' as exit keys
                if key == b'esc' or key.lower() == b'q':
                    break

                if key in (b'j', b'J'):
                    key = b'down'
                elif key in (b'k', b'K'):
                    key = b'up'
                elif key in (b' ', b'f', b'F'):
                    key = b'pagedown'
                elif key in (b'b', b'B'):
                    key = b'pageup'
                elif key in (b'g',):
                    key = b'home'
                elif key in (b'G',):
                    key = b'end'

                if key == b'pagedown':
                    scroll_page(visible_rows)
                    continue
                if key == b'pageup':
                    scroll_page(-visible_rows)
                    continue
                if key == b'home':
                    scroll_row = 0
                    if data.get("videos"):
                        selected_id = "video_0"
                    continue
                if key == b'end':
                    scroll_row = max_scroll_row()
                    videos = data.get("videos", [])
                    if videos:
                        selected_id = f"video_{len(videos) - 1}"
                        clamp_selection_to_scroll()
                    continue
                
                # Handle arrows
                is_up = (key == b'up')
                is_down = (key == b'down')
                is_left = (key == b'left')
                is_right = (key == b'right')

                if is_up or is_down or is_left or is_right:
                    if selected_id.startswith("video_"):
                        idx = int(selected_id.split("_")[1])
                        num_videos = len(data.get("videos", []))
                        
                        if is_down: # Down
                            if idx + columns < num_videos:
                                new_idx = idx + columns
                                selected_id = f"video_{new_idx}"
                                if (new_idx // columns) >= scroll_row + visible_rows:
                                    scroll_row += 1
                        elif is_up: # Up
                            if idx >= columns:
                                new_idx = idx - columns
                                selected_id = f"video_{new_idx}"
                                if (new_idx // columns) < scroll_row:
                                    scroll_row = max(0, scroll_row - 1)
                            else:
                                selected_id = "tab_0"
                        elif is_left: # Left
                            if idx > 0:
                                new_idx = idx - 1
                                selected_id = f"video_{new_idx}"
                                if (new_idx // columns) < scroll_row:
                                    scroll_row = max(0, (new_idx // columns))
                        elif is_right: # Right
                            if idx < num_videos - 1:
                                new_idx = idx + 1
                                selected_id = f"video_{new_idx}"
                                if (new_idx // columns) >= scroll_row + visible_rows:
                                    scroll_row = (new_idx // columns) - visible_rows + 1

                        # Proactive Load More in Background
                        if query and api_base and current_max_results < 100 and not is_fetching:
                            if (is_down or is_right) and (idx >= num_videos - 3):
                                is_fetching = True
                                threading.Thread(target=fetch_more_bg, daemon=True).start()
                                
                    elif selected_id.startswith("tab_"):
                        idx = int(selected_id.split("_")[1])
                        if is_left: # Left
                            if idx > 0: selected_id = f"tab_{idx - 1}"
                        elif is_right: # Right
                            if idx < 2: selected_id = f"tab_{idx + 1}" # Max 2 now (Home, Subs, Library)
                        elif is_down: # Down
                            selected_id = "video_0"
                        elif is_up: # Up
                            selected_id = "search"
                            
                    elif selected_id == "search":
                        if is_down: # Down
                            selected_id = "tab_0"
                        elif is_right:
                            selected_id = "account"
                            
                    elif selected_id == "account":
                        if is_left:
                            selected_id = "search"
                        elif is_down:
                            selected_id = "tab_2"
                
                elif key == b'enter': # Enter
                    if selected_id == "account":
                        if not logged_in:
                            if old_settings:
                                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                            sys.stdout.write("\033[?25h\033[2J\033[H")
                            sys.stdout.write("[i] 브라우저에서 YouTube 로그인 페이지를 여는 중...\n")
                            sys.stdout.flush()
                            try:
                                webbrowser.open("https://www.youtube.com/", new=2)
                                input(
                                    "브라우저에서 YouTube 로그인을 완료한 뒤 여기서 Enter를 누르세요. "
                                    "쿠키는 브라우저에서만 읽습니다."
                                )
                                if cookies_file or cookies_from_browser:
                                    cookies_from_browser = cookies_from_browser or "chrome"
                                    print(f"[i] {cookies_from_browser} 로그인 쿠키를 읽는 중...")
                                    configure_cookies(cookies_file, cookies_from_browser)
                                else:
                                    print("[i] 설치된 브라우저에서 로그인 쿠키를 찾는 중...")
                                    cookies_from_browser = configure_first_available_browser_cookies()
                                    print(f"[i] {cookies_from_browser} 쿠키를 사용합니다.")
                                logged_in = REQUEST_COOKIE_JAR is not None
                                if logged_in and not query:
                                    new_data = fetch_youtube_recommendations(max_results, cookies_file, cookies_from_browser)
                                    data["videos"] = new_data.get("videos", data.get("videos", []))
                                    current_max_results = len(data.get("videos", []))
                                    num_videos = current_max_results
                            except Exception as e:
                                print(f"[!] 로그인 쿠키 로드 실패: {e}")
                                time.sleep(2)
                            if old_settings:
                                tty.setcbreak(fd)
                            sys.stdout.write("\033[?25l\033[2J\033[H")
                            sys.stdout.flush()
                    elif selected_id.startswith("video_"):
                        idx = int(selected_id.split("_")[1])
                        videos = data.get("videos", [])
                        if 0 <= idx < len(videos):
                            video = videos[idx]
                            url = video.get("url")
                            if url:
                                if url.startswith("/"):
                                    url = "https://www.youtube.com" + url
                                
                                # Restore terminal for subprocess
                                if old_settings:
                                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                                
                                # Get path to video_ascii.py
                                script_dir = os.path.dirname(os.path.abspath(__file__))
                                player_script = os.path.join(script_dir, "video_ascii.py")
                                
                                # Build command
                                cmd = [sys.executable, player_script, "--url", url]
                                if should_use_color(color_mode, None):
                                    cmd.append("--color")
                                if cookies_file:
                                    cmd.extend(["--cookies", cookies_file])
                                if cookies_from_browser:
                                    cmd.extend(["--cookies-from-browser", cookies_from_browser])
                                
                                # Fetch suggestions
                                suggestions = []
                                if api_base:
                                    sys.stdout.write("\n[i] 추천 영상 정보 가져오는 중...")
                                    sys.stdout.flush()
                                    try:
                                        v_data = fetch_api_video(api_base, url)
                                        suggestions = v_data.get("suggestions", [])
                                    except:
                                        pass
                                
                                if not suggestions and data.get("videos"):
                                    all_vids = data["videos"]
                                    suggestions = [v for i, v in enumerate(all_vids) if i != idx][:8]
                                
                                if suggestions:
                                    cmd.extend(["--suggestions", json.dumps(suggestions)])
                                
                                sys.stdout.write("\033[?25h\033[2J\033[H")
                                sys.stdout.flush()
                                
                                try:
                                    subprocess.run(cmd)
                                except KeyboardInterrupt:
                                    pass
                                except Exception as e:
                                    print(f"\n[error] Failed to start video player: {e}")
                                    time.sleep(2)
                                
                                # Re-set cbreak for UI
                                if old_settings:
                                    tty.setcbreak(fd)
                                sys.stdout.write("\033[?25l\033[2J\033[H")
                                sys.stdout.flush()
            except Exception as e:
                # Log error and exit safely
                if old_settings:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                sys.stdout.write("\033[?25h\033[2J\033[H")
                print(f"[Fatal Error in Loop] {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
    finally:
        # Restore terminal on exit
        if old_settings:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)



def main():
    parser = argparse.ArgumentParser(
        description="Render a YouTube-like interface as ASCII art.",
    )
    parser.add_argument("url", nargs="?", help="Optional YouTube watch URL or video id to fetch through the local API")
    parser.add_argument("--page", choices=["home", "watch"], default="home", help="Mock page to render without URL")
    parser.add_argument("--search", "-s", help="Search query to render with /api/youtube/search")
    parser.add_argument("--api-base", default="http://127.0.0.1:3000", help="YouTube API server base URL")
    parser.add_argument("--max-results", type=int, default=30, help="Maximum videos to load. Default: 30")
    parser.add_argument("--width", type=int, help="ASCII output width. Default: current terminal width")
    parser.add_argument("--height", type=int, help="ASCII output height. Default: current terminal height")
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="ANSI color mode. Default: auto",
    )
    parser.add_argument("--output", "-o", help="Optional output file (.txt)")
    parser.add_argument("--select", help="ID of the component to select (search, video_0, etc.)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    parser.add_argument(
        "--login",
        nargs="?",
        const="chrome",
        metavar="BROWSER[:PROFILE]",
        help="Use YouTube login cookies from a browser. Default browser when omitted: chrome",
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="Read YouTube cookies from a browser, e.g. chrome, safari, firefox, brave:Default",
    )
    parser.add_argument("--cookies", metavar="FILE", help="Read YouTube cookies from a Netscape cookies.txt file")
    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, io.UnsupportedOperation):
            pass

    width, height = resolve_output_size(args.width, args.height)
    cookies_from_browser = args.cookies_from_browser or args.login
    try:
        configure_cookies(args.cookies, cookies_from_browser)
    except Exception as exc:
        print(f"[warning] Could not load YouTube login cookies: {exc}", file=sys.stderr)
    logged_in = REQUEST_COOKIE_JAR is not None
    
    if args.search:
        data = fetch_api_search(args.api_base, args.search, args.max_results)
    elif args.url:
        data = fetch_api_video(args.api_base, args.url)
    elif args.page == "watch":
        data = sample_watch_data()
    else:
        try:
            data = fetch_youtube_recommendations(args.max_results, args.cookies, cookies_from_browser)
        except Exception as exc:
            print(f"[warning] Could not fetch YouTube recommendations: {exc}", file=sys.stderr)
            data = sample_home_data()

    if args.interactive or (not args.output and sys.stdin.isatty()):
        sys.stdout.write("\033[?25l")
        try:
            run_interactive(
                data,
                width,
                height,
                args.color,
                api_base=args.api_base,
                query=args.search,
                cookies_file=args.cookies,
                cookies_from_browser=cookies_from_browser,
                logged_in=logged_in,
                max_results=args.max_results,
            )
        finally:
            # Show cursor and clear screen on exit
            sys.stdout.write("\033[?25h\033[2J\033[H")
            sys.stdout.flush()
    else:
        ascii_ui = render_home_interface(
            data,
            width,
            height,
            selected_id=args.select or "",
            logged_in=logged_in,
        )
        if should_use_color(args.color, args.output):
            ascii_ui = colorize_ascii(ascii_ui)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(ascii_ui)
        else:
            print(ascii_ui)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
