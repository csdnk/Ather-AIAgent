"""Original vector chunks retain their exact source coordinates during recall."""

from hashlib import sha256

import pytest

from aether_agent_memory.remember.basic.passages import original_passages
from aether_agent_memory.remember.contracts.foundation import ChunkDescriptor


def digest(text):
    return sha256(text.encode()).hexdigest()


def chunk(index, text, start, end):
    return ChunkDescriptor(
        chunk_index=index,
        start_char=start,
        end_char=end,
        input_hash=digest(text[start:end]),
        vector_id=digest(str(index)),
        verified=True,
    )


def test_read_selected_chunk_not_whole_long_memory():
    text = "前面的背景。" * 500 + "负责人是林澈。" + "后面的背景。" * 500
    start = len("前面的背景。" * 500)
    selected = chunk(1, text, start, start + len("负责人是林澈。"))
    passages = original_passages(text, digest(text), (selected,))
    assert len(passages) == 1
    assert passages[0].content == "负责人是林澈。"
    assert passages[0].start_char == start
    assert passages[0].total_chars == len(text)
    assert passages[0].body_hash == digest(text)
    assert passages[0].range_hash == selected.input_hash


def test_corrupted_or_stale_chunk_never_returns_content():
    text = "第一部分。负责人是林澈。"
    selected = chunk(1, text, 5, len(text))
    with pytest.raises(ValueError):
        original_passages(text, digest("changed"), (selected,))
    with pytest.raises(ValueError):
        original_passages(text, digest(text), (selected.model_copy(update={"start_char": 0}),))


def test_multiple_hits_keep_rank_order_and_do_not_duplicate_chunks():
    text = "甲方会议。乙方会议。"
    first, second = chunk(0, text, 0, 5), chunk(1, text, 5, len(text))
    passages = original_passages(text, digest(text), (second, first, second))
    assert [p.chunk_index for p in passages] == [1, 0]
