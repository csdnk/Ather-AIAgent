"""Cold authenticated PostgreSQL startup may exceed a short request deadline."""

import select
import socket
import threading

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_postgres_observability import dsns as dsns

from aether_agent_memory.runtime.foundation.postgres_telemetry import PostgresTelemetry


def test_log_pool_waits_for_real_delayed_tls_connections(tmp_path, dsns):
    """Delay actual encrypted transport, retaining real PG auth and SQL execution."""
    original = conninfo_to_dict(dsns["state"])
    target = (original.get("hostaddr", original["host"]), int(original.get("port", "5432")))
    stopping = threading.Event()
    threads = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        port = listener.getsockname()[1]

        def forward(client):
            with client:
                if stopping.wait(4):
                    return
                with socket.create_connection(target, timeout=20) as upstream:
                    while not stopping.is_set():
                        readable, _, _ = select.select([client, upstream], [], [], 0.1)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is client else client).sendall(data)

        def accept():
            while not stopping.is_set():
                try:
                    client, _ = listener.accept()
                except TimeoutError:
                    continue
                thread = threading.Thread(target=forward, args=(client,), daemon=True)
                threads.append(thread)
                thread.start()

        acceptor = threading.Thread(target=accept, daemon=True)
        acceptor.start()
        telemetry = None
        try:
            delayed = make_conninfo(
                dsns["state"], hostaddr="127.0.0.1", port=port, connect_timeout=20
            )
            telemetry = PostgresTelemetry(delayed, tmp_path / "unused")
            assert telemetry.probe(write=True)["reason"] == "write_rollback"
            assert not list(tmp_path.rglob("*.db"))
        finally:
            if telemetry is not None:
                telemetry.close()
            stopping.set()
            acceptor.join(timeout=2)
            for thread in threads:
                thread.join(timeout=2)
