"""Real TLS gRPC transport with a controlled server; not P2 backend acceptance."""

import asyncio
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from aether_agent_memory.p2.client import P2GrpcClient
from aether_agent_memory.p2.generated import aether_engine_pb2 as pb
from aether_agent_memory.p2.generated import aether_engine_pb2_grpc as pb_grpc

pytestmark = pytest.mark.integration


def certificate(name, *, issuer=None, issuer_key=None, purpose=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    stamp = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(stamp - timedelta(minutes=1))
        .not_valid_after(stamp + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True)
    )
    if purpose:
        builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
    if purpose == ExtendedKeyUsageOID.SERVER_AUTH:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    cert = builder.sign(issuer_key or key, hashes.SHA256())
    return key, cert


def key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture
def tls_server(tmp_path):
    ca_key, ca = certificate("P3 test CA")
    server_key, server_cert = certificate(
        "server", issuer=ca, issuer_key=ca_key, purpose=ExtendedKeyUsageOID.SERVER_AUTH
    )
    client_key, client_cert = certificate(
        "client", issuer=ca, issuer_key=ca_key, purpose=ExtendedKeyUsageOID.CLIENT_AUTH
    )
    _, other_ca = certificate("untrusted CA")
    pem = serialization.Encoding.PEM
    paths = {}
    for name, data in {
        "ca_file": ca.public_bytes(pem),
        "cert_file": client_cert.public_bytes(pem),
        "key_file": key_pem(client_key),
        "wrong_ca": other_ca.public_bytes(pem),
    }.items():
        paths[name] = tmp_path / (name + ".pem")
        paths[name].write_bytes(data)

    class Objects(pb_grpc.ObjectServiceServicer):
        def GetObject(self, request, context):  # noqa: N802
            if dict(context.invocation_metadata()).get("authorization") != "Bearer test-token":
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "test token required")
            return pb.ObjectBytes(data=b"TLS protected bytes")

    with ThreadPoolExecutor(max_workers=2) as pool:
        server = grpc.server(pool)
        pb_grpc.add_ObjectServiceServicer_to_server(Objects(), server)
        port = server.add_secure_port(
            "127.0.0.1:0",
            grpc.ssl_server_credentials(
                [(key_pem(server_key), server_cert.public_bytes(pem))],
                root_certificates=ca.public_bytes(pem),
                require_client_auth=True,
            ),
        )
        assert port > 0
        server.start()
        try:
            yield f"127.0.0.1:{port}", paths
        finally:
            server.stop(0).wait()


async def test_tls_mtls_and_token_apply_to_both_async_and_sync_channels(tls_server):
    endpoint, paths = tls_server
    client = P2GrpcClient(
        endpoint,
        secure=True,
        token="test-token",
        timeout_seconds=1,
        **{name: paths[name] for name in ("ca_file", "cert_file", "key_file")},
    )
    try:
        assert await client.get_object("key") == b"TLS protected bytes"
        assert await asyncio.to_thread(client.get_object_sync, "key") == b"TLS protected bytes"
    finally:
        await client.close()


@pytest.mark.parametrize("failure", ["wrong_ca", "missing_client_cert", "missing_token"])
async def test_tls_rejects_invalid_trust_client_identity_or_token(tls_server, failure):
    endpoint, paths = tls_server
    settings = {name: paths[name] for name in ("ca_file", "cert_file", "key_file")}
    if failure == "wrong_ca":
        settings["ca_file"] = paths["wrong_ca"]
    if failure == "missing_client_cert":
        settings.pop("cert_file")
        settings.pop("key_file")
    client = P2GrpcClient(
        endpoint,
        secure=True,
        timeout_seconds=1,
        token="" if failure == "missing_token" else "test-token",
        **settings,
    )
    try:
        with pytest.raises(grpc.RpcError) as error:
            await client.get_object("key")
        assert error.value.code() == (
            grpc.StatusCode.UNAUTHENTICATED
            if failure == "missing_token"
            else grpc.StatusCode.UNAVAILABLE
        )
        with pytest.raises(grpc.RpcError):
            await asyncio.to_thread(client.get_object_sync, "key")
    finally:
        await client.close()


def test_token_cannot_be_sent_over_plaintext():
    with pytest.raises(ValueError, match="secure transport"):
        P2GrpcClient("127.0.0.1:1", token="test-token")
