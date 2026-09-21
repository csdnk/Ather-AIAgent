"""Compatibility alias for aether_agent_memory.recall.embedding.backends."""

import sys

from aether_agent_memory.recall.embedding import backends as _implementation
from aether_agent_memory.recall.embedding.backends import (
    BACKEND_DESCRIPTORS as BACKEND_DESCRIPTORS,
)
from aether_agent_memory.recall.embedding.backends import (
    DEFAULT_MODEL_BATCH_SIZE as DEFAULT_MODEL_BATCH_SIZE,
)
from aether_agent_memory.recall.embedding.backends import (
    BackendConfig as BackendConfig,
)
from aether_agent_memory.recall.embedding.backends import (
    BackendDescriptor as BackendDescriptor,
)
from aether_agent_memory.recall.embedding.backends import (
    BackendFactory as BackendFactory,
)
from aether_agent_memory.recall.embedding.backends import (
    BackendUnavailableError as BackendUnavailableError,
)
from aether_agent_memory.recall.embedding.backends import (
    FallbackEmbeddingBackend as FallbackEmbeddingBackend,
)
from aether_agent_memory.recall.embedding.backends import (
    FastEmbedOnnxBackend as FastEmbedOnnxBackend,
)
from aether_agent_memory.recall.embedding.backends import (
    IpexBackend as IpexBackend,
)
from aether_agent_memory.recall.embedding.backends import (
    OpenVinoBackend as OpenVinoBackend,
)
from aether_agent_memory.recall.embedding.backends import (
    Precision as Precision,
)
from aether_agent_memory.recall.embedding.backends import (
    SidecarEmbeddingBackend as SidecarEmbeddingBackend,
)
from aether_agent_memory.recall.embedding.backends import (
    _batch_positions as _batch_positions,
)
from aether_agent_memory.recall.embedding.backends import (
    _file_hash as _file_hash,
)
from aether_agent_memory.recall.embedding.backends import (
    _files_hash as _files_hash,
)
from aether_agent_memory.recall.embedding.backends import (
    _identifier_hash as _identifier_hash,
)
from aether_agent_memory.recall.embedding.backends import (
    _model_source as _model_source,
)
from aether_agent_memory.recall.embedding.backends import (
    _module_available as _module_available,
)
from aether_agent_memory.recall.embedding.backends import (
    _package_version as _package_version,
)
from aether_agent_memory.recall.embedding.backends import (
    create_backend as create_backend,
)
from aether_agent_memory.recall.embedding.backends import (
    create_backend_chain as create_backend_chain,
)
from aether_agent_memory.recall.embedding.backends import (
    describe_backends as describe_backends,
)
from aether_agent_memory.recall.embedding.backends import (
    register_backend as register_backend,
)

# Preserve legacy module monkeypatch/registry behavior with one implementation.
sys.modules[__name__] = _implementation
