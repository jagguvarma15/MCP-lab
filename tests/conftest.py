"""
Shared pytest fixtures for MCP test suites.

Provides reusable fixtures for single-server clients, initialized clients,
multi-server clients, and the make_server_cmd helper used across test modules.

Also auto-applies category/dependency markers to tests based on their path
so users can select subsets without memorizing directory layout:

    pytest -m "security and not slow"
    pytest -m "conformance or integration"
    pytest -m "not requires_http"          # skip HTTP tests without aiohttp
"""

import sys
import pytest

from harness import MockMCPClient
from harness.multi_client import MultiServerClient


# ---------------------------------------------------------------------------
# Marker auto-application
# ---------------------------------------------------------------------------

# Category: directory segment -> markers to apply to every test under it.
# Order matters: the security marker is also applied to auth-delegation tests
# under integration/ via the name-based rules below.
_PATH_MARKERS: dict[str, tuple[str, ...]] = {
    "/tests/conformance/": ("conformance",),
    "/tests/security/":    ("security",),
    "/tests/fuzzing/":     ("fuzz", "slow", "requires_hypothesis"),
    "/tests/integration/": ("integration",),
    "/tests/transport/":   ("transport",),
    "/tests/evaluation/":  ("evaluation",),
}

# Filename-specific extra markers.
_FILE_MARKERS: dict[str, tuple[str, ...]] = {
    "test_http.py":            ("requires_http",),
    "test_auth_delegation.py": ("security",),  # auth is a security concern
}

# Individual slow tests outside the fuzzing suite (by test nodeid substring).
_SLOW_NAMES: tuple[str, ...] = (
    "test_high_latency_still_works",
)


def pytest_collection_modifyitems(config, items):
    """Auto-apply category/speed/dependency markers based on test location."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")

        for segment, marks in _PATH_MARKERS.items():
            if segment in path:
                for mark in marks:
                    item.add_marker(mark)

        filename = path.rsplit("/", 1)[-1]
        for mark in _FILE_MARKERS.get(filename, ()):
            item.add_marker(mark)

        if any(name in item.nodeid for name in _SLOW_NAMES):
            item.add_marker("slow")


SERVER_CMD = [sys.executable, "-m", "harness.mock_server"]


def make_server_cmd(**behavior_args) -> list[str]:
    """Build a CLI command for a server with specific behaviors.

    Keyword arguments are converted to CLI flags:
        make_server_cmd(error_rate=0.5)  -> [..., "--error-rate", "0.5"]
        make_server_cmd(wrong_version=True) -> [..., "--wrong-version"]
    """
    cmd = [sys.executable, "-m", "harness.mock_server"]
    for key, value in behavior_args.items():
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool) and value:
            cmd.append(flag)
        elif not isinstance(value, bool):
            cmd.extend([flag, str(value)])
    return cmd


@pytest.fixture
def mcp_client():
    """A MockMCPClient connected to a vanilla mock server (not yet initialized)."""
    c = MockMCPClient.over_stdio(SERVER_CMD)
    yield c
    c.shutdown()


@pytest.fixture
def initialized_client():
    """A MockMCPClient that has already completed the initialize handshake."""
    c = MockMCPClient.over_stdio(SERVER_CMD)
    c.initialize()
    yield c
    c.shutdown()


@pytest.fixture
def multi_client():
    """A MultiServerClient connected to two vanilla mock servers (not yet initialized)."""
    with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
        yield multi
