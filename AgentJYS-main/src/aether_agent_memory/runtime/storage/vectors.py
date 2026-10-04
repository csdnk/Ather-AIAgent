"""Our namespaced Azure Milvus provider behind the existing projection/search ports."""

import re
from typing import Any
from urllib.parse import urlsplit

from aether_agent_memory.runtime.flows.vector_adapters import MilvusVectors
from aether_agent_memory.runtime.foundation.common import fingerprint
from aether_agent_memory.runtime.foundation.identity import Identity
from aether_agent_memory.runtime.storage.ports import MetadataUnitOfWork


class AzureVectors(MilvusVectors):
    def __init__(
        self,
        uow: MetadataUnitOfWork,
        identity: Identity,
        model_space: str,
        dimensions: int,
        *,
        namespace: str,
        collection: str,
        uri: str,
        token: str,
        ca_file: str | None,
        server_name: str,
        database: str,
    ) -> None:
        url = urlsplit(uri)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Azure Milvus requires an HTTPS origin without credentials")
        if not token or not ca_file or not server_name:
            raise ValueError("Azure Milvus requires explicit authentication, CA and server name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", namespace):
            raise ValueError("Milvus namespace must be a bounded environment identifier")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", collection):
            raise ValueError("Milvus logical collection must be a bounded identifier")
        prefix = fingerprint([namespace, database, collection])
        actual_collection = "p3_" + prefix[:32] + "_" + collection
        super().__init__(
            uow,
            identity,
            model_space,
            dimensions,
            uri=uri,
            token=token,
            collection=actual_collection,
            secure=True,
            ca_file=ca_file,
            server_name=server_name,
            database=database,
        )
        self.projection_namespace = "milvus_projections_" + prefix
        self.namespace, self.database, self.logical_collection = namespace, database, collection
        self.endpoint_hash = fingerprint([uri.rstrip("/"), server_name])

    def binding(self) -> dict[str, Any]:
        return {
            "provider": "azure_milvus",
            "namespace": self.namespace,
            "endpoint_hash": self.endpoint_hash,
            "database": self.database,
            "collection": self.collection,
            "model_space": self.model_space,
            "dimensions": self.dimensions,
            "contract_version": 1,
        }
