import sys

path = 'src/youtube_html_ascii.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update nav_bar signature
content = content.replace(
    'def nav_bar(width: int, selected: str = "Home") -> list[str]:',
    'def nav_bar(width: int, selected: str = "Home", selected_id: str = "") -> list[str]:'
)

# 2. Update search bar color logic
content = content.replace(
    'search = "[ Search " + "_" * max(2, search_w - 10) + " ]"',
    'search_text = " Search " + "_" * max(2, search_w - 10) + " "\n        if selected_id == "search":\n            search_text = paint(search_text, "bright_black")\n        search = "[" + search_text + "]"'
)

# 3. Update tabs color logic
old_tabs = """    tabs = "  ".join(
        ("[" + item + "]") if item == selected else item
        for item in ["Home", "Shorts", "Subscriptions", "Library"]
    )"""
new_tabs = """    tab_list = ["Home", "Shorts", "Subscriptions", "Library"]
    tab_parts = []
    for i, item in enumerate(tab_list):
        text = item
        if selected_id == f"tab_{i}":
            text = paint(text, "bright_black")
        if item == selected:
            tab_parts.append("[" + text + "]")
        else:
            tab_parts.append(text)
    tabs = "  ".join(tab_parts)"""
content = content.replace(old_tabs, new_tabs)

# 4. Update render_home_interface signature
content = content.replace(
    'def render_home_interface(data: dict[str, Any] | None = None, width: int = 120, height: int = 40) -> str:',
    'def render_home_interface(data: dict[str, Any] | None = None, width: int = 120, height: int = 40, selected_id: str = "") -> str:'
)

# 5. Update header call
content = content.replace(
    'header = nav_bar(width, "Home")',
    'header = nav_bar(width, "Home", selected_id)'
)

# 6. Update video card loop
content = content.replace(
    'render_video_card(item, card_w, index + 1, thumb_h)',
    'render_video_card(item, card_w, index + 1, thumb_h, selected=(selected_id == f"video_{index}"))'
)

# 7. Add --select to main
content = content.replace(
    'parser.add_argument("--output", "-o", help="Optional output file (.txt)")',
    'parser.add_argument("--output", "-o", help="Optional output file (.txt)")\n    parser.add_argument("--select", help="ID of the component to select (search, video_0, etc.)")'
)
content = content.replace(
    'ascii_ui = render_home_interface(data, width, height)',
    'ascii_ui = render_home_interface(data, width, height, selected_id=args.select or "")'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
