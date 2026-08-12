"""链式代理转发器：浏览器 → 本机转发 → Clash → cliproxy → 目标站点。

解决场景：cliproxy 等代理服务商拒绝从你的直连 IP 接入，报错形如：
    msg: forbidden ip=112.92.71.20 not supported
（112.92.71.20 是你的本机出口 IP）

原理：本转发器监听一个本地端口，把浏览器的代理流量原样中继到
cliproxy，但 TCP 连接本身先经 Clash（127.0.0.1:7897）隧道出去，
让 cliproxy 看到的来源 IP 变成 Clash 节点的出口 IP。

用法：
    python chain_proxy.py                        # 默认 127.0.0.1:8899 → Clash 7897 → us2.cliproxy.io:3010
    python chain_proxy.py --listen 8899 --upstream 127.0.0.1:7897 --target us2.cliproxy.io:3010

然后把 proxies.txt 里的代理换成：
    http://<clip用户名>:<clip密码>@127.0.0.1:8899
（凭据会原样转发给 cliproxy 做认证，本转发器不校验）
"""
from __future__ import annotations

import argparse
import socket
import threading


def parse_endpoint(text: str) -> tuple[str, int]:
    host, port = text.rsplit(":", 1)
    return host, int(port)


def tunnel_via(upstream: tuple[str, int], target: tuple[str, int]) -> socket.socket:
    """经上游 HTTP 代理（Clash）建立到 target 的 TCP 隧道"""
    s = socket.create_connection(upstream, timeout=15)
    req = (f"CONNECT {target[0]}:{target[1]} HTTP/1.1\r\n"
           f"Host: {target[0]}:{target[1]}\r\n\r\n").encode()
    s.sendall(req)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    status = buf.split(b"\r\n", 1)[0]
    if b" 200" not in status:
        s.close()
        raise RuntimeError(f"upstream CONNECT failed: {status[:120]!r}")
    return s


def _relay(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except Exception:
                pass


def handle(client: socket.socket, args) -> None:
    try:
        upstream = tunnel_via(
            parse_endpoint(args.upstream),
            parse_endpoint(args.target),
        )
    except Exception as e:
        try:
            client.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n".encode())
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
        print(f"[chain] 隧道建立失败: {e}")
        return
    # 客户端(浏览器)的 CONNECT + Proxy-Authorization 原样中继给 cliproxy
    threading.Thread(target=_relay, args=(client, upstream), daemon=True).start()
    _relay(upstream, client)


def main():
    ap = argparse.ArgumentParser(description="链式代理转发器")
    ap.add_argument("--listen", default="127.0.0.1:8899", help="本地监听地址 (默认 127.0.0.1:8899)")
    ap.add_argument("--upstream", default="127.0.0.1:7897", help="上游 Clash 地址 (默认 127.0.0.1:7897)")
    ap.add_argument("--target", default="us2.cliproxy.io:3010", help="目标代理地址 (默认 us2.cliproxy.io:3010)")
    args = ap.parse_args()

    listen_host, listen_port = parse_endpoint(args.listen)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_host, listen_port))
    srv.listen(16)
    print(f"[chain] 链式转发器: {args.listen} -> Clash({args.upstream}) -> {args.target}")
    print(f"[chain] proxies.txt 填: http://<clip用户名>:<clip密码>@{args.listen}")
    while True:
        try:
            client, _addr = srv.accept()
        except Exception:
            break
        threading.Thread(target=handle, args=(client, args), daemon=True).start()


if __name__ == "__main__":
    main()
