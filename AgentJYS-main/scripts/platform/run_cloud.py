"""Run the cloud Agent with explicit trusted settings and a single session owner."""

import argparse
from pathlib import Path

import uvicorn

from aether_platform.auth.bff import create_cloud_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
uvicorn.run(create_cloud_app(args.config), host="0.0.0.0", port=19010,
            access_log=False, proxy_headers=False)
