"""Refine 优化提示词：根据批判意见生成改进后的代码。

依赖：pyyaml（读取 config/prompts.yaml）。
v2 设计：强制"只输出合法 Python 代码"，避免 LLM 输出解释文字被误当代码；
以用户需求为唯一目标，主题不符必须彻底重写。
"""

from pathlib import Path

import yaml

# 项目根：prompts -> core -> app -> backend -> 根
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# config/prompts.yaml 路径（优化指令以配置层为单一事实来源）
_CONFIG_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


def _load_templates() -> dict:
    """读取 prompts.yaml 中的 refine 模板。"""
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("refine", {})


def build_refine_prompt(code: str, critique: str, task_desc: str = "") -> str:
    """组装代码优化提示词：系统指令 + 原始用户需求 + 批判意见 + 原代码。

    批判意见与原始需求在前、原代码在后，引导 LLM 以"满足用户需求"为第一优先级重写，
    避免仅围绕代码质量微调而偏离需求主题。
    """
    templates = _load_templates()
    sections = [templates.get("system_prompt", "").strip()]

    if task_desc.strip():
        sections.append(f"## 原始用户需求\n{task_desc.strip()}")

    if critique.strip():
        sections.append(f"## 批判意见\n{critique.strip()}")

    if code.strip():
        sections.append(f"## 原始代码\n```python\n{code}\n```")

    output_hint = templates.get("output_format", "").strip()
    if output_hint:
        sections.append(f"## 输出要求\n{output_hint}")

    return "\n\n".join(sections)
