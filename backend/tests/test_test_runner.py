"""测试运行器解析逻辑测试：_parse_summary 从 pytest 输出提取统计。"""

import pytest

from app.core.tools.test_runner import TestRunner


class TestParseSummary:
    """_parse_summary：解析 pytest 汇总输出中的 passed / failed / error 计数。"""

    def test_全部通过(self):
        counts = TestRunner._parse_summary("1 passed in 0.01s")
        assert counts == {"passed": 1, "failed": 0, "errors": 0}

    def test_混合结果(self):
        counts = TestRunner._parse_summary("3 passed, 1 failed, 1 error in 1.23s")
        assert counts == {"passed": 3, "failed": 1, "errors": 1}

    def test_仅错误(self):
        counts = TestRunner._parse_summary("2 errors in 0.50s")
        assert counts == {"passed": 0, "failed": 0, "errors": 2}

    def test_空输出_全零(self):
        counts = TestRunner._parse_summary("")
        assert counts == {"passed": 0, "failed": 0, "errors": 0}

    def test_取最后一次汇总(self):
        # 带 traceback 的输出中汇总可能多次出现，应取末尾的最终值
        output = "test_a FAILED\n1 failed in 0.10s\n\n================\n1 failed in 0.10s"
        counts = TestRunner._parse_summary(output)
        assert counts == {"passed": 0, "failed": 1, "errors": 0}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
