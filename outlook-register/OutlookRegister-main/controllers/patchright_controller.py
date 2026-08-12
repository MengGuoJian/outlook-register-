import random
import time
from patchright.sync_api import sync_playwright
from .base_controller import BaseBrowserController


class PatchrightController(BaseBrowserController):

    def launch_browser(self):
        try:
            # 检查是否使用指纹浏览器
            if self.browser_path and self.browser_path.endswith('chrome.exe'):
                return self._connect_via_cdp()

            p = sync_playwright().start()

            # 代理池模式浏览器不带代理，每个任务建独立上下文时随机分配
            launch_proxy = None if self.proxy_pool else self.proxy_settings(self.proxy)

            b = p.chromium.launch(
                headless=False,
                args=['--lang=zh-CN'],
                proxy=launch_proxy,
            )

            return p, b

        except Exception as e:
            print(f"启动浏览器失败: {e}")
            return False, False

    def _connect_via_cdp(self):
        """通过 CDP 连接已运行的指纹浏览器"""
        port = self.browser_debug_port
        import urllib.request
        import json

        try:
            # 检查浏览器是否已启动
            resp = urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=3)
            data = json.loads(resp.read().decode())
            webSocketDebuggerUrl = data.get('webSocketDebuggerUrl', '')

            if not webSocketDebuggerUrl:
                print(f"[Error] 无法获取 CDP 端点，请确认浏览器已启动")
                return False, False

            print(f"[Info] 检测到指纹浏览器已运行在端口 {port}")
            print(f"      WebSocket: {webSocketDebuggerUrl}")

            p = sync_playwright().start()
            b = p.chromium.connect_over_cdp(webSocketDebuggerUrl)
            return p, b

        except Exception as e:
            print(f"[Error] 连接指纹浏览器失败: {e}")
            print(f"        请先手动启动指纹浏览器: chrome.exe --remote-debugging-port={port}")
            return False, False
        
    def handle_captcha(self, page):

        frame1 = page.frame_locator('iframe[title="验证质询"]')
        frame2 = frame1.frame_locator('iframe[style*="display: block"]')

        for _ in range(0, self.max_captcha_retries + 1):

            page.wait_for_timeout(random.randint(250, 450))
            loc = frame2.locator('[aria-label="可访问性挑战"]')
            self.smooth_click(page, loc)

            page.wait_for_timeout(random.randint(300, 600))
            loc2 = frame2.locator('[aria-label="再次按下"]')
            self.smooth_click(page, loc2)

            try:
                page.locator('.draw').wait_for(state="detached", timeout=14000)
                try:
                    # 简单的认为加载8秒后成功，暂不考虑请求.
                    page.locator('[role="status"][aria-label="正在加载..."]').wait_for(timeout=5000)

                    captcha_passed = False
                    for _ in range(20):
                        if page.get_by_text('一些异常活动').count() or page.get_by_text('此站点正在维护，暂时无法使用，请稍后重试。').count() > 0:
                            print("[Error: Rate limit] - 正常通过验证码，但当前IP注册频率过快。")
                            return False
                        elif frame2.locator('[aria-label="可访问性挑战"]').count() > 0:  
                            captcha_passed = False
                            page.wait_for_timeout(random.randint(500, 1000))
                            break
                        elif page.get_by_text("暂时跳过").count() > 0:
                            captcha_passed = True
                            break
                        page.wait_for_timeout(random.randint(375, 425))
                    else:
                        if frame2.locator('[aria-label="可访问性挑战"]').count() == 0:
                            captcha_passed = True

                    if captcha_passed:
                        break

                except Exception:
                    if page.get_by_text('暂时跳过').count() > 0:
                        break
                    frame1.locator(':has-text("请再试一次"), :has-text("Keep going"), :has-text("a few more tries")').first.wait_for(timeout=15000)
                    continue

            except Exception:
                if page.get_by_text('暂时跳过').count() > 0:
                     break
                return False
        else: 
            return False

        return True

    def get_thread_page(self):
        browser = self.get_thread_browser()
        # 代理池模式：每个任务随机抽一个代理建独立上下文（CDP 连接同样支持）
        if self.proxy_pool:
            context = browser.new_context(proxy=self.proxy_settings(self.pick_proxy()))
        else:
            context = browser.new_context()
        return context.new_page()

    def clean_up(self, page=None, type="all_browser"):
        if type == "done_browser" and page:
            context = page.context
            try:
                context.close()
            except Exception:
                pass
            self.kill_task_forwarder()

        elif type == "all_browser":
            self.kill_task_forwarder()
            for p, b in self.active_resources:
                try:
                    b.close()
                except Exception: pass
                try:
                    p.stop()
                except Exception: pass