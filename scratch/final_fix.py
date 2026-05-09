import sys

path = 'src/youtube_html_ascii.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# The file is messy. We want to keep everything up to the end of render_home_interface.
# render_home_interface ends with: 
# return "\n".join(trim_to_height(header + [merge_columns(sidebar, content_lines)], height, width))

fixed_lines = []
found_end = False
for line in lines:
    fixed_lines.append(line)
    if 'return "\\n".join(trim_to_height(header + [merge_columns(sidebar, content_lines)], height, width))' in line:
        found_end = True
        break

if not found_end:
    # Try another possible end line
    fixed_lines = []
    for line in lines:
        fixed_lines.append(line)
        if 'return "\\n".join(trim_to_height(header + content_lines, height, width))' in line:
            break

# Now append the missing code properly
missing_code = """

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
    return "\\n".join(merged)


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
        top = "\\n".join(player)
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

    return "\\n".join(trim_to_height(page_header + [top] + info_lines, height, width))


def run_interactive(data: dict[str, Any], width: int, height: int, color_mode: str):
    selected_id = "video_0"
    
    while True:
        # Clear screen and move cursor to top
        sys.stdout.write("\\033[H")
        
        # Render
        ascii_ui = render_home_interface(data, width, height, selected_id=selected_id)
        if should_use_color(color_mode, None):
            ascii_ui = colorize_ascii(ascii_ui)
        
        sys.stdout.write(ascii_ui)
        sys.stdout.flush()
        
        # Wait for key
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
            
        key = msvcrt.getch()
        
        if key == b'\\x1b' or key.lower() == b'q': # Esc or Q
            break
            
        # Handle arrows (Windows)
        if key in (b'\\x00', b'\\xe0'):
            key = msvcrt.getch()
            # Navigation logic
            columns = 1
            sidebar_w = 20 if width >= 100 else 0
            content_w = width - sidebar_w - (2 if sidebar_w else 0)
            if content_w >= 130: columns = 3
            elif content_w >= 80: columns = 2
            
            if selected_id.startswith("video_"):
                idx = int(selected_id.split("_")[1])
                num_videos = len(data.get("videos", []))
                
                if key == b'H': # Up
                    if idx >= columns:
                        selected_id = f"video_{idx - columns}"
                    else:
                        selected_id = "tab_0"
                elif key == b'P': # Down
                    if idx + columns < num_videos:
                        selected_id = f"video_{idx + columns}"
                elif key == b'K': # Left
                    if idx > 0:
                        selected_id = f"video_{idx - 1}"
                elif key == b'M': # Right
                    if idx < num_videos - 1:
                        selected_id = f"video_{idx + 1}"
                        
            elif selected_id.startswith("tab_"):
                idx = int(selected_id.split("_")[1])
                if key == b'K': # Left
                    if idx > 0: selected_id = f"tab_{idx - 1}"
                elif key == b'M': # Right
                    if idx < 3: selected_id = f"tab_{idx + 1}"
                elif key == b'P': # Down
                    selected_id = "video_0"
                elif key == b'H': # Up
                    selected_id = "search"
                    
            elif selected_id == "search":
                if key == b'P': # Down
                    selected_id = "tab_0"
        
        elif key == b'\\r': # Enter
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Render a YouTube-like interface as ASCII art.",
    )
    parser.add_argument("url", nargs="?", help="Optional YouTube watch URL or video id to fetch through the local API")
    parser.add_argument("--page", choices=["home", "watch"], default="home", help="Mock page to render without URL")
    parser.add_argument("--search", "-s", help="Search query to render with /api/youtube/search")
    parser.add_argument("--api-base", default="http://127.0.0.1:3000", help="YouTube API server base URL")
    parser.add_argument("--max-results", type=int, default=9, help="Maximum search results to request from the API")
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
    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, io.UnsupportedOperation):
            pass

    width, height = resolve_output_size(args.width, args.height)
    
    if args.search:
        data = fetch_api_search(args.api_base, args.search, args.max_results)
    elif args.url:
        data = fetch_api_video(args.api_base, args.url)
    elif args.page == "watch":
        data = sample_watch_data()
    else:
        data = sample_home_data()

    if args.interactive or (not args.output and sys.stdin.isatty()):
        sys.stdout.write("\\033[?25l")
        try:
            run_interactive(data, width, height, args.color)
        finally:
            sys.stdout.write("\\033[?25h")
    else:
        ascii_ui = render_home_interface(data, width, height, selected_id=args.select or "")
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
"""

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)
    f.write(missing_code)
print("Final fix Done")
