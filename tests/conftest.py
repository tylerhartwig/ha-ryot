"""Fixtures: an isolated Ryot (scripts/ryot_stack.py) and a fresh user per test.

Home Assistant itself is the in-process test instance from
pytest-homeassistant-custom-component; nothing here talks to the live HA or
to your real Ryot.
"""

from __future__ import annotations

from collections.abc import Generator
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from .ryot_seed import SeededUser, create_user, seed_standard

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(scope="session")
def ryot_stack() -> Generator[dict[str, str]]:
    if url := os.environ.get("RYOT_TEST_URL"):
        yield {"url": url, "admin_token": os.environ["RYOT_TEST_ADMIN_TOKEN"]}
        return
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "ryot_stack.py")],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    if not line:
        proc.wait()
        pytest.fail(f"isolated Ryot failed to start (exit {proc.returncode}); see stderr above")
    info = json.loads(line)
    try:
        yield info
    finally:
        proc.terminate()
        proc.wait(30)


@pytest.fixture
def ryot_user(socket_enabled, ryot_stack) -> SeededUser:
    """A brand-new Ryot user with no data. HA tests block sockets by default;
    the only thing these tests talk to is the loopback stack."""
    return create_user(ryot_stack["url"], ryot_stack["admin_token"])


@pytest.fixture
def seeded_user(ryot_user) -> SeededUser:
    seed_standard(ryot_user)
    return ryot_user
