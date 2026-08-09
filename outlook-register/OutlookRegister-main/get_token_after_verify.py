import sys
import json
import time
import base64
import hashlib
import secrets
import string
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright

def generate_code_verifier(length=128):
    alphabet = string.ascii_letters + string.digits + '-._~'
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_code_challenge(code_verifier):
    sha256_hash = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(sha256_hash).decode().rstrip('=')

def main():
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    client_id = config['oauth2']['client_id']
    redirect_url = config['oauth2']['redirect_url']
    scopes = config['oauth2']['Scopes']
    # 验证/获取token使用Google Chrome (与注册用的指纹浏览器分离)
    browser_path = config['oauth2'].get('token_browser_path') or config['playwright']['browser_path']
    browser_args = config['playwright'].get('browser_args', [])

    with open('Results/logged_email.txt', 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("No logged emails found in Results/logged_email.txt")
        return

    print("=" * 50)
    print("  Outlook Email Token Retriever")
    print("=" * 50)
    print()

    print("Available emails:")
    for i, line in enumerate(lines, 1):
        parts = line.split(':')
        email = parts[0]
        password = parts[1] if len(parts) > 1 else ''
        print(f"  {i}. {email}")
        print(f"     Password: {password}")
        print()

    if len(sys.argv) > 1:
        try:
            choice = int(sys.argv[1])
        except:
            print("Usage: python get_token_after_verify.py [email_number]")
            return
    else:
        try:
            choice = int(input("Enter email number to get token (1-{}): ".format(len(lines))))
            if choice < 1 or choice > len(lines):
                print("Invalid choice!")
                return
        except:
            print("Invalid input!")
            return

    selected = lines[choice - 1]
    email, password = selected.split(':')

    print()
    print(f"Selected: {email}")
    print(f"Password: {password}")
    print()

    print("[Info] Getting token...")
    print()

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

    # IMPORTANT: Only intercept the nativeclient redirect, NOT all requests.
    # Intercepting all requests ("**/*") blocks Microsoft login page resources
    # (JS/CSS/images) and causes the page to spin forever.
    def handle_route(route):
        url = route.request.url
        if 'code=' in url:
            captured_code[0] = url
            print(f"[Info] Code captured from redirect URL!")
            # Fulfill with a success page instead of letting the browser
            # navigate to the nativeclient endpoint (which shows an error)
            route.fulfill(
                status=200,
                content_type='text/html',
                body='<html><body style="font-family:sans-serif;text-align:center;padding-top:80px;"><h2>Authorization successful!</h2><p>You can close this window and return to the terminal.</p></body></html>'
            )
        else:
            route.continue_()

    with sync_playwright() as p:
        # 使用Google Chrome验证/获取token: 不加载指纹参数, 使用独立临时profile
        # (指纹浏览器仅用于注册流程)
        launch_args = ['--lang=zh-CN', '--disable-blink-features=AutomationControlled']
        for arg in browser_args:
            if arg.startswith('--user-data-dir=') or arg.startswith('--fingerprint='):
                continue
            if arg not in launch_args:
                launch_args.append(arg)

        browser = p.chromium.launch(
            executable_path=browser_path,
            headless=False,
            args=launch_args
        )
        page = browser.new_page()
        browser_holder = browser

        # 只拦截 nativeclient 重定向,放行所有其他请求(登录页/JS/CSS)
        page.route(f"{redirect_url}**", handle_route)

        print("Opening browser to login...")
        print()
        print("Please login with the email credentials and grant consent.")
        print()

        try:
            page.goto(auth_url, timeout=60000)
        except Exception as e:
            print(f"[Info] Navigation note: {e}")

        print("Waiting for authorization callback...")
        print("Please complete the login and consent in the browser.")
        print()

        for i in range(1800):  # 3 minutes
            time.sleep(0.1)
            if captured_code[0]:
                break
            if i > 0 and i % 300 == 0:
                print(f"  ...waiting ({i//10}s elapsed)")
        else:
            print()
            print("[Info] Auto-capture timed out after 3 minutes.")

        try:
            browser_holder.close()
        except Exception:
            pass

    if not captured_code[0]:
        print()
        print("=" * 50)
        print("  Manual Code Entry")
        print("=" * 50)
        print()
        print("If the browser shows an error page, the code may still be in the URL.")
        print("Please copy the full URL from the browser address bar:")
        print()
        print("  1. It should contain: ?code=M.C507_BAY.2.U.xxxxx...")
        print("  2. Paste it below:")
        print()

        try:
            manual_url = input("Paste URL here: ").strip()
            if manual_url and 'code=' in manual_url:
                captured_code[0] = manual_url
                print("[Info] URL received!")
            else:
                print("[Error] No code found in URL!")
                return
        except:
            print("[Error] No input received.")
            return

    parsed = urllib.parse.urlparse(captured_code[0])
    params = urllib.parse.parse_qs(parsed.query)
    auth_code = params.get('code', [None])[0]

    if not auth_code:
        print("[Error] No authorization code found!")
        print(f"URL: {captured_code[0]}")
        return

    print(f"Authorization code: {auth_code[:30]}...")
    print()

    import requests
    from urllib.request import getproxies
    proxies = getproxies()
    http_proxy = proxies.get('http') or proxies.get('https')
    proxy_settings = {"http": http_proxy, "https": http_proxy} if http_proxy else None

    try:
        print("[Info] Exchanging code for token...")
        response = requests.post(
            'https://login.microsoftonline.com/common/oauth2/v2.0/token',
            data={
                'client_id': client_id,
                'code': auth_code,
                'redirect_uri': redirect_url,
                'grant_type': 'authorization_code',
                'code_verifier': code_verifier,
                'scope': ' '.join(scopes)
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            proxies=proxy_settings
        )

        token_data = response.json()

        if 'refresh_token' in token_data:
            refresh_token = token_data['refresh_token']
            access_token = token_data.get('access_token', '')
            expires_at = datetime.now().timestamp() + token_data.get('expires_in', 0)

            print()
            print("=" * 50)
            print("  Token Retrieved Successfully!")
            print("=" * 50)
            print()
            print(f"Email:    {email}")
            print(f"Password: {password}")
            print(f"Client ID: {client_id}")
            print(f"Refresh Token:")
            print(f"  {refresh_token}")
            print()
            print(f"Access Token (first 50 chars):")
            print(f"  {access_token[:50]}...")
            print()
            print(f"Expires at: {datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}")
            print()

            with open('Results/verified_tokens.txt', 'a', encoding='utf-8') as f:
                f.write(f"{email}----{password}----{client_id}----{refresh_token}\n")
            print("[Info] Token saved to Results/verified_tokens.txt")

        else:
            print("[Error] No refresh_token in response!")
            print(f"Response: {response.text[:500]}")

    except Exception as e:
        print(f"[Error] Failed to get token: {e}")

if __name__ == "__main__":
    main()
