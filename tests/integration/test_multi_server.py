"""
Integration Tests -- Multi-server composition.

Tests what happens when a client talks to 2+ MCP servers simultaneously.
This is the real-world scenario that most single-server test suites miss.

Run: pytest tests/integration/test_multi_server.py -v
"""

import json
import time
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from harness.multi_client import MultiServerClient
from harness import MockMCPClient


SERVER_CMD = [sys.executable, "-m", "harness.mock_server"]


def make_server_cmd(**behavior_args) -> list[str]:
    """Build a CLI command for a server with specific behaviors."""
    cmd = [sys.executable, "-m", "harness.mock_server"]
    for key, value in behavior_args.items():
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool) and value:
            cmd.append(flag)
        elif not isinstance(value, bool):
            cmd.extend([flag, str(value)])
    return cmd


# ---------------------------------------------------------------------------
# Test: Tool Namespace Collisions
# ---------------------------------------------------------------------------

class TestToolNamespaceCollision:
    """
    Two servers register a tool with the same name.
    Which one wins? Does the client error, pick one silently, or expose both?
    """

    def test_tool_namespace_collision(self):
        """Two servers each have an 'echo' tool. Verify collision is detectable."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            tools_by_server = multi.list_all_tools()

            # Both servers should have tools
            assert len(tools_by_server) == 2

            # Check for collisions
            collisions = multi.get_tool_collisions()
            assert len(collisions) > 0, (
                "Expected tool name collisions when two servers have the same tools"
            )
            assert "echo" in collisions, (
                "Both servers have 'echo' but collision was not detected"
            )

    def test_collision_flat_list_shows_duplicates(self):
        """Flat tool list should contain duplicates from both servers."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            multi.list_all_tools()
            flat = multi.get_all_tools_flat()

            echo_count = sum(1 for t in flat if t.get("name") == "echo")
            assert echo_count == 2, (
                f"Expected 2 'echo' tools in flat list, got {echo_count}"
            )


# ---------------------------------------------------------------------------
# Test: Cross-Server Tool Call Routing
# ---------------------------------------------------------------------------

class TestCrossServerRouting:
    """
    Client has servers A (tools: echo, calculator) and B (tools: echo + shadow read_file).
    Verify tool dispatch is correctly mapped per-server.
    """

    def test_cross_server_tool_call_routing(self):
        """Call a tool that only exists on one server -- verify correct routing."""
        cmd_a = SERVER_CMD  # Has echo, calculator, slow_operation
        cmd_b = make_server_cmd(shadow_tool="read_file")  # Has echo + read_file shadow

        with MultiServerClient([cmd_a, cmd_b]) as multi:
            multi.initialize_all()
            multi.list_all_tools()

            # read_file only exists on server B (as a shadow tool)
            server = multi.find_tool_server("read_file")
            assert server is not None, "read_file tool should be found"
            assert server is multi.servers[1], (
                "read_file should route to server B (index 1)"
            )

    def test_explicit_server_routing(self):
        """Call the same tool name on different servers explicitly."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            multi.list_all_tools()

            # Call echo on server 0
            resp_a = multi.call_tool_on_server(0, "echo", {"message": "from_a"})
            assert resp_a is not None
            assert not resp_a.is_error

            # Call echo on server 1
            resp_b = multi.call_tool_on_server(1, "echo", {"message": "from_b"})
            assert resp_b is not None
            assert not resp_b.is_error


# ---------------------------------------------------------------------------
# Test: One Server Crashes, Other Survives
# ---------------------------------------------------------------------------

class TestServerCrashResilience:
    """
    Start two servers. Kill one mid-conversation.
    Verify tools from the surviving server still work.
    """

    def test_one_server_crashes_other_survives(self):
        """Kill server 0, verify server 1 still responds."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            multi.list_all_tools()

            # Verify both work initially
            resp_0 = multi.call_tool_on_server(0, "echo", {"message": "alive_0"})
            resp_1 = multi.call_tool_on_server(1, "echo", {"message": "alive_1"})
            assert resp_0 is not None and not resp_0.is_error
            assert resp_1 is not None and not resp_1.is_error

            # Kill server 0
            multi.shutdown_server(0)

            # Server 1 should still work
            resp_1_after = multi.call_tool_on_server(1, "echo", {"message": "still_alive"})
            assert resp_1_after is not None, (
                "Server 1 should still respond after server 0 is killed"
            )
            assert not resp_1_after.is_error


# ---------------------------------------------------------------------------
# Test: Combined Tool List Ordering
# ---------------------------------------------------------------------------

class TestCombinedToolList:
    """
    List tools from multiple servers.
    Verify the aggregated list is deterministic and complete.
    """

    def test_combined_tool_list_ordering(self):
        """Tool list from 3 servers should be deterministic."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()

            # Get tools twice
            tools_1 = multi.list_all_tools()
            tools_2 = multi.list_all_tools()

            # Same keys
            assert set(tools_1.keys()) == set(tools_2.keys())

            # Same tools per server
            for key in tools_1:
                names_1 = [t["name"] for t in tools_1[key]]
                names_2 = [t["name"] for t in tools_2[key]]
                assert names_1 == names_2, (
                    f"Tool ordering for {key} is not deterministic"
                )

    def test_no_tools_silently_dropped(self):
        """All tools from all servers should appear in the aggregated list."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            multi.list_all_tools()
            flat = multi.get_all_tools_flat()

            # Each server has 3 tools (echo, calculator, slow_operation)
            # 3 servers = 9 total tools (with duplicates)
            assert len(flat) == 9, (
                f"Expected 9 tools (3 per server), got {len(flat)}"
            )


# ---------------------------------------------------------------------------
# Test: Server Startup Race
# ---------------------------------------------------------------------------

class TestServerStartupRace:
    """
    Initialize two servers concurrently.
    Verify both complete handshake correctly.
    """

    def test_server_startup_race(self):
        """Both servers should initialize successfully."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            responses = multi.initialize_all()

            for i, resp in enumerate(responses):
                assert resp is not None, f"Server {i} returned no response"
                assert not resp.is_error, f"Server {i} returned error: {resp.error}"
                assert resp.result.get("serverInfo") is not None, (
                    f"Server {i} missing serverInfo"
                )

    def test_both_servers_have_tools_after_init(self):
        """After initialization, both servers should list their tools."""
        with MultiServerClient([SERVER_CMD, SERVER_CMD]) as multi:
            multi.initialize_all()
            tools = multi.list_all_tools()

            for name, tool_list in tools.items():
                assert len(tool_list) > 0, (
                    f"Server '{name}' has no tools after initialization"
                )
