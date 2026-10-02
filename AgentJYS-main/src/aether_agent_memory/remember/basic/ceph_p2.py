"""Immutable Remember objects on Ceph RGW through the real S3 SigV4 protocol.

The bucket is provisioned by deployment. Credentials are resolved only from named
environment variables, without falling back to AWS profiles or instance metadata.
Whole-object reads validate the stored SHA256. Range readers must additionally
verify their trusted range-manifest hash, as a whole-object hash cannot verify a slice.
"""

import asyncio
import base64
import hashlib
import os
import re
from dataclasses import dataclass
from importlib import import_module
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class CephP2Config:
    endpoint: str
    bucket: str
    access_key_env: str = "CEPH_ACCESS_KEY_ID"
    secret_key_env: str = "CEPH_SECRET_ACCESS_KEY"
    region: str = "us-east-1"
    connect_timeout_seconds: float = 5
    read_timeout_seconds: float = 30
    max_object_bytes: int = 64 * 1024 * 1024
    max_range_bytes: int = 8 * 1024 * 1024
    max_pool_connections: int = 10
    max_attempts: int = 3

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Ceph endpoint must be an HTTP(S) origin without credentials")
        if not self.bucket or self.bucket.strip() != self.bucket or "/" in self.bucket:
            raise ValueError("Ceph bucket must be a nonempty bucket name")
        if not self.region.strip():
            raise ValueError("Ceph region must be nonempty")
        for name in (self.access_key_env, self.secret_key_env):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Ceph credential settings must name environment variables")
        for value in (self.connect_timeout_seconds, self.read_timeout_seconds):
            if not 0 < value < float("inf"):
                raise ValueError("Ceph timeouts must be finite and positive")
        for value in (
            self.max_object_bytes,
            self.max_range_bytes,
            self.max_pool_connections,
            self.max_attempts,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Ceph byte, connection and retry limits must be positive integers")


class CephP2:
    """Bodies-compatible object adapter with bounded synchronous and async I/O."""

    # Conditional creation plus exact comparison on 412 makes the same immutable
    # write safe even when an earlier request is still in flight or lost its reply.
    immutable_write_replay_safe = True

    def __init__(self, config: CephP2Config) -> None:
        self.config = config
        self.endpoint, self.bucket = config.endpoint.rstrip("/"), config.bucket
        credentials = {}
        for parameter, env_name in (
            ("aws_access_key_id", config.access_key_env),
            ("aws_secret_access_key", config.secret_key_env),
        ):
            value = os.environ.get(env_name)
            if not value:
                raise ValueError(f"Ceph credential environment variable is missing: {env_name}")
            credentials[parameter] = value
        try:
            boto3 = import_module("boto3")
            sdk_config = import_module("botocore.config").Config
            self._client_error: type[Exception] = import_module("botocore.exceptions").ClientError
        except ImportError as exc:
            raise RuntimeError("CephP2 requires the remember-ceph optional dependency") from exc
        self.client: Any = boto3.session.Session().client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=config.region,
            **credentials,
            config=sdk_config(
                signature_version="s3v4",
                s3={"addressing_style": "path", "payload_signing_enabled": True},
                connect_timeout=config.connect_timeout_seconds,
                read_timeout=config.read_timeout_seconds,
                max_pool_connections=config.max_pool_connections,
                retries={"mode": "standard", "total_max_attempts": config.max_attempts},
                # RGW support for AWS's optional CRC trailers varies by release.
                # Content-MD5 plus signed payload SHA256 provide upload integrity.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        self._closed = False

    @staticmethod
    def _error_info(exc: Exception) -> tuple[str, int]:
        response = getattr(exc, "response", {})
        return (
            str(response.get("Error", {}).get("Code", "")),
            int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)),
        )

    @classmethod
    def _missing(cls, exc: Exception) -> bool:
        code, status = cls._error_info(exc)
        # A missing bucket is a deployment failure, never an absent object.
        return status == 404 and code in {"NoSuchKey", "NotFound", "404"}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Ceph object client is closed")

    @staticmethod
    def _length(response: dict[str, Any]) -> int:
        length = response.get("ContentLength")
        if type(length) is not int or length < 0:
            raise ValueError("Ceph response content length is invalid")
        return length

    def _consume(
        self, response: dict[str, Any], *, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        stream = response.get("Body")
        if stream is None:
            raise ValueError("Ceph response body is missing")
        try:
            length = self._length(response)
            limit = self.config.max_object_bytes
            if byte_range is not None:
                start, end = byte_range
                limit = self.config.max_range_bytes
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.get("ContentRange", ""))
                if (
                    response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 206
                    or match is None
                    or tuple(map(int, match.groups()[:2])) != (start, end - 1)
                    or int(match.group(3)) < end
                    or length != end - start
                ):
                    raise ValueError("Ceph server returned an invalid byte range")
            if length > limit:
                raise ValueError("Ceph response exceeds configured byte limit")
            result = bytearray()
            while True:
                # The extra byte detects a dishonest ContentLength without an
                # unbounded read or downloading an ignored full-object range.
                chunk = stream.read(min(1024 * 1024, length + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > length:
                    raise ValueError("Ceph response content length mismatch")
            if len(result) != length:
                raise ValueError("Ceph response content length mismatch")
            body = bytes(result)
            if byte_range is None:
                digest = response.get("Metadata", {}).get("sha256", "")
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("Ceph object SHA256 checksum is missing or invalid")
                if hashlib.sha256(body).hexdigest() != digest:
                    raise ValueError("Ceph object SHA256 checksum mismatch")
            return body
        finally:
            stream.close()

    def get_object_sync(self, key: str) -> bytes | None:
        self._ensure_open()
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self._client_error as exc:
            if self._missing(exc):
                return None
            raise
        return self._consume(response)

    def put_object_sync(self, key: str, body: bytes) -> None:
        self._ensure_open()
        if not isinstance(body, bytes):
            raise TypeError("Ceph object body must be immutable bytes")
        if len(body) > self.config.max_object_bytes:
            raise ValueError("Ceph object exceeds configured byte limit")
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentLength=len(body),
                ContentType="application/octet-stream",
                ContentMD5=base64.b64encode(
                    hashlib.md5(body, usedforsecurity=False).digest()
                ).decode("ascii"),
                Metadata={"sha256": hashlib.sha256(body).hexdigest()},
                IfNoneMatch="*",
            )
        except self._client_error as exc:
            code, status = self._error_info(exc)
            if status != 412 or code not in {"PreconditionFailed", "412"}:
                raise
            if self.get_object_sync(key) != body:
                raise ValueError("immutable P2 key already contains different bytes") from exc

    def delete_object_sync(self, key: str) -> None:
        self._ensure_open()
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except self._client_error as exc:
            if not self._missing(exc):
                raise

    def read_range_sync(self, key: str, start: int, end: int) -> bytes:
        self._ensure_open()
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError("invalid byte range")
        if end - start > self.config.max_range_bytes:
            raise ValueError("byte range exceeds configured limit")
        try:
            if start == end:
                response = self.client.head_object(Bucket=self.bucket, Key=key)
                if end > self._length(response):
                    raise ValueError("byte range exceeds object")
                return b""
            response = self.client.get_object(
                Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end - 1}"
            )
        except self._client_error as exc:
            if self._missing(exc):
                raise FileNotFoundError(key) from exc
            if self._error_info(exc)[1] == 416:
                raise ValueError("byte range exceeds object") from exc
            raise
        return self._consume(response, byte_range=(start, end))

    async def get_object(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self.get_object_sync, key)

    async def put_object(self, key: str, body: bytes) -> None:
        await asyncio.to_thread(self.put_object_sync, key, body)

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(self.delete_object_sync, key)

    async def read_range(self, key: str, start: int, end: int) -> bytes:
        return await asyncio.to_thread(self.read_range_sync, key, start, end)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.client.close()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    def __enter__(self) -> "CephP2":
        self._ensure_open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
