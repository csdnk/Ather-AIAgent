"""Run the isolated P0 BFF; access logs are off to keep OAuth codes out of logs."""

import argparse
from pathlib import Path

import uvicorn

from aether_platform.auth.bff import create_lab_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
uvicorn.run(create_lab_app(args.config), host="127.0.0.1", port=19010, access_log=False)
