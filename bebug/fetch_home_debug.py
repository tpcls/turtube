from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def extract_json_blob(page_html: str, marker: str):
    needle = marker + " = "
    start = page_html.find(needle)
    if start < 0:
        start = page_html.find(marker)
    if start < 0:
        return None

    i = page_html.find("{", start + len(marker))
    if i < 0:
        return None

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
                    return json.loads(page_html[begin : i + 1])
        i += 1
    return None


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    url = "https://www.youtube.com/?hl=ko&gl=KR"
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    with urlopen(req, timeout=20) as resp:
        html = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")

    (out_dir / "youtube_home.html").write_text(html, encoding="utf-8")
    initial_data = extract_json_blob(html, "var ytInitialData") or extract_json_blob(html, "ytInitialData")
    if initial_data is not None:
        (out_dir / "yt_initial_data.json").write_text(
            json.dumps(initial_data, ensure_ascii=False, indent=2)[:2_000_000],
            encoding="utf-8",
        )

    key_counts = {}
    video_like = []
    for item in walk(initial_data):
        for key in item:
            if key.endswith("Renderer") or key.endswith("ViewModel"):
                key_counts[key] = key_counts.get(key, 0) + 1
        for key in ("videoRenderer", "richItemRenderer", "lockupViewModel", "compactVideoRenderer"):
            value = item.get(key)
            if isinstance(value, dict):
                text = json.dumps(value, ensure_ascii=False)[:1200]
                video_like.append({"key": key, "sample": text})
                break

    summary = {
        "html_bytes": len(html),
        "has_initial_data": initial_data is not None,
        "marker_positions": {
            key: html.find(key)
            for key in ["ytInitialData", "richItemRenderer", "videoRenderer", "lockupViewModel", "compactVideoRenderer"]
        },
        "renderer_counts": dict(sorted(key_counts.items(), key=lambda kv: kv[1], reverse=True)[:40]),
        "video_like_samples": video_like[:20],
        "watch_urls_in_html": re.findall(r"/watch\\?v=([A-Za-z0-9_-]{11})", html)[:20],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
