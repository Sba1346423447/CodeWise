"""SSRF 防护：拦截内网 / 环回 / 保留地址的外发请求。

依赖：标准库（ipaddress / socket / urllib.parse）。
适用于出站请求前校验目标 URL（如 web_search 工具），防止指向内网服务。
注意：不防护 DNS rebinding（校验与请求间的解析窗口），当前单机部署可接受。
"""

import ipaddress
import socket
from urllib.parse import urlsplit


def is_public_host(host: str) -> bool:
    """校验主机名解析出的所有 IP 均为公网地址（非内网/环回/链路本地/保留段）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # 解析失败按非公网处理，保守拒绝
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def ensure_public_url(url: str) -> None:
    """校验 URL 指向公网地址；不通过时抛出 ValueError（含拦截原因）。"""
    host = urlsplit(url).hostname
    if not host:
        raise ValueError(f"SSRF 防护拦截：无效 URL（缺少主机名）{url!r}")
    if not is_public_host(host):
        raise ValueError(f"SSRF 防护拦截：目标为内网/保留地址 {host}")
