from enum import StrEnum


class MemoryType(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class SourceType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    TOOL = "tool"
    RAG = "rag"
    DOCUMENT = "document"
    DISTILLED = "distilled"


class SignalType(StrEnum):
    ACCESS = "access"
    EVICTION = "eviction"
    PROMOTION_HINT = "promotion_hint"
    DEMOTION_HINT = "demotion_hint"
    ARCHIVAL = "archival"
    CREATION = "creation"


class StorageTier(StrEnum):
    L0_DRAM = "L0"
    L1_NVME = "L1"
    L2_HDD = "L2"
    L3_OBJECT = "L3"
    L4_ARCHIVE = "L4"
