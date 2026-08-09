import json
import time
import urllib.parse
from playwright.sync_api import sync_playwright

def main():
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    client_id = config['oauth2']['client_id']
    redirect_url = config['oauth2']['redirect_url']
    scopes = config['oauth2']['Scopes']
    browser_path = config['playwright']['browser_path']
    browser_args = config['playwright'].get('browser_args', [])

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_url,
        'scope': ' '.join(scopes),
        'response_mode': 'query',
        'prompt': 'login',
        'code_challenge': 'test_challenge_123456789012345678901234567890123456789012345678901234567890',
        'code_challenge_method': 'S256'
    }
    auth_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)

    # 解析 user-data-dir
    user_data_dir = None
    filtered_args = []
    for arg in browser_args:
        if arg.startswith('--user-data-dir='):
            user_data_dir = arg.split('=', 1)[1].strip('"')
        else:
            filtered_args.append(arg)

    launch_args = ['--lang=zh-CN', '--disable-blink-features=AutomationControlled']
    for arg in filtered_args:
        if arg not in launch_args:
            launch_args.append(arg)

    print(f"[Test] user_data_dir={user_data_dir}")
    print(f"[Test] args={launch_args}")
    print(f"[Test] browser_path={browser_path}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=launch_args,
            executable_path=browser_path,
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("[Test] Browser opened, navigating...")
        try:
            page.goto(auth_url, timeout=45000)
            print(f"[Test] goto OK. URL={page.url[:100]}")
        except Exception as e:
            print(f"[Test] goto error: {type(e).__name__}: {str(e)[:150]}")
            try:
                print(f"[Test] URL after error: {page.url[:100]}")
            except:
                pass

        page.wait_for_timeout(8000)

        # 检查页面状态
        try:
            title = page.title()
            print(f"[Test] Title: {title}")
        except Exception as e:
            print(f"[Test] title error: {e}")

        # 截屏
        try:
            page.screenshot(path='debug_screenshot.png')
            print("[Test] Screenshot saved: debug_screenshot.png")
        except Exception as e:
            print(f"[Test] screenshot error: {e}")

        # 检查是否有登录表单
        try:
            login_input = page.locator('[name="loginfmt"]')
            count = login_input.count()
            print(f"[Test] loginfmt input count: {count}")
        except Exception as e:
            print(f"[Test] locator error: {e}")

        context.close()

    print("[Test] DONE")

if __name__ == "__main__":
    main()
