import os
import subprocess
import sys

import pytest

from aether_agent_memory.runtime.temporal.locking import DirectoryLock


def test_second_process_cannot_own_business_directory(tmp_path):
    lock = DirectoryLock()
    lock.acquire(tmp_path)
    script = (
        "from pathlib import Path; "
        "from aether_agent_memory.runtime.temporal.locking import DirectoryLock; "
        "import sys; DirectoryLock().acquire(Path(sys.argv[1]))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            env=os.environ,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode != 0
        assert "already owned" in result.stderr
        with pytest.raises(RuntimeError, match="already owned"):
            DirectoryLock().acquire(tmp_path)
    finally:
        lock.release()
    other = DirectoryLock()
    other.acquire(tmp_path)
    other.release()
