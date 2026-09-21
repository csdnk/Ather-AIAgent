"""Lightweight, explainable B2 memory classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import jieba
except ImportError:  # pragma: no cover - supports source-only unit checks
    jieba = None


@dataclass(frozen=True)
class TextClassification:
    category: str
    keywords: list[str]
    confidence: float


_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("user_preference", ("记住", "偏好", "喜欢", "以后都", "总是", "不要再")),
    ("tool_result", ("接口返回", "调用结果", "查询结果", "工具结果", "天气", "API")),
    ("rag_evidence", ("检索结果", "参考资料", "证据", "引用", "知识库命中")),
    ("task", ("待办", "截止", "提交", "需要", "下周", "明天", "提醒")),
    ("document_knowledge", ("文档", "说明书", "手册", "规范", "合同", "知识库")),
)


def classify_text(text: str) -> TextClassification:
    """Classify a memory item and expose the matched tokens for traceability."""
    normalized = " ".join(text.strip().split())
    if not normalized:
        return TextClassification(category="unknown", keywords=[], confidence=0.0)
    tokens = _tokens(normalized)
    token_set = set(tokens)
    for category, candidates in _RULES:
        matched = [word for word in candidates if word in normalized or word in token_set]
        if matched:
            return TextClassification(
                category=category,
                keywords=matched,
                confidence=min(0.55 + 0.15 * len(matched), 0.95),
            )
    return TextClassification(category="conversation", keywords=tokens[:5], confidence=0.4)


def _tokens(text: str) -> list[str]:
    if jieba is not None:
        return [token for token in jieba.lcut(text) if token.strip()]
    return re.findall(r"[\u4e00-\u9fff]{1,}|[A-Za-z]+|\d+", text)
