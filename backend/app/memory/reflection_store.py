"""中期反思记忆：按轮次索引存储本次会话的反思记录。

纯标准库实现；反思记录随会话生命周期存在，供反思节点写入、路由判断读取。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ReflectionRecord:
    """单轮反思记录：批判意见 + 优化后代码。"""

    round_index: int      # 反思轮次（从 1 开始）
    critique: str         # 批判意见（四维度审查结论）
    refined_code: str     # 优化后的代码
    passed: bool = False  # 该轮代码是否通过审查


class ReflectionStore:
    """按轮次索引的反思记录存储（中期记忆，随会话生命周期存在）。"""

    def __init__(self) -> None:
        self._records: Dict[int, ReflectionRecord] = {}

    def add(
        self,
        round_index: int,
        critique: str,
        refined_code: str,
        passed: bool = False,
    ) -> None:
        """新增一条反思记录；同轮次重复写入时覆盖（防止节点重入）。"""
        self._records[round_index] = ReflectionRecord(
            round_index=round_index,
            critique=critique,
            refined_code=refined_code,
            passed=passed,
        )

    def get(self, round_index: int) -> Optional[ReflectionRecord]:
        """按轮次索引获取反思记录。"""
        return self._records.get(round_index)

    def latest(self) -> Optional[ReflectionRecord]:
        """获取最近一轮反思记录（供 edges.py 路由判断是否继续反思）。"""
        return self._records[max(self._records)] if self._records else None

    def all_records(self) -> List[ReflectionRecord]:
        """按轮次升序返回全部反思记录。"""
        return [self._records[key] for key in sorted(self._records)]

    def count(self) -> int:
        """当前已完成的反思轮数。"""
        return len(self._records)

    def clear(self) -> None:
        """清空全部反思记录（会话重置时调用）。"""
        self._records.clear()
