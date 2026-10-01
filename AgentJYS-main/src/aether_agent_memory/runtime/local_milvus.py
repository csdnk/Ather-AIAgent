"""Foreground official Milvus Lite launcher for an explicitly selected local directory.

Milvus Lite 3.2.1's FAISS persistence needs an ASCII path on Windows. A checked
Win32 short name is an alias of the same directory, never a relocated database.
The official CLI owns the server and its lock; Ctrl+C stops a foreground run.
"""

import argparse
import os
import sys
from pathlib import Path


def _windows_short_path(path: str) -> str:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short_path = kernel.GetShortPathNameW
    get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_short_path.restype = wintypes.DWORD
    needed = get_short_path(path, None, 0)
    if not needed:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(needed)
    written = get_short_path(path, buffer, needed)
    if not written or written >= needed:
        raise OSError("Windows could not obtain a stable short directory name")
    return buffer.value


def prepare_data_directory(directory: str | Path) -> str:
    """Create the selected directory and return its safe, identity-checked spelling."""
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("Milvus data path must be a directory")
    original = str(directory)
    if sys.platform != "win32" or original.isascii():
        return original
    try:
        alias = _windows_short_path(original)
        if alias.isascii() and os.path.samefile(original, alias):
            # Do not resolve this alias: resolve() can expand the Unicode name again.
            return alias
    except OSError as exc:
        raise ValueError(
            "Milvus Lite needs an ASCII directory on Windows; choose an ASCII path "
            "because this directory has no usable short-name alias"
        ) from exc
    raise ValueError(
        "Milvus Lite needs an ASCII alias of the same directory on Windows; "
        "choose an ASCII directory when short names are unavailable"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19530)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    directory = prepare_data_directory(args.directory)
    from milvus_lite.cmdline import main as official_main

    return int(
        official_main(
            [
                "server",
                "--data-dir",
                directory,
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--max-workers",
                "1",
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
