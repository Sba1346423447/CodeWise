"""LLM 配置：模型名、temperature、max_tokens、base_url，支持 YAML + 环境变量双层覆盖。

依赖：pyyaml（读取 config/settings.yaml）、pydantic（配置校验）；
配置优先级：环境变量 > YAML 默认值。
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# settings.yaml 所在目录（项目根 /config/settings.yaml）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "settings.yaml"


class LLMConfig(BaseModel):
    """LLM 调用参数：环境变量优先级高于 settings.yaml。"""

    model: str = Field(default="gpt-4o-mini", description="模型名称")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(default=4096, gt=0, description="单次生成最大 token 数")
    base_url: str = Field(
        default="https://api.openai.com/v1", description="OpenAI 兼容 API 地址"
    )
    api_key: str = Field(default="", description="API Key，敏感信息，建议通过环境变量注入")
    request_timeout: float = Field(
        default=120.0, gt=0, description="单次 LLM 请求超时（秒）：推理模型长推理+复杂代码场景耗时可能超 60s，给足预算避免误杀"
    )

    @classmethod
    def from_yaml(cls) -> "LLMConfig":
        """读取 settings.yaml 的 llm 配置段，同名环境变量覆盖 YAML 默认值。"""
        data: dict = {}
        if _CONFIG_PATH.exists():
            raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            data = raw.get("llm", {})

        return cls(
            model=os.getenv("LLM_MODEL", data.get("model", "gpt-4o-mini")),
            temperature=float(os.getenv("LLM_TEMPERATURE", data.get("temperature", 0.2))),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", data.get("max_tokens", 4096))),
            base_url=os.getenv("OPENAI_BASE_URL", data.get("base_url", "https://api.openai.com/v1")),
            api_key=os.getenv("OPENAI_API_KEY", data.get("api_key", "")),
            request_timeout=float(
                os.getenv("LLM_REQUEST_TIMEOUT", data.get("request_timeout", 120.0))
            ),
        )


# 模块级单例：全应用共享同一份 LLM 配置
config = LLMConfig.from_yaml()
