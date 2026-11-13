import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("AEGIS_BACKOFF_SCALE", "0.01")

CHROME = os.environ.get("AEGIS_CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def site():
    """Serve tests/site (a small company website with deliberately flaky UI)."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                            cwd=Path(__file__).parent / "site", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.7)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()


@pytest.fixture
def offline_runtime(tmp_path):
    from aegis.config import Settings
    from aegis.core.store import Store
    from aegis.services import RunOptions, Runtime

    def make(**opts):
        s = Settings()
        s.db_path = str(tmp_path / "aegis.db")
        s.chrome_path = CHROME
        store = Store(s.db_path)
        return Runtime(s, RunOptions(mode="offline", **opts), store=store)

    return make


needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium available")
