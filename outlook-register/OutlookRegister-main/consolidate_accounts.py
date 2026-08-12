"""账号统一整理脚本：把各种格式的账号记录合并为 accounts.txt。

统一格式（每行一个账号，4 段，---- 分隔）：
    email----password----client_id----refresh_token

输入源（Results/ 下，缺哪个就跳过哪个）：
    verified_tokens.txt  已是 4 段格式
    outlook_token.txt    5 段格式：email---password---refresh---access---expire
    logged_email.txt     2 段格式：email: password

去重规则：按邮箱去重；同一邮箱多条记录时，优先保留带 refresh_token 的。

用法：
    python consolidate_accounts.py            # 输出到 Results/accounts.txt
    python consolidate_accounts.py --out x.txt
"""
from __future__ import annotations

import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(BASE, "Results")


def _client_id() -> str:
    try:
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("oauth2", {}).get("client_id", "")
    except Exception:
        return ""


def _read_lines(name: str) -> list[str]:
    path = os.path.join(RESULTS, name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [ln.strip() for ln in f if ln.strip()]


def parse_verified(line: str) -> tuple[str, str, str, str]:
    """email----password----client_id----refresh_token"""
    parts = line.split("----", 3)
    if len(parts) < 4:
        return "", "", "", ""
    return parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()


def parse_outlook_token(line: str, default_client_id: str) -> tuple[str, str, str, str]:
    """email---password---refresh---access---expire"""
    parts = line.split("---", 4)
    if len(parts) < 3:
        return "", "", "", ""
    return parts[0].strip(), parts[1].strip(), default_client_id, parts[2].strip()


def parse_logged(line: str, default_client_id: str) -> tuple[str, str, str, str]:
    """email: password"""
    if ":" not in line:
        return "", "", "", ""
    email, password = line.split(":", 1)
    return email.strip(), password.strip(), default_client_id, ""


def main():
    ap = argparse.ArgumentParser(description="账号统一整理为 email----password----client_id----refresh_token")
    ap.add_argument("--out", default=os.path.join(RESULTS, "accounts.txt"))
    args = ap.parse_args()

    default_client_id = _client_id()
    accounts: dict[str, tuple[str, str, str]] = {}  # email -> (password, client_id, refresh)

    def add(email: str, password: str, client_id: str, refresh: str) -> None:
        if not email or not password:
            return
        if email not in accounts or (refresh and not accounts[email][2]):
            accounts[email] = (password, client_id or default_client_id, refresh)

    for line in _read_lines("verified_tokens.txt"):
        add(*parse_verified(line))
    for line in _read_lines("outlook_token.txt"):
        add(*parse_outlook_token(line, default_client_id))
    for line in _read_lines("logged_email.txt"):
        add(*parse_logged(line, default_client_id))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for email in sorted(accounts):
            password, client_id, refresh = accounts[email]
            f.write(f"{email}----{password}----{client_id}----{refresh}\n")

    with_token = sum(1 for v in accounts.values() if v[2])
    print(f"✅ 已整理 {len(accounts)} 个账号 -> {args.out}")
    print(f"   其中 {with_token} 个带 refresh_token，{len(accounts) - with_token} 个无 token（仅 email:password）")


if __name__ == "__main__":
    main()
