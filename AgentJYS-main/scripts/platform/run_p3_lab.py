"""Local P3 runtime with scoped identity projection and verified TLS relay routing."""

import argparse
import json
import os
import socket
import threading
from pathlib import Path

import uvicorn
import yaml
from prepare_p3_lab import publish_identities

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--platform", type=Path, required=True)
args = parser.parse_args()
directory = args.directory.resolve()
platform = json.loads(args.platform.read_text(encoding="utf-8-sig"))
os.environ.update(json.loads((directory / "environment.private.json").read_text()))
os.environ.update(HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="2")
os.environ["NO_PROXY"] = ",".join(
    filter(None, [os.environ.get("NO_PROXY", ""), "localhost", "127.0.0.1", "::1"])
)
config = yaml.safe_load((directory / "service.yaml").read_text())
redis_host = config["azure_storage"]["redis"]["host"]
original_resolve = socket.getaddrinfo


def relay_resolve(host, port, *values, **kwargs):
    # Only this process and this explicit relay endpoint are redirected. Redis
    # keeps the original hostname for TLS certificate validation.
    if host == redis_host and int(port) == 46380:
        host = "127.0.0.1"
    return original_resolve(host, port, *values, **kwargs)


socket.getaddrinfo = relay_resolve
publish_identities(platform, directory)


def sync():
    while not stopped.wait(2):
        try:
            publish_identities(platform, directory)
        except Exception:
            # BFF continues to revalidate directory state on every user request.
            print("P3 identity projection unavailable", flush=True)


stopped = threading.Event()
threading.Thread(target=sync, daemon=True).start()
os.environ["AETHER_SERVICE_CONFIG"] = str(directory / "service.yaml")
try:
    uvicorn.run(
        "aether_agent_memory.runtime.flows.application:application",
        factory=True,
        host="127.0.0.1",
        port=19020,
        access_log=False,
    )
finally:
    stopped.set()
