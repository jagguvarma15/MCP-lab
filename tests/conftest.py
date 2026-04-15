"""
Shared pytest fixtures for MCP test suites.

Provides reusable fixtures for single-server clients, initialized clients,
multi-server clients, and the make_server_cmd helper used across test modules.
"""

import sys
import pytest

from harness import MockMCPClient
from harness.multi_client import MultiServerClient


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
