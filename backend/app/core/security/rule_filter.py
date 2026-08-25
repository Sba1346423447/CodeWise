"""第一层·规则过滤：确定性黑名单审查，拦截明确危险的工具调用参数。

设计原则：
- 零成本、可解释：纯字符串模式匹配，命中即拦截并给出命中的规则名
- 规则来源 config/settings.yaml 的 security 段（模块级缓存，参考 edges.py 模式）
- 只拦"明确危险"的模式；可疑但可能有正当用途的交给第三层 AI 风险分类判定
"""

from pathlib import Path
from typing import Any

import yaml

from ...utils.logger import get_logger

logger = get_logger("core.security.rule_filter")

# 项目根：rule_filter -> security -> core -> app -> backend -> 根
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"

# 默认拦截级危险代码模式（settings.yaml 缺失/读取失败时的兜底）：
# 命中即拦截（block），不允许通过任何链路
_DEFAULT_DANGEROUS_CODE = [
    "os.system", "os.popen", "subprocess.", "shutil.rmtree",
    "eval(", "exec(", "__import__(",
]
# 默认确认级代码模式：网络外联。可能有正当用途（如用户明确要求发 HTTP 请求），
# 命中后挂起等人工确认（confirm），而非静默拦截
_DEFAULT_CONFIRM_CODE = ["socket.", "requests.", "urllib."]
# 默认敏感路径模式：密钥 / 凭据 / 环境变量文件
_DEFAULT_SENSITIVE_PATH = [".env", ".pem", ".key", "id_rsa", "secret", "credential", "token"]


def _load_security_config() -> dict[str, Any]:
    """读取 settings.yaml 的 security 段；读取失败返回空 dict（走代码内默认规则）。"""
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return raw.get("security", {}) or {}
    except (OSError, yaml.YAMLError):
        return {}


# 模块级缓存：避免每次审查重复读盘（与 edges.py 的 MAX_REFLECTION_ROUNDS 同模式）
_SECURITY_CONFIG = _load_security_config()
# 总开关：False 时整个审查链路短路（直接放行）
SECURITY_ENABLED = bool(_SECURITY_CONFIG.get("enabled", True))

_RULES: dict[str, list[str]] = _SECURITY_CONFIG.get("rules", {}) or {}
DANGEROUS_CODE_PATTERNS: list[str] = list(
    _RULES.get("dangerous_code_patterns", _DEFAULT_DANGEROUS_CODE)
)
CONFIRM_CODE_PATTERNS: list[str] = list(
    _RULES.get("confirm_code_patterns", _DEFAULT_CONFIRM_CODE)
)
SENSITIVE_PATH_PATTERNS: list[str] = list(
    _RULES.get("sensitive_path_patterns", _DEFAULT_SENSITIVE_PATH)
)


def check_code_patterns(code: str) -> str | None:
    """扫描代码文本中的拦截级危险模式；命中返回"模式 + 规则说明"，未命中返回 None。

    作用对象：code_executor / test_runner 的 code 参数与代码主链路（code_review_node）。
    网络外联类模式（socket/requests/urllib）不在此列——归 check_code_confirm_patterns。
    """
    if not code:
        return None
    for pattern in DANGEROUS_CODE_PATTERNS:
        if pattern in code:
            return f"代码包含危险模式：{pattern}（命令执行/动态执行类操作被禁止）"
    return None


def check_code_confirm_patterns(code: str) -> str | None:
    """扫描代码文本中的确认级模式（网络外联）；命中返回原因，未命中返回 None。

    命中后不拦截而是挂起等人工确认：网络请求可能有正当用途
    （如用户明确要求"把代码发到某地址"），由用户最终裁决。
    """
    if not code:
        return None
    for pattern in CONFIRM_CODE_PATTERNS:
        if pattern in code:
            return f"代码包含网络外联模式：{pattern}（将发起网络请求，需人工确认）"
    return None


def check_path_patterns(path: str) -> str | None:
    """扫描目标路径中的敏感文件模式；命中返回拦截原因，未命中返回 None。

    作用对象：file_editor 的 path 参数。防密钥/凭据文件读写（.env、私钥等）。
    """
    if not path:
        return None
    lowered = path.lower()
    for pattern in SENSITIVE_PATH_PATTERNS:
        if pattern.lower() in lowered:
            return f"目标路径命中敏感文件规则：{pattern}（密钥/凭据文件禁止读写）"
    return None


def check_tool_call(tool_name: str, args: dict[str, Any]) -> str | None:
    """对单个工具调用做拦截级规则审查：命中返回拦截原因，安全返回 None。

    分工具审查策略：
    - code_executor / test_runner：审查 code 参数（拦截级危险代码模式）
    - file_editor：审查 path 参数（敏感路径模式）
    - web_search / 其他工具：无静态规则，交由第三层 AI 风险分类判定
    """
    if tool_name in ("code_executor", "test_runner"):
        return check_code_patterns(str(args.get("code", "")))
    if tool_name == "file_editor":
        return check_path_patterns(str(args.get("path", "")))
    return None


def check_tool_call_confirm(tool_name: str, args: dict[str, Any]) -> str | None:
    """对单个工具调用做确认级规则审查：命中返回确认原因，否则返回 None。

    与 check_tool_call（拦截级）互补：网络外联类代码不静默拦截，
    而是交由第四层人工确认裁决（用户可能确实想执行该代码）。
    """
    if tool_name in ("code_executor", "test_runner"):
        return check_code_confirm_patterns(str(args.get("code", "")))
    return None
