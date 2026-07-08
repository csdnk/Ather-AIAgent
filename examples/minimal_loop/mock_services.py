from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from aether_agent_memory import (
    Memory,
    MockContextPackBuilder,
    MockEmbeddingClient,
    MockEpisodicMemoryManager,
    MockSemanticMemoryManager,
    MockSignalEmitter,
    MockStorageClient,
    MockWorkingMemoryManager,
    Settings,
    StorageTier,
)

SCORE_FLOOR = 0.5
SCORE_CEIL = 0.95


def normalize_score(raw: float) -> float:
    clamped = max(0.0, min(1.0, (raw + 1.0) / 2.0))
    return round(SCORE_FLOOR + clamped * (SCORE_CEIL - SCORE_FLOOR), 4)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def fmt_dt(dt: object) -> str:
    if dt is None:
        return "—"
    return str(dt).split("+")[0].split(".")[0]


def fmt_embedding(emb: object, shown: int = 3) -> str:
    if emb is None:
        return "—"
    vals = list(emb)
    if not vals:
        return "[]"
    head = ", ".join(f"{v:+.3f}" for v in vals[:shown])
    more = f", …(+{len(vals) - shown})" if len(vals) > shown else ""
    return f"[{head}{more}]  dim={len(vals)}"


def memory_table_rows(memories: list[Memory]) -> list[list[str]]:
    return [
        [
            m.id[:8],
            m.type.value,
            m.state.value,
            truncate(m.content, 30),
            m.source.value,
            str(m.access_count),
            fmt_dt(m.expires_at),
        ]
        for m in memories
    ]


def episodic_detail_rows(memories: list[Memory]) -> list[list[str]]:
    return [
        [
            m.id[:8],
            m.state.value,
            truncate(m.content, 28),
            fmt_embedding(m.embedding),
            m.p2_ref.object_key if m.p2_ref else "—",
            m.p2_ref.tier.value if m.p2_ref else "—",
        ]
        for m in memories
    ]


@dataclass
class DemoEnv:
    settings: Settings = field(default_factory=Settings)
    embedder: MockEmbeddingClient = field(default_factory=lambda: MockEmbeddingClient(dim=32))
    storage: MockStorageClient = field(
        default_factory=lambda: MockStorageClient(default_tier=StorageTier.L1_NVME)
    )
    working: MockWorkingMemoryManager = field(init=False)
    episodic: MockEpisodicMemoryManager = field(init=False)
    semantic: MockSemanticMemoryManager = field(init=False)
    builder: MockContextPackBuilder = field(init=False)
    emitter: MockSignalEmitter = field(default_factory=MockSignalEmitter)

    def __post_init__(self) -> None:
        self.working = MockWorkingMemoryManager(
            default_ttl=timedelta(seconds=self.settings.default_ttl_seconds)
        )
        self.episodic = MockEpisodicMemoryManager(
            embedder=self.embedder, half_life_hours=self.settings.half_life_hours
        )
        self.semantic = MockSemanticMemoryManager(embedder=self.embedder)
        self.builder = MockContextPackBuilder(
            working=self.working,
            episodic=self.episodic,
            semantic=self.semantic,
        )
