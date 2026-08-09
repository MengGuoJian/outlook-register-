import json
import time
import base64
import hashlib
import secrets
import string
import urllib.parse
from playwright.sync_api import sync_playwright

def generate_code_verifier(length=128):
    alphabet = string.ascii_letters + string.digits + '-._~'
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_code_challenge(code_verifier):
    sha256_hash = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(sha256_hash).decode().rstrip('=')

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def main():
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    client_id = config['oauth2']['client_id']
    redirect_url = config['oauth2']['redirect_url']
    scopes = config['oauth2']['Scopes']
    browser_path = config['playwright']['browser_path']
    browser_args = config['playwright'].get('browser_args', [])

    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_url,
        'scope': ' '.join(scopes),
        'response_mode': 'query',
        'prompt': 'login',
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }

    auth_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)

    captured_code = [None]
    navigations = []

    def handle_route(route):
        url = route.request.url
        navigations.append(("ROUTE", url[:120]))
        if 'code=' in url:
            captured_code[0] = url
            log(f"*** CAPTURED CODE from redirect URL! ***")
            route.fulfill(
                status=200,
                content_type='text/html',
                body='<html><body><h2>Authorization successful!</h2></body></html>'
            )
        else:
            route.continue_()

    def on_nav(frame):
        if frame == frame.page.main_frame:
            navigations.append(("NAV", frame.url[:150]))
            log(f"NAV -> {frame.url[:150]}")

    log(f"Browser: {browser_path}")
    log(f"Browser args: {browser_args}")
    log(f"Auth URL: {auth_url[:110]}...")

    # Parse args: extract user-data-dir for persistent context
    user_data_dir = None
    filtered_args = []
    for arg in browser_args:
        if arg.startswith("--user-data-dir="):
            user_data_dir = arg.split("=", 1)[1].strip('"')
        else:
            filtered_args.append(arg)

    with sync_playwright() as p:
        if user_data_dir:
            log(f"Using persistent context: {user_data_dir}")
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=['--lang=zh-CN', '--disable-blink-features=AutomationControlled'] + filtered_args,
                executable_path=browser_path,
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            log("Using regular launch (no user-data-dir)")
            browser = p.chromium.launch(
                executable_path=browser_path,
                headless=False,
                args=['--lang=zh-CN', '--disable-blink-features=AutomationControlled'] + filtered_args
            )
            page = browser.new_page()

        page.route(f"{redirect_url}**", handle_route)
        page.on("framenavigated", on_nav)

        log("Opening browser...")
        try:
            page.goto(auth_url, timeout=45000)
            log(f"goto returned. current URL: {page.url[:110]}")
        except Exception as e:
            log(f"goto exception: {type(e).__name__}: {str(e)[:150]}")
            try:
                log(f"current URL after error: {page.url[:110]}")
            except:
                pass

        for i in range(45):
            time.sleep(1)
            if captured_code[0]:
                log("CODE CAPTURED - breaking")
                break
            if i % 5 == 0:
                try:
                    title = page.title()[:40]
                    log(f"waiting... URL: {page.url[:80]} | title: {title}")
                except:
                    log(f"waiting... page closed?")

        try:
            if user_data_dir:
                context.close()
            else:
                browser.close()
        except:
            pass

    log("=" * 60)
    log("NAVIGATION LOG (unique):")
    seen = set()
    for nav in navigations:
        s = str(nav)
        if s not in seen:
            seen.add(s)
            log(s[:170])

    log("=" * 60)
    if captured_code[0]:
        log("SUCCESS! Code captured!")
    else:
        log("RESULT: login page should have loaded (no code expected without user action)")

if __name__ == "__main__":
    main()
