"""Immutable deployment model spaces shared by query and passage consumers."""
# 模型空间是 Query、Passage 和索引兼容性的共同契约。
# 同一标识不能悄悄指向不同模型、维度、分词器或归一化策略。

import math

from aether_agent_memory.recall.contracts.foundation import EmbeddingSpace
from aether_agent_memory.recall.contracts.models import EmbeddingRequest, EmbeddingResult
from aether_agent_memory.runtime.contracts.models import ErrorCode
from aether_agent_memory.runtime.foundation.common import FoundationError
from aether_agent_memory.runtime.foundation.requests import text_hash


class EmbeddingSpaces:
    def __init__(self, spaces: tuple[EmbeddingSpace, ...]) -> None:
        # 拒绝重复空间标识，防止后注册配置静默覆盖先前配置。
        self._spaces = {s.model_space: s for s in spaces}
        if len(self._spaces) != len(spaces):
            raise ValueError("duplicate model space")

    def resolve(self, model_space: str) -> EmbeddingSpace:
        # 只允许使用已装配的空间；未知标识不能自动回退到另一个模型。
        if model_space not in self._spaces:
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "unknown model space")
        return self._spaces[model_space]


def validate_embedding(
    request: EmbeddingRequest, result: EmbeddingResult, space: EmbeddingSpace
) -> None:
    # 先验证整批请求归属，再逐项核对索引、输入摘要、维度和有限值。
    # 声明单位归一化的空间允许 1e-4 数值误差，超过误差视为提供方契约失败。
    if (
        result.operation_id != request.operation_id
        or result.usage != request.usage
        or result.model_space != request.model_space
        or result.model_space != space.model_space
        or result.dimensions != space.dimensions
        or len(result.items) != len(request.texts)
    ):
        raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "embedding response binding mismatch")
    for index, (text, item) in enumerate(zip(request.texts, result.items, strict=True)):
        norm = math.sqrt(sum(v * v for v in item.vector))
        if (
            item.index != index
            or item.input_hash != text_hash(text)
            or len(item.vector) != space.dimensions
            or not all(math.isfinite(v) for v in item.vector)
            or (space.normalization == "unit" and abs(norm - 1) > 1e-4)
        ):
            raise FoundationError(ErrorCode.CONTRACT_VIOLATION, "invalid embedding item")
