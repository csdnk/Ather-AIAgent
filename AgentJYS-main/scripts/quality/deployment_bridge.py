"""Apifox loopback orchestration of the fixed existing-deployment supervisor."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from benchmark_bridge import Manager, handler
from post_deploy import execute


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--allow-existing-model", action="store_true")
    a = p.parse_args()
    config = json.loads(a.config.read_text(encoding="utf-8-sig"))
    token = Path(config["token_file"]).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("invalid local token")
    server = ThreadingHTTPServer(("127.0.0.1", 14884), BaseHTTPRequestHandler)

    def run(profile, jobdir):
        report = execute(config, jobdir / "regression", a.allow_existing_model)
        report["status"] = report["execution_status"]
        report["reason"] = (
            "See records: failure, missing stages and cleanup are reported independently"
        )
        return report

    manager = Manager(
        config["bridge_results"], run, max_jobs=1, profiles=("deployment-regression",)
    )
    server.RequestHandlerClass = handler(manager, token)
    timer = threading.Timer(7200, server.shutdown)
    timer.daemon = True
    timer.start()
    print("Aether deployment regression bridge ready: loopback14884; 1 new job; 2h", flush=True)
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()


if __name__ == "__main__":
    main()
