"""OutlookRegister 可视化面板（FastAPI）。

功能：
- 映射可视化：outlook 邮箱 ↔ 域名邮箱（来自 Results/email_mappings.jsonl）
- 实时日志：滚动展示 Results/register_run.log
- 统计卡片 + 一键启动/停止注册流程

启动：
    python web_dashboard.py            # 默认 127.0.0.1:8766
    python web_dashboard.py --port 9000 --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(BASE, "Results")
LOG_FILE = os.path.join(RESULTS, "register_run.log")
MAPPINGS_FILE = os.path.join(RESULTS, "email_mappings.jsonl")
STATIC_DIR = os.path.join(BASE, "static")
PROXIES_FILE = os.path.join(BASE, "proxies.txt")

sys.path.insert(0, BASE)
from domain_mail_client import load_config, load_mappings, normalize_proxy  # noqa: E402

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="OutlookRegister Dashboard", docs_url=None, redoc_url=None)


class ProxiesReq(BaseModel):
    text: str = ""
    via_clash: bool = False

_run_proc: subprocess.Popen | None = None
_run_lock = threading.Lock()

STATUS_LABELS = {
    "created": "Mailbox Created",
    "code_received": "Code Received",
    "verified": "Verified",
    "skipped": "Skipped",
    "failed": "Failed",
}


def _latest_per_outlook(events: list[dict]) -> list[dict]:
    """每个 outlook 邮箱取最新一条事件，按时间倒序。"""
    latest: dict[str, dict] = {}
    for ev in events:
        key = ev.get("outlook_email") or ev.get("domain_email") or ""
        if key:
            latest[key] = ev
    items = sorted(latest.values(), key=lambda e: e.get("ts", 0), reverse=True)
    for ev in items:
        ev["status_label"] = STATUS_LABELS.get(ev.get("status", ""), ev.get("status", ""))
        ev["time_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.get("ts", 0)))
    return items


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


# ──────────────────────── 代理池管理 ────────────────────────


def _parse_proxy_text(text: str) -> list[str]:
    """解析文本框内容：去空行/注释/重复，四段式 host:port:user:pass 自动转标准格式"""
    seen, out = set(), []
    for raw in (text or "").splitlines():
        line = normalize_proxy(raw.strip())
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _read_proxies_txt() -> list[str]:
    if not os.path.exists(PROXIES_FILE):
        return []
    with open(PROXIES_FILE, "r", encoding="utf-8") as f:
        return _parse_proxy_text(f.read())


def _tool_config() -> dict:
    """读工具自身的 config.json（代理等注册配置）"""
    path = os.path.join(BASE, "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


@app.get("/api/proxies")
def api_proxies():
    """返回代理池：txt（面板可编辑的 proxies.txt）+ cfg（config.json 的 proxies，只读）"""
    cfg = _tool_config()
    cfg_list = cfg.get("proxies") or []
    if isinstance(cfg_list, str):
        cfg_list = [cfg_list]
    return {
        "ok": True,
        "txt": _read_proxies_txt(),
        "cfg": [str(p).strip() for p in cfg_list if str(p).strip()],
        "single": cfg.get("proxy", ""),
    }


@app.post("/api/proxies")
def api_proxies_save(req: ProxiesReq):
    """保存代理池（覆盖 proxies.txt，自动去重）"""
    lines = _parse_proxy_text(req.text)
    with open(PROXIES_FILE, "w", encoding="utf-8") as f:
        f.write("# 代理池：每行一个代理，支持 http://host:port 或 http://user:pass@host:port\n")
        f.write("# 由 Web 面板保存（自动去重）。出口 IP 被 Outlook 标记后换一个代理即可。\n")
        for line in lines:
            f.write(line + "\n")
    return {"ok": True, "count": len(lines), "txt": lines}


def _test_proxy_via_clash(proxy: str, timeout: float = 18) -> dict:
    """经 Clash 隧道连接目标代理，发认证请求取出口 IP。

    用于 cliproxy 等拒绝直连来源（来源 IP 白名单/地区限制）的代理：
    隧道让代理看到的来源 IP 变成 Clash 节点出口。
    """
    from urllib.parse import unquote, urlsplit
    try:
        p = urlsplit(proxy if "://" in proxy else "http://" + proxy)
        host = p.hostname
        port = p.port or 80
        username = unquote(p.username or "")
        password = unquote(p.password or "")
        if not host:
            return {"proxy": proxy, "ok": False, "ip": "", "error": "bad proxy url"}

        s = socket.create_connection(("127.0.0.1", 7897), timeout=timeout)
        s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        s.settimeout(timeout)
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = s.recv(4096)
            if not c:
                break
            buf += c
        if b" 200" not in buf.split(b"\r\n", 1)[0]:
            s.close()
            return {"proxy": proxy, "ok": False, "ip": "", "error": "clash tunnel failed"}

        auth = ""
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth = f"Proxy-Authorization: Basic {token}\r\n"
        s.sendall(f"GET http://api.ipify.org/ HTTP/1.1\r\nHost: api.ipify.org\r\n"
                  f"{auth}Connection: close\r\n\r\n".encode())
        data = b""
        try:
            while True:
                c = s.recv(4096)
                if not c:
                    break
                data += c
        except socket.timeout:
            pass
        s.close()
        if not data:
            return {"proxy": proxy, "ok": False, "ip": "", "error": "no response"}
        head, _, body = data.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 200" not in status:
            return {"proxy": proxy, "ok": False, "ip": "",
                    "error": status.decode(errors="replace")[:80]}
        ip = body.decode(errors="replace").strip().splitlines()[-1] if body else ""
        return {"proxy": proxy, "ok": True, "ip": ip}
    except Exception as e:
        return {"proxy": proxy, "ok": False, "ip": "", "error": str(e)[:80]}


@app.post("/api/proxies/test")
def api_proxies_test(req: ProxiesReq | None = None):
    """连通性测试：显示每个代理的出口 IP（可测文本框里还没保存的）。

    via_clash=true 时经 Clash 隧道测试（来源 IP = Clash 节点出口），
    用于 cliproxy 等拒绝直连来源的代理。
    """
    import concurrent.futures as _cf
    import requests as _requests

    pool = _parse_proxy_text(req.text) if (req and req.text) else _read_proxies_txt()
    via_clash = bool(req and req.via_clash)

    def _test_one(proxy: str) -> dict:
        try:
            r = _requests.get(
                "https://api.ipify.org",
                proxies={"http": proxy, "https": proxy},
                timeout=8,
            )
            return {"proxy": proxy, "ok": r.status_code == 200, "ip": (r.text or "").strip()[:45]}
        except Exception as e:
            return {"proxy": proxy, "ok": False, "ip": "", "error": str(e)[:80]}

    results = []
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        if via_clash:
            futures = [ex.submit(_test_proxy_via_clash, p) for p in pool[:50]]
        else:
            futures = [ex.submit(_test_one, p) for p in pool[:50]]
        for fut in futures:
            results.append(fut.result())
    return {"ok": True, "results": results}


@app.get("/api/mappings")
def api_mappings():
    events = load_mappings()
    return {
        "ok": True,
        "items": _latest_per_outlook(events),
        "total": len(events),
        "outlook_count": len({e.get("outlook_email") for e in events if e.get("outlook_email")}),
    }


@app.get("/api/log")
def api_log(since: int = 0):
    """增量返回日志：客户端传上次的 size，返回新行 + 最新 size。"""
    if not os.path.exists(LOG_FILE):
        return {"ok": True, "lines": [], "size": 0}
    size = os.path.getsize(LOG_FILE)
    if since > size:  # 文件被截断/轮转，从头开始
        since = 0
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        f.seek(since)
        chunk = f.read()
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    return {"ok": True, "lines": lines, "size": size}


@app.get("/api/stats")
def api_stats():
    events = load_mappings()
    by_status: dict[str, int] = {}
    for ev in events:
        s = ev.get("status", "")
        by_status[s] = by_status.get(s, 0) + 1
    latest = _latest_per_outlook(events)
    cfg = load_config()
    running = bool(_run_proc and _run_proc.poll() is None)
    # 代理池数量（config.json proxies 字段 + proxies.txt）
    tool_cfg = _tool_config()
    proxy_count = 0
    proxies_cfg = tool_cfg.get("proxies") or []
    if isinstance(proxies_cfg, str):
        proxies_cfg = [proxies_cfg]
    proxy_count += len([p for p in proxies_cfg if str(p).strip()])
    txt_path = os.path.join(BASE, "proxies.txt")
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            proxy_count += sum(1 for l in f if l.strip() and not l.strip().startswith("#"))
    return {
        "ok": True,
        "by_status": by_status,
        "status_labels": STATUS_LABELS,
        "outlook_total": len({e.get("outlook_email") for e in events if e.get("outlook_email")}),
        "verified": sum(1 for e in latest if e.get("status") == "verified"),
        "config": {
            "api_base": cfg.get("api_base", ""),
            "domain": cfg.get("domain", ""),
            "recovery_enabled": bool(cfg.get("enable_recovery_email")),
            "proxy_count": proxy_count,
        },
        "run": {"running": running, "pid": _run_proc.pid if running else None},
    }


def _ensure_pool_forwarder() -> None:
    """转发器模式：确认 pool_forwarder.py 在运行，否则拉起"""
    if not _tool_config().get("use_pool_forwarder"):
        return
    try:
        probe = socket.create_connection(("127.0.0.1", 8899), timeout=2)
        probe.close()
        return
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, "pool_forwarder.py"],
            cwd=BASE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        print("[dashboard] pool_forwarder.py 已启动")
    except Exception as e:
        print(f"[dashboard] 启动 pool_forwarder 失败: {e}")


@app.post("/api/run/start")
def api_run_start():
    global _run_proc
    with _run_lock:
        if _run_proc and _run_proc.poll() is None:
            return JSONResponse({"ok": False, "message": "Registration already running"}, status_code=409)
        _ensure_pool_forwarder()
        try:
            _run_proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=BASE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except Exception as e:
            return JSONResponse({"ok": False, "message": f"Failed to start: {e}"}, status_code=500)
    return {"ok": True, "pid": _run_proc.pid}


@app.post("/api/run/stop")
def api_run_stop():
    global _run_proc
    with _run_lock:
        if _run_proc and _run_proc.poll() is None:
            try:
                _run_proc.terminate()
            except Exception:
                pass
            _run_proc = None
            return {"ok": True, "message": "Stop requested"}
    return {"ok": True, "message": "Not running"}


@app.get("/api/run/status")
def api_run_status():
    running = bool(_run_proc and _run_proc.poll() is None)
    return {"ok": True, "running": running, "pid": _run_proc.pid if running else None}


def main():
    # Windows 控制台 GBK 兼容：强制 UTF-8 输出
    if sys.platform.startswith("win"):
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="OutlookRegister 可视化面板")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("[!] 缺少 uvicorn，正在安装 ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn[standard]"])
        import uvicorn  # noqa: F401

    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://{args.host}:{args.port}/")
        except Exception:
            pass

    print(f"\n📊 OutlookRegister Panel: http://{args.host}:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
