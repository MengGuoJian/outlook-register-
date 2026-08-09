"""
Outlook 登录测试脚本
测试指定账号能否登录，并检查能否接收验证码
支持手动输入验证码
"""
import sys
import os
import time
from patchright.sync_api import sync_playwright

EMAIL = "vuoh1jon9yhkej@outlook.com"
PASSWORD = "U94HpbnNAU&"
PROXY = "http://127.0.0.1:7897"


def login_and_check():
    p = sync_playwright().start()

    proxy_settings = {
        "server": PROXY,
        "bypass": "localhost",
    }

    b = p.chromium.launch(
        headless=False,
        args=['--lang=zh-CN'],
        proxy=proxy_settings
    )

    ctx = b.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    page = ctx.new_page()

    try:
        # 1. 打开登录页
        print("[1/4] Opening login page...")
        page.goto("https://login.live.com/login.srf", timeout=30000)
        page.wait_for_timeout(3000)

        screenshot_path = os.path.join(os.path.dirname(__file__), "Results", "login_step1.png")
        os.makedirs("Results", exist_ok=True)
        page.screenshot(path=screenshot_path)
        print(f"      Screenshot saved: {screenshot_path}")
        print(f"      Current URL: {page.url}")

        # 2. 输入邮箱
        print("\n[2/4] Entering email...")
        login_input = page.locator('#usernameEntry')
        login_input.wait_for(state="visible", timeout=10000)
        login_input.fill(EMAIL)
        page.wait_for_timeout(800)
        print(f"      Email entered: {EMAIL}")

        # 点击 Next
        next_btn = page.locator('[data-testid="primaryButton"]')
        next_btn.wait_for(state="visible", timeout=10000)
        next_btn.click()
        print(f"      Clicked Next...")

        # 3. 等待密码页或验证码页
        print("\n[3/4] Waiting for next page...")
        page.wait_for_timeout(3000)

        current_url = page.url
        page_text = page.inner_text('body')
        print(f"      Current URL: {current_url}")

        # 检查是否是密码页
        if 'passwd' in current_url or 'login.srf' in current_url and 'passwd' not in current_url:
            # 尝试输入密码
            pwd_input = page.locator('[name="passwd"]')
            try:
                pwd_input.wait_for(state="visible", timeout=5000)
                pwd_input.fill(PASSWORD)
                page.wait_for_timeout(500)
                next_btn.click()
                print(f"      Password entered")
            except:
                # 可能不是密码页，检查是否有验证码
                if '验证' in page_text or '验证码' in page_text:
                    print(f"      Detected verification code page!")
                else:
                    print(f"      Could not find password field")

        # 截图
        screenshot_path = os.path.join(os.path.dirname(__file__), "Results", "login_step2.png")
        page.screenshot(path=screenshot_path)
        print(f"      Screenshot saved: {screenshot_path}")
        print(f"\n      Page content:\n{page_text[:500]}")

        # 4. 检查是否需要验证码
        print(f"\n[4/4] Checking login status...")

        if '验证' in page_text or '验证码' in page_text or 'verification' in page_text.lower():
            print(f"\n      [!] Verification code required!")
            print(f"      Please check your linked email for the code.")
            print(f"      Enter the code in the browser window that just opened.")
            print(f"      Waiting for you to enter the code manually...")

            # 等待用户手动输入验证码
            for i in range(60):  # 等待最多5分钟
                page.wait_for_timeout(5000)
                current_url = page.url
                page_text = page.inner_text('body')

                # 检查是否登录成功
                if 'owa' in current_url or 'outlook.live.com/mail' in current_url:
                    print(f"\n      [SUCCESS] Logged in to Outlook successfully!")
                    return True

                # 检查是否还在验证码页
                if '验证' in page_text or '验证码' in page_text:
                    print(f"      Waiting for verification code... ({i+1}/60)")
                else:
                    # 页面发生了变化
                    print(f"      Page changed to: {current_url}")
                    if 'login' in current_url:
                        print(f"      Back to login page - verification failed")
                        return False

            print(f"\n      [TIMEOUT] Verification code timeout")
            return False

        elif 'owa' in current_url or 'outlook.live.com/mail' in current_url:
            print(f"\n      [SUCCESS] Logged in to Outlook successfully!")
            return True

        else:
            print(f"\n      [UNKNOWN] Current state: {current_url}")
            print(f"      Please check the browser window and screenshot.")
            print(f"      Screenshot saved: {screenshot_path}")

            # 保持浏览器打开，让用户手动操作
            print(f"\n      Browser will stay open. Close it manually when done.")
            try:
                while True:
                    page.wait_for_timeout(5000)
                    current_url = page.url
                    if 'owa' in current_url or 'outlook.live.com/mail' in current_url:
                        print(f"\n      [SUCCESS] Logged in to Outlook!")
                        return True
                    if 'login' in current_url and 'live.com' in current_url:
                        print(f"\n      Back to login page")
                        return False
            except KeyboardInterrupt:
                pass
            return False

    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        b.close()
        p.stop()


if __name__ == "__main__":
    success = login_and_check()
    sys.exit(0 if success else 1)
