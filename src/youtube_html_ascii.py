#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import io
import json
import re
import shutil
import sys
import textwrap
import urllib.parse
import urllib.request
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
_THUMBNAIL_ASCII_CACHE: dict[tuple[str, int, int], list[str]] = {}


def paint(text: str, color: str) -> str:
    return COLORS[color] + text + RESET


def colorize_ascii(ascii_ui: str) -> str:
    colored_lines = []
    for line in ascii_ui.splitlines():
        if set(line.strip()) <= {"+", "-", "=", "|"}:
            colored_lines.append(paint(line, "bright_black"))
            continue

        line = re.sub(r"(\[>\] YouTube|\[YOUTUBE\]|YouTube)", lambda m: paint(m.group(1), "bright_red"), line)
        line = re.sub(r"(\[play\]|\[ PLAY \])", lambda m: paint(m.group(1), "green"), line)
        line = re.sub(r"(\[user\])", lambda m: paint(m.group(1), "cyan"), line)
        line = re.sub(r"(\[\+\]|\[Upload\]|\[ Like \]|\[ Share \]|\[ Save \]|\[ Subscribe \])", lambda m: paint(m.group(1), "blue"), line)
        line = re.sub(r"(\d[\d,.KM]* views?|\d+:\d+|LIVE|Live|Metadata unavailable)", lambda m: paint(m.group(1), "magenta"), line)
        line = re.sub(r"(@[\w_]+)", lambda m: paint(m.group(1), "cyan"), line)
        colored_lines.append(line)
    return "\n".join(colored_lines)


def should_use_color(mode: str, output_path: str | None) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return output_path is None and sys.stdout.isatty()


def fetch_html(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def fetch_thumbnail_ascii(url: str, width: int, height: int) -> list[str]:
    if not url or width <= 0 or height <= 0:
        return []
    cache_key = (url, width, height)
    if cache_key in _THUMBNAIL_ASCII_CACHE:
        return _THUMBNAIL_ASCII_CACHE[cache_key]
    try:
        from PIL import Image

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            image = Image.open(io.BytesIO(resp.read())).convert("L")
        image = image.resize((width, height))
        ramp = " .:-=+*#%@"
        rows = []
        for y in range(height):
            chars = []
            for x in range(width):
                value = image.getpixel((x, y))
                chars.append(ramp[value * (len(ramp) - 1) // 255])
            rows.append("".join(chars))
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
        count = int(value)
    except (TypeError, ValueError):
        return ""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B views"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M views"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K views"
    return f"{count} views"


def normalize_api_video(item: dict[str, Any]) -> dict[str, str]:
    views = clean_text(item.get("viewCountText", "")) or format_view_count(item.get("viewCount"))
    published = clean_text(item.get("publishedText", ""))
    meta = " | ".join(part for part in [views, published] if part)
    return {
        "title": clean_text(item.get("title", "")) or "Untitled",
        "channel": clean_text(item.get("channelTitle", "")) or "YouTube",
        "meta": meta,
        "views": views,
        "length": clean_text(item.get("lengthText", "")) or format_duration(item.get("durationSeconds")),
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
        "suggestions": [],
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
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


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


def box_line(width: int, fill: str = " ") -> str:
    return "|" + (fill * max(0, width - 2)) + "|"


def fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = clean_text(text)
    return text[:width].ljust(width)


def fit_left_right(left: str, right: str, width: int) -> str:
    if width <= 0:
        return ""
    left = clean_text(left)
    right = clean_text(right)
    if len(right) >= width:
        return right[-width:]
    gap = width - len(left) - len(right)
    if gap < 1:
        left = left[: max(0, width - len(right) - 1)].rstrip()
        gap = width - len(left) - len(right)
    return left + (" " * max(1, gap)) + right


def wrap_lines(text: str, width: int, max_lines: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    lines = textwrap.wrap(text, width=max(1, width), break_long_words=True, break_on_hyphens=False)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if width > 3:
            lines[-1] = lines[-1][: width - 3].rstrip() + "..."
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


def border(width: int, char: str = "-") -> str:
    return "+" + char * max(0, width - 2) + "+"


def nav_bar(width: int, selected: str = "Home") -> list[str]:
    logo = " [>] YouTube "
    inner_w = max(1, width - 2)
    right = "[+] [user]"
    available_search_w = inner_w - len(logo) - len(right) - 2
    search = ""
    if available_search_w >= 12:
        search_w = min(42, available_search_w)
        search = "[ Search " + "_" * max(2, search_w - 10) + " ]"
    line = fit_left_right(logo + search, right, inner_w)
    tabs = "  ".join(
        ("[" + item + "]") if item == selected else item
        for item in ["Home", "Shorts", "Subscriptions", "Library"]
    )
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
    title: str = "",
    meta: str = "",
    thumbnail_url: str = "",
) -> list[str]:
    lines = [border(width)]
    inner_w = max(1, width - 2)
    label = clean_text(label)
    if label:
        label = label[: max(1, inner_w - 2)]
    overlay: dict[int, str] = {}
    if thumbnail_url:
        title_start = max(1, height - 5)
        thumbnail_rows = fetch_thumbnail_ascii(thumbnail_url, inner_w, max(1, title_start - 1))
        if thumbnail_rows:
            for offset, thumbnail_line in enumerate(thumbnail_rows):
                overlay[1 + offset] = thumbnail_line
        else:
            overlay[1] = "[thumbnail]"
            overlay[2] = thumbnail_url
    if title:
        title_rows = wrap_lines(title, inner_w, 2)
        title_start = max(1, height - 5)
        for offset, title_line in enumerate(title_rows):
            overlay[title_start + offset] = title_line
    if meta:
        overlay[height - 3] = meta
    for row in range(1, height - 1):
        if row in overlay:
            lines.append("|" + fit(overlay[row], inner_w) + "|")
        elif label and row == height - 2:
            content = label.rjust(inner_w)
            lines.append("|" + content + "|")
        else:
            lines.append(box_line(width))
    lines.append(border(width))
    return lines


def render_video_card(item: dict[str, str], width: int, index: int, thumb_height: int = 9) -> list[str]:
    lines = render_ascii_thumbnail(
        width,
        thumb_height,
        item.get("length", ""),
        item.get("title", ""),
        item.get("views", "") or item.get("meta", ""),
        item.get("thumbnail", ""),
    )
    inner_w = max(1, width - 2)
    lines.append("|" + fit(f"{index:>2}. " + item.get("channel", "YouTube"), inner_w) + "|")
    lines.append(border(width))
    return lines


def merge_grid(cards: list[list[str]], columns: int, gap: int = 2) -> str:
    if not cards:
        return ""
    rows = []
    for start in range(0, len(cards), columns):
        chunk = cards[start : start + columns]
        heights = [len(card) for card in chunk]
        max_h = max(heights)
        widths = [max(len(line) for line in card) for card in chunk]
        for y in range(max_h):
            parts = []
            for card, card_w in zip(chunk, widths):
                parts.append((card[y] if y < len(card) else "").ljust(card_w))
            rows.append((" " * gap).join(parts))
        rows.append("")
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


def render_home_interface(data: dict[str, Any] | None = None, width: int = 120, height: int = 40) -> str:
    width = max(40, width)
    height = max(12, height)
    data = data or sample_home_data()
    sidebar_w = 18 if width >= 96 else 0
    content_w = width - sidebar_w - (2 if sidebar_w else 0)

    header = nav_bar(width, "Home")
    content_lines: list[str] = []

    columns = 3 if content_w >= 114 else 2 if content_w >= 82 else 1
    card_w = max(30, (content_w - 2 - ((columns - 1) * 2)) // columns)
    thumb_h = 10 if height >= 34 else 9 if height >= 24 else 8
    card_h = thumb_h + 2
    available_grid_h = max(card_h, height - len(header) - len(content_lines))
    visible_rows = max(1, available_grid_h // (card_h + 1))
    max_videos = max(1, columns * visible_rows)
    cards = [
        render_video_card(item, card_w, index + 1, thumb_h)
        for index, item in enumerate(data.get("videos", [])[:max_videos])
    ]
    grid = merge_grid(cards, columns).splitlines()
    content_lines.extend(grid)

    if not sidebar_w:
        return "\n".join(trim_to_height(header + content_lines, height, width))

    sidebar = [
        border(sidebar_w),
        "|" + fit("  Home", sidebar_w - 2) + "|",
        "|" + fit("  Shorts", sidebar_w - 2) + "|",
        "|" + fit("  Subscriptions", sidebar_w - 2) + "|",
        "|" + fit("  History", sidebar_w - 2) + "|",
        "|" + fit("  Playlists", sidebar_w - 2) + "|",
        "|" + fit("  Downloads", sidebar_w - 2) + "|",
        border(sidebar_w),
    ]
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
    for item in suggestions[:6]:
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
    left_w = max(len(line) for line in left) if left else 0
    right_w = max(len(line) for line in right) if right else 0
    rows = max(len(left), len(right))
    merged = []
    for i in range(rows):
        l = left[i] if i < len(left) else " " * left_w
        r = right[i] if i < len(right) else " " * right_w
        merged.append(l.ljust(left_w) + (" " * gap) + r)
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
    info_lines = [""]
    info_lines.extend(wrap_lines(data.get("title", "Untitled"), width, 3) or ["Untitled"])
    info_lines.extend(wrap_lines(f"Channel: {data.get('channel', 'YouTube')}", width, 2))
    info_lines.extend(wrap_lines(metadata, width, 2))
    info_lines.extend(wrap_lines("[ Like ] [ Share ] [ Save ] [ Subscribe ]", width, 2))
    info_lines.append("")
    info_lines.extend(wrap_lines(data.get("description", ""), width - 2, 5))
    info_lines.extend(
        [
            "",
            "+" + "-" * (width - 2) + "+",
            "|" + fit(" Comments", width - 2) + "|",
            "+" + "-" * (width - 2) + "+",
            "| " + fit("@viewer  This looks like YouTube escaped into the terminal.", width - 4) + " |",
            "| " + fit("@ascii_fan  The recommendation rail is the best part.", width - 4) + " |",
            "+" + "-" * (width - 2) + "+",
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


def main():
    parser = argparse.ArgumentParser(
        description="Render a YouTube-like interface as ASCII art.",
    )
    parser.add_argument("url", nargs="?", help="Optional YouTube watch URL or video id to fetch through the local API")
    parser.add_argument("--page", choices=["home", "watch"], default="home", help="Mock page to render without URL")
    parser.add_argument("--search", "-s", help="Search query to render with /api/youtube/search")
    parser.add_argument("--api-base", default="http://127.0.0.1:3000", help="YouTube API server base URL")
    parser.add_argument("--max-results", type=int, default=12, help="Maximum search results to request from the API")
    parser.add_argument("--width", type=int, help="ASCII output width. Default: current terminal width")
    parser.add_argument("--height", type=int, help="ASCII output height. Default: current terminal height")
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="ANSI color mode. Default: auto",
    )
    parser.add_argument("--output", "-o", help="Optional output file (.txt)")
    args = parser.parse_args()

    width, height = resolve_output_size(args.width, args.height)
    if args.search:
        data = fetch_api_search(args.api_base, args.search, args.max_results)
        ascii_ui = render_home_interface(data, width, height)
    elif args.url:
        data = fetch_api_video(args.api_base, args.url)
        ascii_ui = render_ascii_interface(data, width, height)
    elif args.page == "watch":
        ascii_ui = render_ascii_interface(sample_watch_data(), width, height)
    else:
        ascii_ui = render_home_interface(sample_home_data(), width, height)

    if should_use_color(args.color, args.output):
        ascii_ui = colorize_ascii(ascii_ui)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(ascii_ui)
        print(f"[ok] saved ASCII interface to {args.output}")
    else:
        print(ascii_ui)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[info] interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
