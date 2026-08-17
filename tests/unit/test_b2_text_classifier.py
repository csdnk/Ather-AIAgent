import pytest

from aether_agent_memory.b2.text_classifier import classify_text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("请记住，我以后都喜欢简洁回答。", "user_preference"),
        ("下周三前需要提交合同，提醒我。", "task"),
        ("天气接口返回南京有雨。", "tool_result"),
        ("这份系统设计文档说明 Redis 用于任务状态。", "document_knowledge"),
        ("你好，今天我们继续讨论项目。", "conversation"),
    ],
)
def test_classifies_b2_memory_text(text: str, category: str) -> None:
    result = classify_text(text)
    assert result.category == category
    assert 0.0 <= result.confidence <= 1.0


@pytest.mark.unit
def test_empty_text_is_unknown() -> None:
    assert classify_text("  ").category == "unknown"
