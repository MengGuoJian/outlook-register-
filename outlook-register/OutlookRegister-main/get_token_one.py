import sys
import json
import time
import base64
import hashlib
import secrets
import string
import urllib.parse
import webbrowser
from datetime import datetime

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

    with open('Results/logged_email.txt', 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("NO_EMAILS")
        return

    email, password = lines[0].split(':')
    email = email.strip()
    password = password.strip()

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

    print("=" * 60)
    print("  Outlook Token Retriever (Manual Mode)")
    print("=" * 60)
    print()
    print(f"[Info] Email: {email}")
    print()
    print("[Step 1] Opening browser...")
    print()

    # 用系统默认浏览器打开授权页面(手动模式,不走Playwright,避免自动化检测)
    try:
        webbrowser.open(auth_url)
        print("[Info] Browser opened. If nothing happens, copy the URL below:")
        print()
        print("  " + auth_url)
        print()
    except Exception as e:
        print(f"[Error] Could not open browser: {e}")
        print()
        print("Please copy this URL into your browser manually:")
        print()
        print("  " + auth_url)
        print()

    print("[Step 2] Login with the email and grant consent.")
    print("After authorization, the browser address bar will show a URL")
    print("containing ?code=xxxx")
    print()
    print("Please copy the ENTIRE URL from the browser address bar")
    print("(it looks like: " + redirect_url + "?code=M.C507_BAY.2.U.xxx)")
    print()
    print("Paste it here:")
    print()

    # 读取用户粘贴的完整URL
    try:
        callback_url = input(">> ").strip()
    except Exception:
        callback_url = ""
        print("[Error] Could not read input.")

    if not callback_url or 'code=' not in callback_url:
        print()
        print("=" * 60)
        print("  Manual Code Entry")
        print("=" * 60)
        print()
        print("The pasted URL did not contain ?code=.")
        print("You can also paste just the code part (after 'code='):")
        print()
        try:
            auth_code = input("Enter the authorization code directly: ").strip()
        except Exception:
            auth_code = ""
        if not auth_code:
            print("[Error] No code provided.")
            return
    else:
        parsed = urllib.parse.urlparse(callback_url)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params.get('code', [None])[0]
        if not auth_code:
            print("[Error] No 'code' parameter found in URL.")
            return

    print()
    print(f"[Info] Authorization code: {auth_code[:30]}...")
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
            print("=" * 60)
            print("  Token Retrieved Successfully!")
            print("=" * 60)
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
            print("TOKEN_SUCCESS")

        else:
            print("[Error] No refresh_token in response!")
            print(f"Response: {response.text[:500]}")
            print("TOKEN_ERROR")

    except Exception as e:
        print(f"[Error] Failed to get token: {e}")
        print("TOKEN_EXCEPTION")

if __name__ == "__main__":
    main()
