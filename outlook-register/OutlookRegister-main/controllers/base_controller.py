import os
import socket
import subprocess
import sys
import time
import json
import random
import threading
from faker import Faker
from abc import ABC, abstractmethod

# 域名邮箱辅助验证（新功能）：缺依赖时自动降级，不影响原有注册流程
try:
    from recovery_email import handle_recovery_email
    from domain_mail_client import normalize_proxy, recovery_enabled
except ImportError:
    handle_recovery_email = None
    recovery_enabled = lambda: False
    normalize_proxy = lambda s: s


class BaseBrowserController(ABC):
    """
    所有浏览器通用的接口和共享逻辑
    """

    def __init__(self):
        with open('config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.wait_time = data['bot_protection_wait'] * 1000
        self.max_captcha_retries = data['max_captcha_retries']
        self.enable_oauth2 = data["oauth2"]['enable_oauth2']
        self.proxy = data['proxy']
        # 代理池：config.json 的 proxies 字段 + 同目录 proxies.txt（每行一个，支持 # 注释）
        # 每个注册任务会随机抽一个代理；池为空时回退到单代理 proxy
        self.proxy_pool = self._load_proxy_pool(data)
        # 本地池转发器模式：走 pool_forwarder.py（HTTP/1.0 CONNECT 改写 + 池内随机端口）
        self.use_pool_forwarder = bool(data.get("use_pool_forwarder", False))
        # 转发器经 Clash 中转（cliproxy 等拒绝直连来源的代理用）；raw=原样转发客户端请求
        self.pool_forwarder_via_clash = str(data.get("pool_forwarder_via_clash", "") or "")
        self.pool_forwarder_raw = bool(data.get("pool_forwarder_raw", False))
        # 已用过的池端口（每个任务不重复抽取；抽完一轮后重置）
        self._used_pool_ports = set()
        self._used_pool_lock = threading.Lock()
        self.email_suffix = data['email_suffix']

        # 指纹浏览器配置
        self.browser_path = data.get("playwright", {}).get("browser_path", "")
        self.browser_debug_port = data.get("playwright", {}).get("remote_debugging_port", 9222)

        self.thread_local = threading.local()
        self.cleanup_lock = threading.Lock()
        self.active_resources = []  # 记录资源以便关闭

        self.results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Results')
        os.makedirs(self.results_dir, exist_ok=True)

    def _load_proxy_pool(self, data):
        """合并 config.json 的 proxies 字段与 proxies.txt 文件（去重保序）"""
        pool = []
        proxies_cfg = data.get('proxies') or []
        if isinstance(proxies_cfg, str):
            proxies_cfg = [proxies_cfg]
        pool.extend(normalize_proxy(p) for p in proxies_cfg if p and str(p).strip())

        txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'proxies.txt')
        if os.path.exists(txt_path):
            with open(txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        pool.append(normalize_proxy(line))

        seen, dedup = set(), []
        for p in pool:
            if p not in seen:
                seen.add(p)
                dedup.append(p)
        return dedup

    def pick_proxy(self):
        """返回当前任务使用的代理。

        转发器模式：为当前任务起一个专用转发器（锁定一个未用过的池端口，
        该任务所有连接走同一出口 IP）；否则从代理池随机抽；池为空时返回单代理。
        """
        if self.use_pool_forwarder:
            return self._spawn_task_forwarder()
        if self.proxy_pool:
            chosen = random.choice(self.proxy_pool)
            self.thread_local.proxy = chosen
            print(f"[Proxy] 本任务代理: {chosen}")
            return chosen
        return self.proxy

    def _spawn_task_forwarder(self):
        """为当前任务起专用转发器：随机挑一个未用过的池端口，锁定给本任务。"""
        proc = getattr(self.thread_local, "forwarder_proc", None)
        if proc is not None and proc.poll() is None:
            return getattr(self.thread_local, "forwarder_proxy", "http://127.0.0.1:8899")

        pool = self.proxy_pool
        if not pool:
            return self.proxy

        with self._used_pool_lock:
            available = [p for p in pool if p not in self._used_pool_ports]
            if not available:  # 一轮用完，重置再来
                self._used_pool_ports = set()
                available = list(pool)
            chosen = random.choice(available)
            self._used_pool_ports.add(chosen)
        host = chosen.rsplit(":", 1)[0].replace("http://", "").replace("https://", "")
        port = int(chosen.rsplit(":", 1)[1])

        # 找空闲本地监听端口
        local_port = None
        for _ in range(30):
            candidate = random.randint(20000, 28000)
            try:
                probe = socket.create_connection(("127.0.0.1", candidate), timeout=0.3)
                probe.close()
            except Exception:
                local_port = candidate
                break
        if local_port is None:
            return self.proxy

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pool_forwarder.py")
        extra_args = []
        if self.pool_forwarder_via_clash:
            extra_args += ["--via-clash", self.pool_forwarder_via_clash]
        if self.pool_forwarder_raw:
            extra_args += ["--raw"]
        try:
            proc = subprocess.Popen(
                [sys.executable, script, "--listen", f"127.0.0.1:{local_port}",
                 "--fixed", str(port)] + extra_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            print(f"[Proxy] 启动任务转发器失败: {e}")
            return self.proxy

        for _ in range(30):  # 等转发器监听就绪
            try:
                probe = socket.create_connection(("127.0.0.1", local_port), timeout=0.3)
                probe.close()
                break
            except Exception:
                time.sleep(0.2)

        self.thread_local.forwarder_proc = proc
        self.thread_local.forwarder_proxy = f"http://127.0.0.1:{local_port}"
        print(f"[Proxy] 任务转发器: 127.0.0.1:{local_port} -> 池端口 {port}")
        return self.thread_local.forwarder_proxy

    def kill_task_forwarder(self):
        """任务结束清理：停掉本任务专用转发器"""
        proc = getattr(self.thread_local, "forwarder_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                delattr(self.thread_local, "forwarder_proc")
            except AttributeError:
                pass
            try:
                delattr(self.thread_local, "forwarder_proxy")
            except AttributeError:
                pass

    def proxy_settings(self, proxy_str):
        """把代理字符串转成 playwright 的 proxy 配置，支持 http://user:pass@host:port 及四段式"""
        proxy_str = normalize_proxy(proxy_str)
        if not proxy_str:
            return None
        settings = {"server": proxy_str, "bypass": "localhost"}
        if "://" in proxy_str and "@" in proxy_str:
            scheme, rest = proxy_str.split("://", 1)
            if "@" in rest:
                cred, hostport = rest.rsplit("@", 1)
                if ":" in cred:
                    user, pwd = cred.split(":", 1)
                    settings = {
                        "server": f"{scheme}://{hostport}",
                        "username": user,
                        "password": pwd,
                        "bypass": "localhost",
                    }
        return settings

    def get_last_pos(self):
        """获取当前线程的上一次鼠标位置 (x, y)"""
        return getattr(self.thread_local, 'last_pos', None)

    def set_last_pos(self, x, y):
        """设置当前线程的鼠标位置 (x, y)"""
        self.thread_local.last_pos = (float(x), float(y))

    def reset_last_pos(self):
        """重置当前线程的坐标历史"""
        if hasattr(self.thread_local, 'last_pos'):
            del self.thread_local.last_pos

    def wait_random_ratio(self, page, min_ratio, delta=0.02):

        actual_ratio = random.uniform(min_ratio, min_ratio + delta)
        page.wait_for_timeout(actual_ratio * self.wait_time)

    def smooth_move_to(self, page, target_x, target_y, steps=None):
        """从上一次坐标滑动到目标坐标"""
        last_pos = self.get_last_pos()
        if not last_pos:
            last_pos = (random.uniform(150, 450), random.uniform(100, 350))
            try:
                page.mouse.move(last_pos[0], last_pos[1])
            except Exception:
                pass

        if steps is None:
            steps = random.randint(6, 14)

        try:
            page.mouse.move(target_x, target_y, steps=steps)
        except Exception:
            pass

        self.set_last_pos(target_x, target_y)

    def smooth_click(self, page, locator, offset_range=5, click_delay_range=(60, 160)):
        """点击方法"""
        try:
            box = locator.bounding_box()
            if not box:
                locator.click()
                return False

            tx = box['x'] + box['width'] / 2 + random.uniform(-offset_range, offset_range)
            ty = box['y'] + box['height'] / 2 + random.uniform(-offset_range, offset_range)

            self.smooth_move_to(page, tx, ty)

            pause_ms = random.randint(click_delay_range[0], click_delay_range[1])
            page.wait_for_timeout(pause_ms)

            page.mouse.click(tx, ty)
            self.set_last_pos(tx, ty)
            return True
        except Exception:
            try:
                locator.click()
            except Exception:
                pass
            return False

    def smooth_type(self, page, locator, text, click_first=True):
        """输入方法"""
        if click_first:
            self.smooth_click(page, locator)

        for char in text:
            try:
                locator.type(char, delay=random.randint(40, 110))
            except Exception:
                break

    @abstractmethod
    def launch_browser(self):
        """
        获取浏览器实例,返回playwright_instance, browser_instance
        """
        pass

    @abstractmethod
    def handle_captcha(self, page):
        """
        验证码处理流程
        """
        pass

    @abstractmethod 
    def clean_up(self, page=None, type="all_browser"):
        """
        清理自己创建的内容
        一个是单进程结束后关闭进程，另一个是程序结束后清除所有内容
        """
        pass

    @abstractmethod
    def get_thread_page(self):
        """
        返回页面
        """

    def get_thread_browser(self):
        """
        通用逻辑:获取不同进程的浏览器
        """
        if not hasattr(self.thread_local, "browser"):
            p, b = self.launch_browser()
            if not p:
                return False

            self.thread_local.playwright = p
            self.thread_local.browser = b

            with self.cleanup_lock:
                self.active_resources.append((p, b))

        return self.thread_local.browser

    def outlook_register(self, page, email, password):
        """
        通用逻辑:注册邮箱
        """

        self.reset_last_pos()
        fake = Faker()

        lastname = fake.last_name()
        firstname = fake.first_name()
        year = str(random.randint(1960, 2005))
        month = str(random.randint(1, 12))
        day = str(random.randint(1, 28))

        try:
            page.goto("https://outlook.live.com/mail/0/?prompt=create_account", timeout=30000, wait_until="domcontentloaded")
            consent_btn = page.get_by_text('同意并继续')
            consent_btn.wait_for(timeout=60000)
            start_time = time.time()
            self.wait_random_ratio(page, 0.06)
            self.smooth_click(page, consent_btn)
        except Exception:
            print("[Error: IP] - IP质量不佳，无法进入注册界面。")
            return False

        try:
            if self.email_suffix == "@hotmail.com":
                self.wait_random_ratio(page, 0.06)
                domain_btn = page.get_by_text("@outlook.com")
                self.smooth_click(page, domain_btn)
                option_btn = page.locator(f'[role="option"]:text-is("@hotmail.com")')
                self.smooth_click(page, option_btn)


            email_input = page.locator('[aria-label="新建电子邮件"]')
            self.smooth_type(page, email_input, email)

            primary_btn = page.locator('[data-testid="primaryButton"]')
            self.smooth_click(page, primary_btn)
            self.wait_random_ratio(page, 0.04)

            pwd_input = page.locator('[type="password"]')
            self.smooth_type(page, pwd_input, password)
            self.wait_random_ratio(page, 0.03)
            self.smooth_click(page, primary_btn)
            self.wait_random_ratio(page, 0.03)

            if page.get_by_text("请重试。如果仍然不起作用，请稍后再试。").count() > 0:
                print("[Error: IP or browser] - 当前IP注册频率过快。检查IP与是否为指纹浏览器并关闭了无头模式。")
                return False

            year_input = page.locator('[name="BirthYear"]')
            if year_input.count() > 0:
                self.smooth_click(page, year_input)
                year_input.fill(year)

            month_btn = page.locator('[name="BirthMonth"]')
            self.smooth_click(page, month_btn)
            self.wait_random_ratio(page, 0.03)
            m_opt = page.locator(f'[role="option"]:text-is("{month}月")')
            self.smooth_click(page, m_opt)

            self.wait_random_ratio(page, 0.03)
            day_btn = page.locator('[name="BirthDay"]')
            self.smooth_click(page, day_btn)
            self.wait_random_ratio(page, 0.03)

            d_opt = page.locator(f'[role="option"]:text-is("{day}日")')
            if d_opt.count() > 0:
                try:
                    d_opt.scroll_into_view_if_needed()
                except Exception:
                    pass
            self.smooth_click(page, d_opt)

            self.smooth_click(page, primary_btn)

            lname_input = page.locator('#lastNameInput')
            lname_input.wait_for(state='visible', timeout=8000)
            self.smooth_type(page, lname_input, lastname)

            self.wait_random_ratio(page, 0.02)
            fname_input = page.locator('#firstNameInput')
            fname_input.wait_for(state='visible', timeout=8000)
            self.smooth_type(page, fname_input, firstname)

            if time.time() - start_time < self.wait_time / 1000:
                page.wait_for_timeout(self.wait_time - (time.time() - start_time) * 1000)

            self.smooth_click(page, primary_btn)
            page.locator('span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]').wait_for(state='detached', timeout=22000)
            page.wait_for_timeout(400)

            if page.get_by_text('一些异常活动').count() or page.get_by_text('此站点正在维护，暂时无法使用，请稍后重试。').count() > 0:
                print("[Error: IP or browser] - 当前IP注册频率过快。检查IP与是否为指纹浏览器并关闭了无头模式。")
                return False

            if page.locator('iframe#enforcementFrame').count() > 0:
                print("[Error: FunCaptcha] - 验证码类型错误，非按压验证码。")
                return False

            captcha_result = self.handle_captcha(page)
            if not captcha_result:
                raise TimeoutError

        except Exception:
            print("[Error: IP] - 加载超时或因触发机器人检测导致按压次数达到最大仍未通过。")
            return False

        filename = os.path.join(self.results_dir, 'logged_email.txt' if self.enable_oauth2 else 'unlogged_email.txt')
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(f"{email}{self.email_suffix}: {password}\n")
        print(f'[Success: Email Registration] - {email}{self.email_suffix}: {password}')

        # 辅助邮箱验证：注册成功后 Outlook 会要求添加辅助邮箱并发送验证码，
        # 用域名邮箱接收并回填（可通过 domain_mail_config.json 的 enable_recovery_email 关闭）
        if handle_recovery_email is not None and recovery_enabled():
            try:
                handle_recovery_email(page, f"{email}{self.email_suffix}")
            except Exception as e:
                print(f"[Recovery] 辅助邮箱步骤异常（不影响注册结果）: {e}")

        if not self.enable_oauth2:
            return True

        start_skip_time = time.time()
        while time.time() - start_skip_time < 20:
            try:
                btn_skip = page.get_by_text("暂时跳过")
                if btn_skip.count() > 0 and btn_skip.is_visible():
                    self.smooth_click(page, btn_skip)
                    page.wait_for_timeout(random.randint(1000, 1500))
                else:
                    btn_skip.wait_for(timeout=7000)
            except Exception:
                break

        try:
            page.locator('[aria-label="新邮件"]').wait_for(timeout=32000)
            return True
        except Exception:
            print('[Error: Timeout] - 邮箱未初始化，无法正常收件。')
            return False