"""Outlook 辅助邮箱（恢复邮箱）验证步骤。

注册成功后 Outlook 的"保护你的账户"流程会要求添加一个辅助邮箱并发送验证码。
本模块：
1. 通过域名邮箱服务创建随机域名邮箱（如 tmpxxxxx@tmp7k2x.top）
2. 在 Outlook 页面填入该域名邮箱作为辅助邮箱
3. 轮询域名邮箱收件箱，提取 Outlook 发来的验证码
4. 回填验证码完成验证
5. 把 outlook 邮箱 ↔ 域名邮箱的对应关系写入 Results/email_mappings.jsonl + .csv

UI 选择器是 best-effort（Outlook 页面会变），可用配置项覆盖：
    "recovery_email_selectors": {
        "add_email_option": [...],   // 页面上"添加电子邮件地址"之类的选项
        "input": [...],              // 辅助邮箱输入框
        "next": [...],               // "下一步/发送验证码"按钮
        "code_input": [...],         // 验证码输入框
        "verify": [...],             // "验证/完成"按钮
        "skip": [...]                // "暂时跳过"按钮（失败兜底）
    }
任何一步失败都优雅降级：不阻塞注册主流程。
"""
from __future__ import annotations

import json
import os
import time

from domain_mail_client import (
    DomainMailError,
    append_mapping,
    create_address,
    load_config,
    random_local_part,
    wait_for_code,
)

DEFAULT_SELECTORS = {
    "add_email_option": [
        "#iProofOptions [role='radio']:has-text('电子邮件')",
        "#iProofsContainer :has-text('电子邮件')",
        "#iProofOptions :has-text('邮件')",
        "text=添加电子邮件地址",
        "text=使用电子邮件地址",
        "text=添加邮箱",
        "text=电子邮件地址",
        "text=Add an email address",
        "text=Use an email address",
        "text=Email",
    ],
    "input": [
        "#EmailAddress",
        'input#EmailAddress',
        'input[name="EmailAddress"]',
        '#iProofEmail',
        'input[placeholder*="example.com"]',
        'input[placeholder*="电子邮件"]',
        'input[autocomplete="off"][type="text"]',
        'input[type="email"]',
        'input[autocomplete="email"]',
        'input[aria-label*="邮箱"]',
        'input[aria-label*="电子邮件"]',
    ],
    "next": [
        "#iNext",
        '#idSIButton9',
        '[data-testid="primaryButton"]',
        'button[type="submit"]',
        "text=发送验证码",
        "text=下一步",
        "text=Next",
        "text=Send code",
    ],
    "code_input": [
        "#iProofCode",
        'input[name="otc"]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[aria-label*="验证码"]',
        'input[aria-label*="代码"]',
    ],
    "verify": [
        "#iNext",
        '[data-testid="primaryButton"]',
        'button[type="submit"]',
        "text=验证",
        "text=完成",
        "text=Verify",
        "text=Done",
    ],
    "skip": [
        "text=暂时跳过",
        "text=Skip for now",
        "text=以后再说",
        "#iSkip",
    ],
}


SELECTORS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recovery_selectors.json")


def _effective_selectors() -> dict:
    """DEFAULT_SELECTORS + recovery_selectors.json 覆盖（每次调用都重读，支持运行中热更新）"""
    merged = DEFAULT_SELECTORS
    try:
        if os.path.exists(SELECTORS_FILE):
            with open(SELECTORS_FILE, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                merged = {
                    **DEFAULT_SELECTORS,
                    **{k: v for k, v in overrides.items() if isinstance(v, list)},
                }
    except Exception:
        pass
    return merged


def _selector(cfg: dict, key: str) -> list:
    sel = (cfg.get("recovery_email_selectors") or {}).get(key)
    if sel:
        return sel
    return _effective_selectors().get(key, [])


def _click_first(page, selectors: list[str], timeout_ms: int = 3000) -> bool:
    """依次尝试选择器，点击第一个可见的；返回是否点中。"""
    import random
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                # 复用控制器的平滑点击风格，但这里不依赖 controller 引用
                loc.scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(random.randint(200, 500))
                loc.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _fill_first(page, selectors: list[str], text: str) -> bool:
    """依次尝试选择器，向第一个可见输入框填值。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.fill(text, timeout=3000)
                return True
        except Exception:
            continue
    return False


def _dump_page(page, name: str) -> None:
    """DEBUG: 把当前页面文本导出到 Results/<name>，便于排查 UI 状态。"""
    try:
        txt = page.text_content("body", timeout=5000)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Results", name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt or "")
        print(f"[Debug] 已导出页面文本到 Results/{name}")
    except Exception as e:
        print(f"[Debug] 导出页面文本失败: {e}")


def _record(outlook_email: str, status: str, domain_email: str = "",
            jwt: str = "", code: str = "", note: str = "") -> None:
    append_mapping({
        "outlook_email": outlook_email,
        "domain_email": domain_email,
        "jwt": jwt,
        "status": status,
        "code": code,
        "note": note,
    })
    print(f"[Recovery] {outlook_email} -> {domain_email or '-'} [{status}] {note}".rstrip())


def _resume_loop(page, cfg: dict, outlook_email: str, phase_name: str,
                 phase_callable, deadline: float) -> bool:
    """阶段失败后保持浏览器打开，等待人工修复选择器（recovery_selectors.json）后自动重试。

    每 15s 重读选择器文件并重试当前阶段，直到成功或 deadline 到期。
    """
    _dump_page(page, "recovery_debug.txt")
    remain_min = max(0, int((deadline - time.time()) // 60))
    print(f"[Recovery] ⏸ 阶段「{phase_name}」失败 —— 浏览器保持打开，等待选择器修复")
    print(f"[Recovery] ⏸ 可编辑 {SELECTORS_FILE}（每 15s 自动重载重试），剩余约 {remain_min} 分钟")
    while time.time() < deadline:
        time.sleep(15)
        try:
            if phase_callable():
                print(f"[Recovery] ✅ 阶段「{phase_name}」修复后重试成功，继续")
                return True
        except Exception as e:
            print(f"[Recovery] 重试异常: {e}")
        _dump_page(page, "recovery_debug.txt")
    return False


def handle_recovery_email(page, outlook_email: str, cfg: dict | None = None) -> bool:
    """主入口：Outlook 注册成功后尝试辅助邮箱验证。

    返回 True=验证完成 / False=跳过或失败（不抛异常，不阻塞注册）。
    任何阶段失败都不会立刻关闭浏览器：进入修复等待（最多 15 分钟），
    编辑 recovery_selectors.json 后自动重试同一页面，不重新注册、不浪费 IP。
    """
    cfg = cfg or load_config()
    if not (cfg.get("api_base") and cfg.get("domain")):
        print("[Recovery] 未配置 domain_mail_config.json，跳过辅助邮箱验证")
        _record(outlook_email, "skipped", note="未配置域名邮箱服务")
        return False
    # 验证码等待超时收紧到 120s（Outlook 一般 10s 内送达，慢轮询下 240s 太耗时）
    if not cfg.get("mail_timeout"):
        cfg = dict(cfg, mail_timeout=120)

    # ── 1. 创建随机域名邮箱 ──
    try:
        created = create_address(cfg, name=random_local_part(cfg.get("recovery_email_prefix", "tmp")))
    except DomainMailError as e:
        print(f"[Recovery] 创建域名邮箱失败: {e}")
        _record(outlook_email, "failed", note=f"创建域名邮箱失败: {e}")
        return False
    domain_email = created["address"]
    jwt = created.get("jwt", "")
    _record(outlook_email, "created", domain_email=domain_email, jwt=jwt)
    print(f"[Recovery] 已创建域名邮箱 {domain_email}")

    deadline = time.time() + 900  # 15 分钟人工修复窗口

    # ── 2. 填写辅助邮箱 + 发送验证码（失败可修复重试） ──
    def phase_fill() -> bool:
        return _try_fill_address(page, cfg, domain_email)

    try:
        if not phase_fill():
            if not _resume_loop(page, cfg, outlook_email, "填写辅助邮箱/发送验证码", phase_fill, deadline):
                _try_skip(page, cfg)
                _record(outlook_email, "failed", domain_email=domain_email,
                        jwt=jwt, note="填写辅助邮箱失败(修复超时)")
                return False
    except Exception as e:
        print(f"[Recovery] 填写辅助邮箱异常: {e}")
        _try_skip(page, cfg)
        _record(outlook_email, "failed", domain_email=domain_email,
                jwt=jwt, note=f"填写辅助邮箱异常: {e}")
        return False

    # ── 3. 轮询验证码（超时进入修复等待，页面仍开着） ──
    state = {"code": None, "mail": None}

    def progress(msg: str) -> None:
        print(msg)

    def phase_poll() -> bool:
        code, mail = wait_for_code(cfg, jwt, domain_email, timeout=90, progress=progress)
        if code:
            state["code"], state["mail"] = code, mail
            return True
        # 90s 内没到：可能发送没真正触发，检查页面是否还停在邮箱表单
        try:
            if _email_field_visible(page):
                print("[Recovery] 邮箱表单仍可见，重新尝试填写+发送")
                _try_fill_address(page, cfg, domain_email)
        except Exception:
            pass
        return False

    if not phase_poll():
        if not _resume_loop(page, cfg, outlook_email, "等待验证码邮件", phase_poll, deadline):
            _try_skip(page, cfg)
            _record(outlook_email, "failed", domain_email=domain_email,
                    jwt=jwt, note="等待验证码超时")
            return False
    code = state["code"]
    mail = state["mail"]
    _record(outlook_email, "code_received", domain_email=domain_email,
            jwt=jwt, code=code, note=f"发件人 {mail.get('sender', '') if mail else ''}")
    print(f"[Recovery] 收到验证码: {code}")

    # ── 4. 回填验证码（失败可修复重试） ──
    def phase_verify() -> bool:
        return _try_verify(page, cfg, code)

    try:
        if not phase_verify():
            if not _resume_loop(page, cfg, outlook_email, "回填验证码", phase_verify, deadline):
                _try_skip(page, cfg)
                _record(outlook_email, "failed", domain_email=domain_email,
                        jwt=jwt, code=code, note="回填验证码失败(修复超时)")
                return False
    except Exception as e:
        print(f"[Recovery] 回填验证码异常: {e}")
        _try_skip(page, cfg)
        _record(outlook_email, "failed", domain_email=domain_email,
                jwt=jwt, code=code, note=f"回填验证码异常: {e}")
        return False

    _record(outlook_email, "verified", domain_email=domain_email,
            jwt=jwt, code=code, note="辅助邮箱验证完成")
    return True


def _page_advanced(page, cfg: dict) -> bool:
    """点击"下一步/发送验证码"后，验证页面是否真的前进了。

    判据：出现验证码输入框，或页面出现"已发送/发送至"类提示文本。
    """
    try:
        for sel in _selector(cfg, "code_input"):
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        txt = page.text_content("body", timeout=3000) or ""
        for marker in ("验证码已发送", "已发送验证码", "验证码发送", "已发送到", "发送至",
                       "验证码已发送到", "已发送", "发送了", "安全代码",
                       "输入此代码", "输入代码", "请输入代码",
                       "code sent", "sent to", "we sent", "we've sent", "has been sent"):
            if marker in txt:
                return True
    except Exception:
        pass
    return False


def _click_send_and_verify(page, cfg: dict) -> bool:
    """依次尝试"下一步/发送验证码"按钮，点完必须验证页面前进才算成功。

    之前的教训：通用选择器（如 [data-testid="primaryButton"]）可能点到
    页面上的诱饵/无关按钮，print 显示"已点击"但邮件根本没发出去。
    """
    import random
    for sel in _selector(cfg, "next"):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            box = loc.bounding_box()
            if not box or box["width"] <= 5 or box["height"] <= 5:
                continue  # 跳过零尺寸/隐形按钮
            loc.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(random.randint(200, 500))
            loc.click(timeout=4000)
            page.wait_for_timeout(2500)
            if _page_advanced(page, cfg):
                print(f"[Recovery] 已点击发送验证码（{sel}），页面已前进")
                return True
            print(f"[Recovery] 点击 {sel} 后页面未前进，尝试下一个按钮")
        except Exception:
            continue
    return False


def _email_field_visible(page) -> bool:
    """邮箱输入框是否可见（AddProof 页可能需要先选中"电子邮件"选项）"""
    try:
        loc = page.locator("#EmailAddress, input[type='email']").first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def _try_fill_address(page, cfg: dict, domain_email: str) -> bool:
    """尝试进入"添加辅助邮箱"并填入地址。"""
    for attempt in range(4):
        # 2a. 邮箱输入框不可见时，先点"电子邮件"验证方式选项
        if not _email_field_visible(page):
            clicked = _click_first(page, _selector(cfg, "add_email_option"), timeout_ms=3000)
            if clicked:
                print("[Recovery] 已点击'电子邮件'验证方式选项")
                page.wait_for_timeout(1500)

        # 2b. 填入邮箱（首选 #EmailAddress 精准定位，避免填错字段）
        if _fill_first(page, _selector(cfg, "input"), domain_email):
            print(f"[Recovery] 已填入辅助邮箱 {domain_email}")
            page.wait_for_timeout(800)
            # 2c. 点"下一步/发送验证码"并验证页面前进
            if _click_send_and_verify(page, cfg):
                # 关键节点快照
                try:
                    txt = page.text_content("body", timeout=4000) or ""
                    txt = " ".join(txt.split())
                    print(f"[Recovery] 发送后页面: {txt[:220]}")
                    _dump_page(page, "recovery_step.txt")
                except Exception:
                    pass
                return True
            # 没找到有效按钮：可能是输入框还没就绪，重试
        page.wait_for_timeout(1000)
    return False


def _try_verify(page, cfg: dict, code: str) -> bool:
    for attempt in range(3):
        if _fill_first(page, _selector(cfg, "code_input"), code):
            page.wait_for_timeout(600)
            if _click_first(page, _selector(cfg, "verify"), timeout_ms=5000):
                page.wait_for_timeout(2000)
                return True
        page.wait_for_timeout(1000)
    return False


def _try_skip(page, cfg: dict) -> None:
    """失败兜底：尝试点"暂时跳过"，别把页面卡住。"""
    try:
        _click_first(page, _selector(cfg, "skip"), timeout_ms=2000)
    except Exception:
        pass
