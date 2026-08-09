"""ReAct 系统提示词：定义 Thought→Action→Observation 格式，注入工具描述与历史经验。

依赖：pyyaml（读取 config/prompts.yaml，提示词文本以配置层为单一事实来源）。
支持按迭代轮次注入不同的交付压力指令：
- 首轮：要求直接输出完整代码（避免无限调工具）
- 后续轮：结合工具观察结果修正，但仍要求尽快交付代码
"""

from pathlib import Path
from typing import List

import yaml

# 项目根：prompts -> core -> app -> backend -> 根（比 llm/config.py 深一层）
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# config/prompts.yaml 路径（提示词文本以配置层为单一事实来源）
_CONFIG_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"


def _load_templates() -> dict:
    """读取 prompts.yaml 中的 react 模板。"""
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("react", {})


def tools_to_text(tools_schemas: List[dict]) -> str:
    """将 OpenAI Function Calling schema 列表转为人类可读的工具描述文本。"""
    lines = []
    for schema in tools_schemas:
        fn = schema.get("function", {})
        properties = fn.get("parameters", {}).get("properties", {})
        arg_names = ", ".join(properties.keys())
        lines.append(f"- {fn.get('name', '')}({arg_names}): {fn.get('description', '')}")
    return "\n".join(lines)


def build_react_system_prompt(
    tools_desc: str,
    experiences: List[str],
    iteration: int = 0,
) -> str:
    """组装 ReAct 系统提示词：基础指令 + 交付压力（按轮次）+ 可用工具 + 历史经验 + 输出格式。"""
    templates = _load_templates()
    sections = [templates.get("system_prompt", "").strip()]

    # 按轮次注入交付压力：首轮强制直接输出代码，后续轮要求尽快交付
    delivery_hint = templates.get("delivery_hint", "").strip()
    if delivery_hint:
        sections.append(delivery_hint)

    if tools_desc.strip():
        sections.append(f"## 可用工具\n{tools_desc.strip()}")

    if experiences:
        exp_text = "\n".join(f"- {item}" for item in experiences)
        sections.append(f"## 历史经验（参考，非强制）\n{exp_text}")

    format_hint = templates.get("format_hint", "").strip()
    if format_hint:
        sections.append(f"## 输出格式\n{format_hint}")

    return "\n\n".join(sections)
