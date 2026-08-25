"""Reflection 批判提示词：四维度审查模板（正确性/性能/可读性/类型安全 + 需求契合度）。

依赖：pyyaml（读取 config/prompts.yaml）。
v2 设计：注入测试失败详情（test_results），让反思基于客观事实而非 LLM 自评，
避免"代码正确但测试失败"却输出 pass=true 的假阳性。
"""

from pathlib import Path
from typing import Any

import yaml

# 项目根：prompts -> core -> app -> backend -> 根
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# config/prompts.yaml 路径（审查维度与输出契约以配置层为单一事实来源）
_CONFIG_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


def _load_templates() -> dict:
    """读取 prompts.yaml 中的 reflection 模板。"""
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("reflection", {})


def _format_test_results(test_results: dict[str, Any]) -> str:
    """将测试失败详情格式化为提示词片段；无有效失败信息时返回空串。"""
    if not test_results:
        return ""
    lines = []
    if test_results.get("passed") is not None:
        lines.append(
            f"通过 {test_results.get('passed', 0)} / 失败 {test_results.get('failed', 0)}"
            f" / 错误 {test_results.get('errors', 0)}"
        )
    output = (test_results.get("output") or "").strip()
    if output:
        lines.append(f"测试输出（含失败原因）：\n{output}")
    return "\n".join(lines)


def build_reflection_prompt(
    code: str,
    round_index: int = 1,
    task_desc: str = "",
    test_results: dict[str, Any] | None = None,
) -> str:
    """组装批判审查提示词：系统指令 + 原始用户需求 + 测试结果 + 待审查代码 + 输出格式。

    task_desc 让审查环节核对"代码是否满足用户真实需求"，避免代码正确但主题跑偏时漏检；
    test_results 提供客观失败详情，让反思意见可执行（指出具体修什么）。
    """
    templates = _load_templates()
    sections = [templates.get("system_prompt", "").strip()]

    if task_desc.strip():
        sections.append(f"## 原始用户需求\n{task_desc.strip()}")

    failure_text = _format_test_results(test_results or {})
    if failure_text:
        sections.append(f"## 测试验证结果（客观事实，务必以此为准）\n{failure_text}")

    if code.strip():
        sections.append(f"## 待审查代码（第 {round_index} 轮）\n```python\n{code}\n```")

    output_format = templates.get("output_format", "").strip()
    if output_format:
        sections.append(f"## 输出要求\n{output_format}")

    return "\n\n".join(sections)
