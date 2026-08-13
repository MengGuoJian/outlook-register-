"""本地代理池转发器：把浏览器的 CONNECT 改写为 HTTP/1.0 无头格式，再随机转发到池内端口。

背景：107.151.197.81:22403-22413 这类代理端口（每个端口 = 一个固定出口 IP）
只接受 HTTP/1.0 的无头 CONNECT：
    CONNECT host:port HTTP/1.0\r\n\r\n
curl / Chromium 发的 HTTP/1.1 CONNECT（带 Host / Proxy-Connection 等头）会被静默丢弃。
requests / http.client 用 HTTP/1.0 所以能通（面板测试正常的原因）。

用法：
    python pool_forwarder.py                        # 默认监听 127.0.0.1:8899，读 proxies.txt
    python pool_forwarder.py --listen 8899 --pool proxies.txt

浏览器代理填：http://127.0.0.1:8899 （无认证）
每个新连接随机抽一个池端口 → 每个注册任务 = 一个出口 IP。
池条目支持 http://host:port 或 host:port:user:pass（带凭据时自动加 Proxy-Authorization 头）。
"""
from __future__ import annotations

import argparse
import base64
import os
import random
import socket
import threading

DEFAULT_POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.txt")


def load_pool(pool_file: str) -> list[dict]:
    """读取代理池，返回 [{host, port, username, password}]

    支持三种条目格式：
        http://host:port
        http://user:pass@host:port
        host:port:user:pass
    """
    entries = []
    if not os.path.exists(pool_file):
        return entries
    with open(pool_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.replace("http://", "", 1).replace("https://", "", 1)
            username = password = ""
            if "@" in line:
                userinfo, line = line.rsplit("@", 1)
                if ":" in userinfo:
                    username, password = userinfo.split(":", 1)
            parts = line.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                entries.append({"host": parts[0], "port": int(parts[1]),
                                "username": username, "password": password})
            elif len(parts) == 4 and parts[1].isdigit():
                entries.append({
                    "host": parts[0], "port": int(parts[1]),
                    "username": parts[2], "password": parts[3],
                })
    return entries


def _read_until(client: socket.socket, marker: bytes, timeout: float = 15) -> bytes:
    client.settimeout(timeout)
    buf = b""
    while marker not in buf:
        chunk = client.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _relay(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def _tunnel_via_clash(upstream: tuple[str, int], target: tuple[str, int]) -> socket.socket | None:
    """经 Clash HTTP 代理建立到 target 的 TCP 隧道（用于 cliproxy 等拒绝直连来源的代理）"""
    try:
        s = socket.create_connection(upstream, timeout=15)
        s.sendall(f"CONNECT {target[0]}:{target[1]} HTTP/1.1\r\n"
                  f"Host: {target[0]}:{target[1]}\r\n\r\n".encode())
        s.settimeout(15)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        if b" 200" in buf.split(b"\r\n", 1)[0]:
            return s
        s.close()
    except Exception:
        pass
    return None


def _inject_auth(req: bytes, entry: dict) -> bytes:
    """把池条目的账密作为 Proxy-Authorization 注入客户端原始请求（已有则替换）"""
    if not entry.get("username"):
        return req
    token = base64.b64encode(f"{entry['username']}:{entry['password']}".encode()).decode()
    auth_line = f"Proxy-Authorization: Basic {token}\r\n".encode()
    head, sep, tail = req.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    out = []
    replaced = False
    for ln in lines:
        if ln.lower().startswith(b"proxy-authorization:"):
            out.append(auth_line.rstrip(b"\r\n"))
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(auth_line.rstrip(b"\r\n"))
    return b"\r\n".join(out) + b"\r\n\r\n" + tail


def _try_forward(entry: dict, target_host: str, target_port: int,
                 out_req: bytes, via_clash: str) -> socket.socket | None:
    """连池端口并发出构造好的请求；成功返回已建立隧道的 socket。

    via_clash 非空时先经 Clash（如 127.0.0.1:7897）隧道到池端口，
    让代理服务商看到的来源 IP 变成 Clash 节点出口。
    """
    try:
        if via_clash:
            s = _tunnel_via_clash(tuple(via_clash.rsplit(":", 1)), (entry["host"], entry["port"]))
        else:
            s = socket.create_connection((entry["host"], entry["port"]), timeout=15)
        if s is None:
            return None
        s.sendall(out_req)
        s.settimeout(15)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        status = buf.split(b"\r\n", 1)[0]
        if b" 200" in status:
            return s
        s.close()
    except Exception:
        pass
    return None


def handle(client: socket.socket, args, pool: list[dict]) -> None:
    try:
        req = _read_until(client, b"\r\n\r\n")
    except Exception:
        client.close()
        return
    first_line = req.split(b"\r\n", 1)[0]
    parts = first_line.split(b" ")
    if len(parts) < 3 or parts[0].upper() != b"CONNECT":
        try:
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass
        client.close()
        return
    target = parts[1]
    if b":" not in target:
        client.close()
        return
    target_host, target_port = target.rsplit(b":", 1)
    try:
        target_port = int(target_port)
    except ValueError:
        client.close()
        return
    target_host = target_host.decode()

    # 随机抽池端口，失败换下一个（最多试 5 个）
    candidates = random.sample(pool, min(5, len(pool))) if pool else []
    for entry in candidates:
        if args.raw:
            # 原样转发客户端请求（注入池条目认证），适配 cliproxy 等标准 HTTP 代理
            out_req = _inject_auth(req, entry)
        else:
            # HTTP/1.0 无头 CONNECT 改写，适配 107.151.197.81 这类特殊池
            out_req = f"CONNECT {target_host}:{target_port} HTTP/1.0\r\n".encode()
            if entry.get("username"):
                token = base64.b64encode(
                    f"{entry['username']}:{entry['password']}".encode()).decode()
                out_req += f"Proxy-Authorization: Basic {token}\r\n".encode()
            out_req += b"\r\n"
        upstream = _try_forward(entry, target_host, target_port, out_req, args.via_clash)
        if upstream is not None:
            # 把网关的 200 响应回给浏览器，然后双向中继
            try:
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            except Exception:
                upstream.close()
                client.close()
                return
            print(f"[forwarder] {target_host}:{target_port} -> {entry['host']}:{entry['port']}"
                  f"{' (via clash)' if args.via_clash else ''}", flush=True)
            threading.Thread(target=_relay, args=(client, upstream), daemon=True).start()
            _relay(upstream, client)
            return
    try:
        client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
    except Exception:
        pass
    client.close()


def main():
    ap = argparse.ArgumentParser(description="本地代理池转发器 (HTTP/1.0 CONNECT 改写)")
    ap.add_argument("--listen", default="127.0.0.1:8899", help="本地监听地址 (默认 127.0.0.1:8899)")
    ap.add_argument("--pool", default=DEFAULT_POOL_FILE, help="代理池文件 (默认 proxies.txt)")
    ap.add_argument("--fixed", default="", help="锁定单个池端口 (默认随机轮换)")
    ap.add_argument("--via-clash", default="", help="经 Clash 隧道连接池端口 (如 127.0.0.1:7897)，来源 IP 变为 Clash 节点出口")
    ap.add_argument("--raw", action="store_true", help="原样转发客户端 CONNECT（注入池条目认证），适配标准 HTTP 代理")
    args = ap.parse_args()

    pool = load_pool(args.pool)
    if not pool:
        print(f"[forwarder] 代理池为空: {args.pool}")
        return
    if args.fixed:
        pool = [dict(pool[0], port=int(args.fixed))]
        print(f"[forwarder] 锁定池端口 {args.fixed} ({pool[0]['host']})", flush=True)
    else:
        print(f"[forwarder] 池 {len(pool)} 个端口: {pool[0]['host']}:{pool[0]['port']} ...", flush=True)

    listen_host, listen_port = args.listen.rsplit(":", 1)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_host, int(listen_port)))
    srv.listen(32)
    print(f"[forwarder] 监听 {args.listen}，浏览器代理填 http://{args.listen} (无认证)", flush=True)
    while True:
        try:
            client, _ = srv.accept()
        except Exception:
            break
        threading.Thread(target=handle, args=(client, args, pool), daemon=True).start()


if __name__ == "__main__":
    main()
