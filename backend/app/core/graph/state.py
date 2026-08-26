"""AgentState：LangGraph 图运行状态，追踪消息历史、反思计数、批判意见与最终代码。

依赖：pydantic（BaseModel + Field 定义状态 schema）；字段追加策略见 _append_items。
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field


def _append_items(left: list[dict], right: list[dict]) -> list[dict]:
    """列表追加 reducer：节点返回的新条目追加到末尾，而非覆盖整表。

    同时服务 messages（消息历史）与 reflections（反思记录）两个列表字段。
    """
    return left + right


class AgentState(BaseModel):
    """贯穿 生成 → 测试 → 反思 → 优化 → 交付 全流程的可变上下文（LangGraph State）。

    设计说明：state 是图中唯一的状态载体，所有节点读写都通过它流转。
    test_code / test_results 保证"测试生成一次、失败详情可追溯"；
    final_message 保证任何情况下交付物不为空（兜底信息）。
    """

    # 用户任务描述（图启动时的初始输入）
    task_desc: str = ""
    # 本次请求选择的模型名（前端模型下拉可选传入；空串表示使用后端默认配置）
    model: str = ""
    # 消息历史（OpenAI 兼容 dict 格式）：使用自定义 reducer 自动追加；
    # value 类型用 Any 以兼容 tool_calls（List[Dict]）、tool_call_id（str）等多种结构
    messages: Annotated[list[dict[str, Any]], _append_items] = Field(default_factory=list)
    # 反思记录列表（每轮一条 {round_index, critique, refined_code}）：随图状态流转，
    # 替代原模块级单例 ReflectionStore，避免多会话并发时互相清空/串味（每个图实例独立）
    reflections: Annotated[list[dict[str, Any]], _append_items] = Field(default_factory=list)
    # 反思轮次计数：每轮 Reflect → Refine 自增，由 max_reflection_rounds 约束上限
    reflection_count: int = 0
    # ReAct 循环迭代计数：react_node 每执行一次自增，超限强制进入反思，
    # 防止 LLM 决策失控导致 ReAct 死循环（见 edges.py 的 MAX_REACT_ITERATIONS）
    react_iterations: int = 0
    # 最近一轮批判意见（Reflect 节点产出，Refine 节点消费）
    critique: str = ""
    # 迭代中的当前代码（ReAct / Refine 产出，供测试与审查）
    current_code: str = ""
    # 最终交付代码（全流程结束后的结果；finalize_node 保证非空，兜底为错误说明）
    final_code: str = ""
    # 当前代码是否通过测试（edges.py 路由判断依据）
    tests_passed: bool = False
    # 生成的 pytest 测试代码（test_gen_node 独立生成一次，后续循环复用，避免重复 LLM 调用）
    test_code: str = ""
    # 测试重生成次数：测试自身崩溃（collection error）时重生成测试而非改代码，
    # 上限 1 次防死循环（超过则冒烟测试兜底）
    test_regen_count: int = 0
    # 测试自身崩溃标记（test_node 写入）：True 时路由回 test_gen_node 重生成测试
    test_broken: bool = False
    # refine 未产出新代码标记（refine_node 写入）：LLM 超时/失败导致代码未变时为 True，
    # 路由直接 finalize——同样的代码再走一轮 review/test/reflect 是同输入重复调用，纯浪费
    refine_no_progress: bool = False
    # 最近一次测试的客观失败详情（断言错误/traceback，供 reflect_node 注入反思）
    test_results: dict[str, Any] = Field(default_factory=dict)
    # 最终交付附带说明（如兜底失败原因），finalize_node 写入，随 final_code 返回前端
    final_message: str = ""
    # 历史最优代码快照（测试通过时由 test_node 更新）：refine 改坏代码后，
    # finalize_node 回退到该版本，保证"交付质量下限"不因迭代劣化而丢失
    best_code: str = ""
    # best_code 是否真实通过过测试（区别于 best_code 为空占位）
    best_tests_passed: bool = False
    # 通用问答模式：react_node 判定 LLM 无代码、无工具调用，输出即为自然语言回答。
    # 为 True 时图跳过测试/反思/优化链路，finalize_node 直接交付 final_message 文本。
    is_answer_only: bool = False
    # ===== 安全审查（四层链路）=====
    # 审查结论：allow（放行执行）/ block（拦截，拦截消息已回填）/ confirm（需人工确认）
    # 由 review_node 写入；react_node 每轮新决策时重置为空
    security_outcome: str = ""
    # 逐工具审查详情：tool_call_id -> {"verdict", "reason", "tool", "args"}
    # confirm 场景下 interrupt 前必须先落 state（节点中断会丢局部变量，
    # 恢复重跑时 confirm_node 从这里读待确认信息，避免重复调用风险分类 LLM）
    security_decisions: dict[str, Any] = Field(default_factory=dict)
    # 人工确认结果：True 批准 / False 拒绝（confirm_node 从 interrupt 恢复后写入）
    security_confirmation: bool | None = None
    # 已通过审查的代码内容指纹（sha256）：code_review_node 放行（含人工批准）时写入。
    # 测试失败回环（refine 未改动代码）再次进入代码审查时凭指纹跳过，
    # 避免同一份已批准代码反复弹窗；代码内容一变指纹即失配，重新走完整审查
    reviewed_code_hash: str = ""
