"""网络搜索工具：搜索 Python 文档/API 参考，返回相关代码片段与链接。

依赖：httpx（HTTP 客户端）；搜索端点使用 DuckDuckGo HTML 版，无需 API Key。
所有外发请求（含重定向目标）均经 SSRF 防护校验（utils/ssrf.py），
防止被诱导访问内网/保留地址。
"""

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx

from ...utils.ssrf import ensure_public_url
from .base import Tool

# DuckDuckGo HTML 搜索端点（免 API Key，适合轻量场景）
_SEARCH_URL = "https://html.duckduckgo.com/html/"
# 单次返回的最大结果数
MAX_RESULTS = 5
# 请求超时（秒）
_TIMEOUT = 10.0
# 最大重定向跟随次数（每一跳都过 SSRF 校验）
_MAX_REDIRECTS = 3

# 解析结果页标题与摘要片段的正则（HTML 结构变更时需同步调整）
_RESULT_A = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
_RESULT_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>')


class WebSearch(Tool):
    """搜索 Python 官方文档与 API 参考，返回标题、链接与代码片段。"""

    name = "web_search"
    description = "搜索 Python 官方文档与 API 参考，返回相关代码片段，辅助解决实现问题。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题描述"},
            },
            "required": ["query"],
        }

    @staticmethod
    def _clean(text: str) -> str:
        """去除 HTML 标签并反转义实体字符。"""
        return unescape(re.sub(r"<[^>]+>", "", text)).strip()

    def _parse_results(self, html: str) -> list[dict[str, str]]:
        """解析 DuckDuckGo 结果页，提取标题 / 链接 / 摘要。"""
        titles = [
            (href, self._clean(title)) for href, title in _RESULT_A.findall(html)
        ]
        snippets = [self._clean(snip) for snip in _RESULT_SNIPPET.findall(html)]

        results = []
        for i, (href, title) in enumerate(titles[:MAX_RESULTS]):
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippets[i] if i < len(snippets) else "",
                }
            )
        return results

    def _fetch(self, query: str) -> httpx.Response:
        """带 SSRF 防护的搜索请求：手动跟随重定向，每一跳目标均校验为公网地址。"""
        url = _SEARCH_URL
        params: dict[str, str] | None = {"q": query}
        for _ in range(_MAX_REDIRECTS + 1):
            ensure_public_url(url)
            resp = httpx.get(url, params=params, timeout=_TIMEOUT, follow_redirects=False)
            if resp.is_redirect:
                url = urljoin(url, resp.headers["location"])
                params = None  # 重定向跳转不再重复携带查询参数
                continue
            resp.raise_for_status()
            return resp
        raise httpx.HTTPError("重定向次数过多")

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        query = kwargs.get("query", "")
        if not query.strip():
            return {"success": False, "error": "query 参数不能为空"}

        try:
            resp = self._fetch(query)
        except httpx.HTTPError as exc:
            # 网络不可用/被限流时返回友好错误，不中断 Agent 流程
            return {"success": False, "error": f"搜索请求失败：{exc}"}
        except ValueError as exc:
            # SSRF 防护拦截：目标为内网/保留地址，直接拒绝
            return {"success": False, "error": str(exc)}

        results = self._parse_results(resp.text)
        if not results:
            return {"success": True, "count": 0, "results": [], "message": "未找到相关结果"}

        return {"success": True, "count": len(results), "results": results}
