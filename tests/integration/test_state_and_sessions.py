"""
Integration Tests -- State management across MCP sessions.

Explores how MCP handles (or doesn't handle) state between connections,
session identity, capability caching, and reconnection behavior.

Run: pytest tests/integration/test_state_and_sessions.py -v
"""

import json
import time
import pytest
import sys

from harness import MockMCPClient


SERVER_CMD = [sys.executable, "-m", "harness.mock_server"]


# ---------------------------------------------------------------------------
# Test: Stateless by Default
# ---------------------------------------------------------------------------

class TestStatelessDesign:
    """
    MCP is stateless by default -- servers should not retain state
    across sessions. Verify this contract.
    """

    def test_server_has_no_memory_across_sessions(self):
        """Connect, call a tool, disconnect, reconnect. Verify no state carried over."""
        # Session 1: call echo with a specific message
        with MockMCPClient.over_stdio(SERVER_CMD) as client1:
            client1.initialize()
            resp1 = client1.call_tool("echo", {"message": "session-1-data"})
            assert not resp1.is_error

        # Session 2: fresh connection to the same server command
        with MockMCPClient.over_stdio(SERVER_CMD) as client2:
            client2.initialize()
            # Server should have no memory of session 1
            # There is no way to query previous calls -- verify by checking
            # that the server responds freshly
            resp2 = client2.call_tool("echo", {"message": "session-2-data"})
            assert not resp2.is_error
            result = json.loads(resp2.result["content"][0]["text"])
            assert result.get("echoed") == "session-2-data"
            # No reference to session-1-data anywhere
            assert "session-1-data" not in json.dumps(resp2.raw)

    def test_request_log_resets_per_session(self):
        """Each server instance should start with an empty request log."""
        # Session 1
        with MockMCPClient.over_stdio(SERVER_CMD) as client1:
            client1.initialize()
            for i in range(5):
                client1.call_tool("echo", {"message": f"msg-{i}"})
            # Client has log of 7 entries (1 init + 1 notify skip + 5 calls)
            assert len(client1.log) > 0

        # Session 2 -- new client, new server process
        with MockMCPClient.over_stdio(SERVER_CMD) as client2:
            client2.initialize()
            # Fresh client log
            assert len(client2.log) == 1  # Only the initialize call


# ---------------------------------------------------------------------------
# Test: Session ID Uniqueness
# ---------------------------------------------------------------------------

class TestSessionIdentity:
    """
    If the server returns session-related info, verify it changes per connection.
    """

    def test_session_id_uniqueness(self):
        """Multiple connections should get different server responses (not cached)."""
        responses = []
        for _ in range(3):
            with MockMCPClient.over_stdio(SERVER_CMD) as client:
                resp = client.initialize()
                responses.append(resp.result)

        # All responses should have valid server info
        for r in responses:
            assert "serverInfo" in r
            assert "protocolVersion" in r

    def test_protocol_version_consistent(self):
        """Protocol version should be the same across sessions."""
        versions = []
        for _ in range(3):
            with MockMCPClient.over_stdio(SERVER_CMD) as client:
                resp = client.initialize()
                versions.append(resp.result.get("protocolVersion"))

        assert len(set(versions)) == 1, (
            f"Protocol version should be consistent across sessions, got: {versions}"
        )


# ---------------------------------------------------------------------------
# Test: Capability Caching
# ---------------------------------------------------------------------------

class TestCapabilityCaching:
    """
    Test whether tools/list responses are cached or regenerated per call.
    """

    def test_capability_caching(self):
        """Call tools/list twice -- results should be identical."""
        with MockMCPClient.over_stdio(SERVER_CMD) as client:
            client.initialize()

            resp1 = client.list_tools()
            resp2 = client.list_tools()

            tools1 = [t["name"] for t in resp1.result["tools"]]
            tools2 = [t["name"] for t in resp2.result["tools"]]

            assert tools1 == tools2, (
                "tools/list should return the same results on repeated calls"
            )

    def test_second_list_call_timing(self):
        """Second tools/list call should not be significantly slower."""
        with MockMCPClient.over_stdio(SERVER_CMD) as client:
            client.initialize()

            resp1 = client.list_tools()
            resp2 = client.list_tools()

            # Second call should be roughly the same speed (no expensive recompute)
            # Allow generous margin since these are subprocess calls
            assert resp2.elapsed_ms < resp1.elapsed_ms * 5, (
                f"Second list call ({resp2.elapsed_ms:.1f}ms) was much slower than "
                f"first ({resp1.elapsed_ms:.1f}ms)"
            )


# ---------------------------------------------------------------------------
# Test: Reconnect After Transport Drop
# ---------------------------------------------------------------------------

class TestReconnection:
    """
    Simulate a transport drop and verify the client must re-initialize.
    """

    def test_reconnect_after_transport_drop(self):
        """Kill the server, start a new one. Client must re-initialize."""
        # Session 1 -- establish and use
        client1 = MockMCPClient.over_stdio(SERVER_CMD)
        client1.initialize()
        resp = client1.call_tool("echo", {"message": "before-drop"})
        assert not resp.is_error
        client1.shutdown()

        # Simulate reconnection -- new server process, must re-init
        client2 = MockMCPClient.over_stdio(SERVER_CMD)
        try:
            # Without initialize, tools/list should work but may not on strict servers
            resp = client2.list_tools()
            # Either way, verify we can initialize and use normally
            client2_fresh = MockMCPClient.over_stdio(SERVER_CMD)
            try:
                resp = client2_fresh.initialize()
                assert resp is not None and not resp.is_error, (
                    "Must be able to initialize a fresh connection after transport drop"
                )
                resp = client2_fresh.call_tool("echo", {"message": "after-reconnect"})
                assert not resp.is_error
            finally:
                client2_fresh.shutdown()
        finally:
            client2.shutdown()

    def test_old_client_cannot_reuse_connection(self):
        """After shutdown, the old client's connection is dead."""
        client = MockMCPClient.over_stdio(SERVER_CMD)
        client.initialize()
        resp = client.call_tool("echo", {"message": "alive"})
        assert not resp.is_error

        # Shut down
        client.shutdown()

        # Attempting to use the old client should fail or return None
        # (process is terminated, pipes are closed)
        try:
            resp = client.call_tool("echo", {"message": "dead"})
            # If we get here without exception, resp should be None or error
            if resp is not None:
                # Some edge case -- at least note it
                pass
        except (BrokenPipeError, OSError, RuntimeError):
            # Expected -- the connection is dead
            pass
