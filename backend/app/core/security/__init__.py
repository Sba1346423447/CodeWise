"""安全审查模块：工具调用执行前的四层审查链路。

分层设计（成本递增、精度递增）：
1. 规则过滤（rule_filter）：确定性黑名单，零成本拦截明确危险模式
2. 工具自检：各工具 execute 内部的参数校验（如 file_editor 路径越权防护）
3. AI 风险分类（risk_classifier）：LLM 语义判别 prompt 注入等规则拦不住的攻击
4. 人工确认：中高风险操作挂起，用户批准后才执行（graph/nodes.py 的 confirm_node）
"""
