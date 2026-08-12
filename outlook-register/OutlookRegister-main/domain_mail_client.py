"""域名邮箱接收器 HTTP 客户端。

对接线上部署的域名邮箱服务（https://mail.tmp7k2x.top，即 cf_temp_email worker）：
- POST /api/new_address            创建随机域名邮箱 → {jwt, address, password, address_id}
- GET  /api/parsed_mails?limit=&offset=  拉取邮件（服务端已解析，含 subject/text）
- GET  /api/mails?limit=&offset=   拉取原始邮件（兜底）

兼容两套部署：
- 默认走公开创建接口 /api/new_address（无需鉴权）
- 若配置 create_path=/admin/new_address 且填了 admin_key，则自动带 x-admin-auth 头
  （domain_mail_receiver 仓库里的 worker 是这种鉴权方式）

验证码从 subject/text 用正则提取（中英文微软验证邮件）。
配置加载顺序：domain_mail_config.json（必填）→ domain_mail_config.local.json（可选覆盖）。
本模块只依赖 requests，不修改任何收件服务项目的代码。
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import string
import threading
import time

import requests

CONFIG_FILES = ("domain_mail_config.json", "domain_mail_config.local.json")

DEFAULT_CONFIG = {
    "api_base": "",
    "domain": "",
    "create_path": "/api/new_address",
    "admin_key": "",
    "enable_recovery_email": True,      # 注册成功后是否尝试辅助邮箱验证
    "mail_poll_interval": 5,            # 轮询收件箱间隔（秒）
    "mail_timeout": 240,                # 等待验证码总超时（秒）
}

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Results")
_MAPPINGS_JSONL = os.path.join(_RESULTS_DIR, "email_mappings.jsonl")
_MAPPINGS_CSV = os.path.join(_RESULTS_DIR, "email_mappings.csv")
_mapping_lock = threading.Lock()

_CODE_PATTERNS = [
    # 中文：一次性/安全/动态/验证 代码 后跟数字（微软真实邮件："你的一次性代码为: 913562"）
    re.compile(r"(?:一次性|安全|动态|验证)代码[^\d]{0,8}(\d{4,8})"),
    re.compile(r"(\d{4,8})[^\d]{0,8}(?:一次性|安全|动态|验证)代码"),
    # 中文：验证码是 123456 / 验证码：123456 / 验证码 123456
    re.compile(r"验证码[^\d]{0,8}(\d{4,8})"),
    re.compile(r"(\d{4,8})[^\d]{0,8}验证码"),
    # 英文：verification/security/confirmation/one-time code is 12345
    re.compile(r"(?:verification|security|confirmation|one[- ]?time)\s+code[^\d]{0,12}(\d{4,8})", re.I),
    re.compile(r"\bcode[^\d]{0,6}[:： ](\d{4,8})", re.I),
    # 英文："Use this code to finish signing in: 913562"
    re.compile(r"use this code[^\d]{0,32}(\d{4,8})", re.I),
]


class DomainMailError(Exception):
    """域名邮箱服务调用失败"""


def normalize_proxy(value) -> str:
    """把代理字符串统一成 http://user:pass@host:port 或 host:port 格式。

    兼容 cliproxy 等四段式格式：
        us2.cliproxy.io:3010:user:pass  ->  http://user:pass@us2.cliproxy.io:3010
    已是标准格式（含 ://）或纯 host:port 时原样返回。
    """
    s = str(value or "").strip()
    if not s or "://" in s:
        return s
    parts = s.split(":")
    if len(parts) == 4 and all(parts):
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    return s


# ────────────────────────── 配置 ──────────────────────────


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    base = os.path.dirname(os.path.abspath(__file__))
    for name in CONFIG_FILES:
        path = os.path.join(base, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    return cfg


def recovery_enabled(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("enable_recovery_email") and cfg.get("api_base") and cfg.get("domain"))


def random_local_part(prefix: str = "tmp", length: int = 10) -> str:
    """生成随机邮箱本地部分（服务端还会自动加前缀，这里仅作兜底）"""
    chars = string.ascii_lowercase + string.digits
    return prefix + "".join(random.choice(chars) for _ in range(length))


# ────────────────────────── API 调用 ──────────────────────────


def _headers(cfg: dict) -> dict:
    headers = {}
    if cfg.get("admin_key"):
        headers["x-admin-auth"] = cfg["admin_key"]
    return headers


def create_address(cfg: dict | None = None, name: str | None = None) -> dict:
    """创建随机域名邮箱，返回 {address, jwt, ...}"""
    cfg = cfg or load_config()
    api_base = str(cfg.get("api_base") or "").rstrip("/")
    if not api_base:
        raise DomainMailError("domain_mail_config.json 的 api_base 为空，请先配置")

    create_path = str(cfg.get("create_path") or "/api/new_address")
    payload: dict = {"domain": cfg.get("domain") or ""}
    if name:
        payload["name"] = name

    resp = requests.post(api_base + create_path, json=payload,
                         headers=_headers(cfg), timeout=30)
    if resp.status_code != 200:
        raise DomainMailError(
            f"创建域名邮箱失败 HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if not isinstance(data, dict) or "address" not in data or "jwt" not in data:
        raise DomainMailError(f"创建域名邮箱响应异常: {data}")
    return data


def list_mails(cfg: dict, jwt: str, limit: int = 20, offset: int = 0,
               parsed: bool = True) -> list[dict]:
    """拉取邮件列表。

    parsed=True 用 /api/parsed_mails（服务端已解析 subject/text，推荐）；
    parsed=False 用 /api/mails（原始邮件，含 raw 字段）。
    """
    api_base = str(cfg.get("api_base") or "").rstrip("/")
    path = "/api/parsed_mails" if parsed else "/api/mails"
    resp = requests.get(
        f"{api_base}{path}",
        params={"limit": limit, "offset": offset},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=12,
    )
    if resp.status_code != 200:
        raise DomainMailError(f"拉取邮件失败 HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data.get("results", []) if isinstance(data, dict) else []


def extract_verification_code(subject: str, text: str) -> str | None:
    """从主题/正文中提取验证码（中英文模式）。"""
    subject = subject or ""
    text = text or ""
    for pattern in _CODE_PATTERNS:
        for haystack in (subject, text):
            m = pattern.search(haystack)
            if m:
                return m.group(1)
    return None


def wait_for_code(cfg: dict, jwt: str, domain_email: str,
                  timeout: int | None = None, progress=None) -> tuple[str | None, dict | None]:
    """轮询收件箱直到提取到验证码。

    返回 (code, mail)。progress 是可选回调（用于打日志）。
    """
    timeout = timeout if timeout is not None else int(cfg.get("mail_timeout", 240))
    interval = max(2, int(cfg.get("mail_poll_interval", 5)))
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            mails = list_mails(cfg, jwt, parsed=True)
        except DomainMailError as e:
            if progress:
                progress(f"[Recovery] 拉取邮件失败，重试中: {e}")
            time.sleep(interval)
            continue
        for mail in mails:
            code = extract_verification_code(mail.get("subject", ""), mail.get("text", ""))
            if code:
                return code, mail
        if progress:
            remain = max(0, int(deadline - time.time()))
            progress(f"[Recovery] 等待验证码邮件... 剩余 {remain}s")
        time.sleep(interval)
    return None, None


# ────────────────────────── 映射记录（落盘） ──────────────────────────

# 记录字段：ts / outlook_email / domain_email / jwt / status / code / note
# status: created=已创建域名邮箱  code_received=已收到验证码
#         verified=验证完成      skipped=跳过      failed=失败


def mappings_jsonl_path() -> str:
    return _MAPPINGS_JSONL


def append_mapping(record: dict) -> None:
    """追加一条映射事件到 jsonl，并重建 csv 快照（加锁防并发写坏）。"""
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    rec = dict(record)
    rec.setdefault("ts", int(time.time()))
    with _mapping_lock:
        with open(_MAPPINGS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _rewrite_csv_locked()


def _rewrite_csv_locked() -> None:
    """从 jsonl 重建 csv：每个 outlook 邮箱一行，取最新状态。"""
    try:
        latest: dict[str, dict] = {}
        with open(_MAPPINGS_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("outlook_email") or rec.get("domain_email") or ""
                if key:
                    latest[key] = rec
        tmp = _MAPPINGS_CSV + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["时间", "Outlook邮箱", "域名邮箱", "状态", "验证码", "备注"])
            for rec in latest.values():
                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.get("ts", 0))),
                    rec.get("outlook_email", ""),
                    rec.get("domain_email", ""),
                    rec.get("status", ""),
                    rec.get("code", ""),
                    rec.get("note", ""),
                ])
        os.replace(tmp, _MAPPINGS_CSV)
    except Exception:
        pass  # csv 只是给人工看的快照，坏了不影响主流程


def load_mappings() -> list[dict]:
    """读取全部映射事件（jsonl），时间正序。"""
    out = []
    if not os.path.exists(_MAPPINGS_JSONL):
        return out
    with open(_MAPPINGS_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
