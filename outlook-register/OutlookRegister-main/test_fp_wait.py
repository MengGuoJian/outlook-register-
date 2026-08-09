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

    import base64, hashlib, secrets, string
    def generate_code_verifier(length=128):
        alphabet = string.ascii_letters + string.digits + '-._~'
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    def generate_code_challenge(code_verifier):
        sha256_hash = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(sha256_hash).decode().rstrip('=')

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

    print(f"[TEST] user_data_dir={user_data_dir}")
    print(f"[TEST] args={launch_args}")

    t0 = time.time()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=launch_args,
            executable_path=browser_path,
        )
        page = context.pages[0] if context.pages else context.new_page()
        t1 = time.time()
        print(f"[TEST] browser launched in {t1-t0:.1f}s")

        page.goto(auth_url, timeout=60000)
        t2 = time.time()
        print(f"[TEST] goto done in {t2-t1:.1f}s, url={page.url[:80]}")

        # 等待登录表单出现
        try:
            page.wait_for_selector('[name="loginfmt"]', timeout=120000)
            t3 = time.time()
            print(f"[TEST] LOGINFMT APPEARED after {(t3-t2):.1f}s (goto to form)")
            page.screenshot(path='fp_login_ok.png')
            print("[TEST] screenshot saved: fp_login_ok.png")
        except Exception as e:
            t3 = time.time()
            print(f"[TEST] loginfmt NEVER appeared ({(t3-t2):.1f}s): {str(e)[:100]}")
            body = page.evaluate('document.body.innerHTML')
            print(f"[TEST] body len={len(body)}")
            print(f"[TEST] body head: {body[:300]}")

        context.close()

    print(f"[TEST] total {time.time()-t0:.1f}s DONE")

if __name__ == "__main__":
    main()
