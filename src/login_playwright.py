import sys
import os
import json
import time
from playwright.sync_api import sync_playwright

def run_login():
    with sync_playwright() as p:
        # Using non-persistent context for a clean login
        # Or persistent if we want to save session
        user_data_dir = os.path.join(os.getcwd(), "scratch", "playwright_user_data")
        # Modern User-Agent
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        
        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            user_agent=user_agent,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ],
            ignore_default_args=["--enable-automation"]
        )
        page = browser.pages[0]
        page.goto("https://www.youtube.com/")
        
        print("\n[Playwright] 브라우저 창이 열렸습니다. 로그인을 진행해 주세요.")
        print("[Playwright] 로그인이 완료되면 이 창을 닫거나 터미널에서 Ctrl+C를 누르세요.")
        
        # Wait for user to be logged in by checking for avatar or sign out link
        # Actually, let's just wait until the user closes the browser window themselves
        # or we detect a login cookie.
        
        try:
            while True:
                # Check if browser is still open
                if not browser.browser.is_connected():
                    break
                
                try:
                    # Check for login state (simple check)
                    is_logged_in = page.evaluate("() => document.body ? (document.body.innerHTML.includes('yt-avatar-shape') || document.body.innerHTML.includes('SIGN OUT')) : false")
                    if is_logged_in:
                        print("[Playwright] 로그인 감지됨! 쿠키를 추출합니다...")
                        cookies = browser.cookies()
                        # Convert to Netscape format
                        netscape_path = os.path.join(os.getcwd(), "scratch", "cookies.txt")
                        with open(netscape_path, "w") as f:
                            f.write("# Netscape HTTP Cookie File\n")
                            for c in cookies:
                                domain = c['domain']
                                include_sub = "TRUE" if domain.startswith(".") else "FALSE"
                                path = c['path']
                                secure = "TRUE" if c['secure'] else "FALSE"
                                expires = int(c['expires']) if c['expires'] > 0 else 0
                                name = c['name']
                                value = c['value']
                                f.write(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                        
                        print(f"[Playwright] 쿠키 저장 완료: {netscape_path}")
                        time.sleep(2)
                        break
                except Exception as e:
                    # Ignore context destruction errors during navigation
                    if "Execution context was destroyed" in str(e):
                        pass
                    else:
                        print(f"[Playwright] 루프 내 경고: {e}")
                    
                time.sleep(2)
        except Exception as e:
            print(f"[Playwright] 중단됨: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_login()
