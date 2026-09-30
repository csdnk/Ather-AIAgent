"""Download the pinned official development CLI, verify archive SHA256, extract one executable."""

import argparse
import hashlib
import io
import os
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.9.1"
ARCHIVES = {
    "Windows": (
        "windows_amd64.zip",
        "babb65844835045c91fb98930fe10b81de28db80c8c609180473d2c0b18c7589",
    ),
    "Linux": (
        "linux_amd64.tar.gz",
        "09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    if platform.machine().lower() not in {"amd64", "x86_64"} or platform.system() not in ARCHIVES:
        parser.error("this pinned package supports Windows/Linux AMD64")
    archive, digest = ARCHIVES[platform.system()]
    url = f"https://github.com/temporalio/cli/releases/download/v{VERSION}/temporal_cli_{VERSION}_{archive}"
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Temporal CLI release checksum differs")
    name = "temporal.exe" if os.name == "nt" else "temporal"
    if archive.endswith("zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            binary = package.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw)) as package:
            member = package.getmember(name)
            if not member.isfile():
                raise ValueError("Temporal CLI archive member is not a regular file")
            binary = package.extractfile(member).read()
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / name
    if path.exists() and path.read_bytes() != binary:
        raise ValueError("existing executable differs; choose a new directory")
    path.write_bytes(binary)
    path.chmod(0o755)
    print(path.resolve())


if __name__ == "__main__":
    main()
