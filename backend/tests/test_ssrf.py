"""SSRF 防护测试：utils/ssrf.py 拦截内网/环回/保留地址，web_search 工具接入校验。

公网用例直接使用公网 IP 字面量（getaddrinfo 无需 DNS，离线可测）。
"""

import pytest

from app.core.tools import web_search
from app.core.tools.web_search import WebSearch
from app.utils.ssrf import ensure_public_url, is_public_host


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # 云元数据端点
        "::1",
        "0.0.0.0",
        "nonexistent.invalid",  # 解析失败保守拒绝
    ],
)
def test_private_hosts_are_blocked(host):
    assert not is_public_host(host)


def test_public_ip_allowed():
    assert is_public_host("8.8.8.8")


def test_ensure_public_url_raises_for_internal():
    with pytest.raises(ValueError, match="SSRF"):
        ensure_public_url("http://192.168.1.10:8080/api")


def test_ensure_public_url_rejects_url_without_host():
    with pytest.raises(ValueError, match="SSRF"):
        ensure_public_url("not-a-url")


def test_web_search_blocks_internal_endpoint(monkeypatch):
    """搜索端点被劫持为内网地址时（如配置错误），请求发起前被拦截。"""
    monkeypatch.setattr(web_search, "_SEARCH_URL", "http://127.0.0.1:8000/search")
    result = WebSearch().execute(query="test")
    assert result["success"] is False
    assert "SSRF" in result["error"]
