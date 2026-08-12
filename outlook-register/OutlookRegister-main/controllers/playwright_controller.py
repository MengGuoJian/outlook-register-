import json
import os
import threading
import time
from playwright.sync_api import sync_playwright
from .base_controller import BaseBrowserController


class PlaywrightController(BaseBrowserController):

    def __init__(self):
        super().__init__()
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.browser_path = data.get("playwright", {}).get("browser_path", "")
        self.browser_args = data.get("playwright", {}).get("browser_args", [])
        # 按压验证码的精确按压时长（毫秒），失败会自动尝试多档校准
        self.captcha_hold_ms = int(data.get("captcha_hold_ms", 3000))

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
                # 持久化模式：代理在浏览器启动时固定（同一个 profile 无法按任务切换代理）
                launch_proxy = self.proxy_settings(self.pick_proxy()) if self.proxy_pool else None
                b = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=False,
                    args=['--lang=zh-CN'] + filtered_args,
                    executable_path=self.browser_path,
                    proxy=launch_proxy,
                )
                return p, b

            # Normal launch (no fingerprint browser)：
            # 代理池模式浏览器本身不带代理，每个任务建独立上下文时随机分配
            launch_proxy = None if self.proxy_pool else self.proxy_settings(self.proxy)
            b = p.chromium.launch(
                executable_path=self.browser_path,
                headless=False,
                args=['--lang=zh-CN'],
                proxy=launch_proxy,
            )
            return p, b

        except Exception as e:
            print(f"Launch browser failed: {e}")
            return False, False

    def get_thread_page(self):
        browser = self.get_thread_browser()
        if not browser:
            return None
        # For persistent context, browser IS the context (has new_page)；代理在启动时已固定
        if hasattr(browser, 'new_page'):
            # 清空历史 cookie：防止上一轮被 PerimeterX 标记的身份（_pxvid 等）带进本轮，
            # 导致验证码在干净 IP 上也空渲染
            try:
                browser.clear_cookies()
            except Exception:
                pass
            # localStorage 里的 PX 身份键（_pxvid/_px3/_pxde）一并清掉（按前缀定向清理）
            try:
                browser.add_init_script("""
                    try {
                        var keys = Object.keys(localStorage);
                        for (var i = 0; i < keys.length; i++) {
                            if (keys[i].indexOf('_px') === 0) localStorage.removeItem(keys[i]);
                        }
                    } catch (e) {}
                """)
            except Exception:
                pass
            return browser.new_page()
        # 代理池模式：每个任务随机抽一个代理建独立上下文
        if self.proxy_pool:
            return browser.new_context(proxy=self.proxy_settings(self.pick_proxy())).new_page()
        return browser.new_context().new_page()

    def handle_captcha(self, page):

        page.wait_for_event("request", lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"), timeout=22000)
        page.wait_for_timeout(800)

        # DEBUG: 列出所有 hsprotect frame 的 URL 与 #px-captcha 内容长度，排查抓错 frame
        try:
            page.wait_for_timeout(6000)
            for fr in page.frames:
                if 'hsprotect' in fr.url:
                    try:
                        html = fr.locator('#px-captcha').inner_html(timeout=2000)
                        pc_len = len(html)
                    except Exception:
                        pc_len = -1
                    print(f"[Debug] frame: {fr.url[:90]}  #px-captcha={pc_len} chars")
                    # 渲染内容单独存 captcha_inner.html，避免被后续完整 DOM 覆盖
                    debug_path = os.path.join(self.results_dir, 'captcha_inner.html')
                    with open(debug_path, 'w', encoding='utf-8') as f:
                        f.write(f"URL: {fr.url}\n\n{html}")
        except Exception as e:
            print(f"[Debug] 导出验证码 DOM 失败: {e}")

        # PerimeterX 按压验证码：按住时长必须精确到毫秒，释放过早/过晚都会失败。
        # 首选 config.json 的 captcha_hold_ms（默认 3000），失败自动尝试多档校准。
        frame = None
        for fr in page.frames:
            if 'hsprotect' in fr.url:
                frame = fr
                break
        if frame is None:
            print("[Captcha] 未找到验证码 iframe")
            return False

        primary = self.captcha_hold_ms
        candidates = [primary] + [d for d in (2500, 3500, 2000, 4000) if d != primary]

        def _wait_success(timeout_ms: int) -> bool:
            try:
                page.wait_for_event(
                    "request",
                    lambda req: req.url.startswith("https://browser.events.data.microsoft.com"),
                    timeout=timeout_ms,
                )
            except Exception:
                return False
            for bad_text in ('一些异常活动', '此站点正在维护', 'some abnormal activity', 'this site is under maintenance'):
                if page.get_by_text(bad_text).count() > 0:
                    print("[Error: Rate limit] - 正常通过验证码，但当前IP注册频率过快。")
                    return False
            return True

        for attempt in range(self.max_captcha_retries + 1):
            # 按钮可能延迟渲染：轮询等待最多 25s（渲染为空 = IP 被静默拦截）
            btn = None
            for _ in range(13):
                btn = self._find_captcha_button(frame)
                if btn is not None:
                    break
                page.wait_for_timeout(2000)
            if btn is None:
                print("[Captcha] 验证码按钮未渲染（IP 风控或网络问题），本轮失败")
                # DEBUG: 导出完整 iframe DOM，排查是空渲染还是选择器漏检
                try:
                    full = frame.content()
                    debug_path = os.path.join(self.results_dir, 'captcha_debug.html')
                    with open(debug_path, 'w', encoding='utf-8') as f:
                        f.write(full)
                    print(f"[Debug] 已导出完整验证码 iframe DOM ({len(full)} chars)")
                except Exception:
                    pass
                continue

            cx, cy = btn
            for hold_ms in candidates:
                t0 = time.monotonic()
                page.mouse.move(cx, cy, steps=10)
                page.mouse.down()
                page.wait_for_timeout(hold_ms)  # 精确毫秒按压
                page.mouse.up()
                elapsed_ms = (time.monotonic() - t0) * 1000
                print(f"[Captcha] 按压 {elapsed_ms:.0f}ms（目标 {hold_ms}ms）第{attempt + 1}轮")

                if _wait_success(8000):
                    print(f"[Captcha] 按压通过（{hold_ms}ms）")
                    return True
                print(f"[Captcha] {hold_ms}ms 未通过，尝试下一档时长")

            # 自动按压全部失败：给人工 40 秒手动接管窗口
            print("[Captcha] 自动按压未通过 —— 请手动按住验证码按钮完成（40 秒内），自动继续...")
            if _wait_success(40000):
                print("[Captcha] 手动按压通过")
                return True

        return False

    def _find_captcha_button(self, frame):
        """在验证码 iframe 里找按压按钮，返回 (center_x, center_y) 或 None。

        先试常规选择器；找不到再用 JS 全量扫描（任何可见、尺寸达标、
        可交互样式的元素都算候选），防止非常规标签/类名漏检。
        """
        sels = ['[role="button"]', 'button', '[class*="hold"]', '[class*="press"]', '[class*="btn"]',
                'div[tabindex]', 'a[tabindex]']
        for sel in sels:
            try:
                loc = frame.locator(f'#px-captcha {sel}').first
                if loc.count() == 0:
                    continue
                if not loc.is_visible():
                    continue
                box = loc.bounding_box()
                if box and box["width"] > 10 and box["height"] > 10:
                    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            except Exception:
                continue
        try:
            info = frame.evaluate("""() => {
                const els = document.querySelectorAll('#px-captcha *');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    if (r.width > 30 && r.height > 30 && s.visibility !== 'hidden'
                        && s.display !== 'none' && s.pointerEvents !== 'none') {
                        return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                                tag: el.tagName, cls: String(el.className).slice(0, 80)};
                    }
                }
                return null;
            }""")
            if info:
                print(f"[Captcha] JS 扫描找到候选按钮: {info['tag']} class={info['cls']}")
                return info["x"], info["y"]
        except Exception:
            pass
        return None

    def clean_up(self, page=None, type="all_browser"):
        if type == "done_browser" and page:
            context = page.context
            persistent = hasattr(context, "new_page")  # launch_persistent_context 返回的对象
            try:
                context.close()
            except Exception:
                pass
            self.kill_task_forwarder()
            if persistent:
                # 持久化 context 关闭即整个浏览器关闭：清掉线程引用，让下一个任务重新启动浏览器
                for attr in ("browser", "playwright"):
                    try:
                        delattr(self.thread_local, attr)
                    except AttributeError:
                        pass

        elif type == "all_browser":
            self.kill_task_forwarder()
            for p, b in self.active_resources:
                try:
                    b.close()
                except Exception:
                    pass
                try:
                    p.stop()
                except Exception:
                    pass
