import json
import threading
from playwright.sync_api import sync_playwright
from .base_controller import BaseBrowserController


class PlaywrightController(BaseBrowserController):

    def __init__(self):
        super().__init__()
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.browser_path = data.get("playwright", {}).get("browser_path", "")
        self.browser_args = data.get("playwright", {}).get("browser_args", [])

    def launch_browser(self):
        """Launch browser in current thread. Playwright sync API must stay in one thread."""
        try:
            p = sync_playwright().start()

            # Detect if using fingerprint browser (has user-data-dir in args)
            use_persistent = False
            user_data_dir = None
            filtered_args = []
            for arg in self.browser_args:
                if arg.startswith("--user-data-dir="):
                    use_persistent = True
                    user_data_dir = arg.split("=", 1)[1].strip('"')
                    continue
                filtered_args.append(arg)

            if use_persistent and user_data_dir:
                print(f"[Info] Using persistent context: {user_data_dir}")
                b = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=False,
                    args=['--lang=zh-CN'] + filtered_args,
                    executable_path=self.browser_path,
                )
                return p, b

            # Normal launch (no fingerprint browser)
            proxy_settings = {
                "server": self.proxy,
                "bypass": "localhost",
            } if self.proxy else None
            b = p.chromium.launch(
                executable_path=self.browser_path,
                headless=False,
                args=['--lang=zh-CN'],
                proxy=proxy_settings
            )
            return p, b

        except Exception as e:
            print(f"Launch browser failed: {e}")
            return False, False

    def get_thread_page(self):
        browser = self.get_thread_browser()
        if not browser:
            return None
        # For persistent context, browser IS the context (has new_page)
        if hasattr(browser, 'new_page'):
            return browser.new_page()
        return browser.new_context().new_page()

    def handle_captcha(self, page):

        page.wait_for_event("request", lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"), timeout=22000)
        page.wait_for_timeout(800)

        for _ in range(0, self.max_captcha_retries + 1):

            page.keyboard.press('Enter')
            page.wait_for_timeout(11500)
            page.keyboard.press('Enter')

            try:
                page.wait_for_event("request", lambda req: req.url.startswith("https://browser.events.data.microsoft.com"), timeout=8000)
                try:
                    page.wait_for_event("request", lambda req: req.url.startswith("https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"), timeout=1700)
                    page.wait_for_timeout(2000)
                    continue

                except:
                    if page.get_by_text('some abnormal activity').count() or page.get_by_text('this site is under maintenance').count() > 0:
                        print("[Error: Rate limit] - Normal captcha passed, but current IP registration frequency is too fast.")
                        return False
                    break

            except:
                page.wait_for_timeout(5000)
                page.keyboard.press('Enter')
                page.wait_for_event("request", lambda req: req.url.startswith("https://browser.events.data.microsoft.com"), timeout=10000)

                try:
                    page.wait_for_event("request", lambda req: req.url.startswith("https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"), timeout=4000)
                except:
                    break
                page.wait_for_timeout(500)
        else:
            return False

        return True

    def clean_up(self, page=None, type="all_browser"):
        if type == "done_browser" and page:
            context = page.context
            context.close()

        elif type == "all_browser":
            for p, b in self.active_resources:
                try:
                    b.close()
                except Exception:
                    pass
                try:
                    p.stop()
                except Exception:
                    pass
