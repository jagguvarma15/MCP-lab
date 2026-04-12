"""
Conformance Tests -- does the server behave according to the MCP spec?

These tests validate that an MCP server correctly implements the protocol:
JSON-RPC 2.0 compliance, proper lifecycle management, capability negotiation,
and error handling.

Run: pytest tests/conformance/ -v
"""

import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from harness import MockMCPClient, ServerBehaviors


SERVER_CMD = f"{sys.executable} -m harness.mock_server"


@pytest.fixture
def client():
    c = MockMCPClient.over_stdio(SERVER_CMD)
    yield c
    c.shutdown()


# ---------------------------------------------------------------------------
# Test: JSON-RPC 2.0 Compliance
# ---------------------------------------------------------------------------

class TestJsonRpcCompliance:
    """Verify the server speaks valid JSON-RPC 2.0."""

    def test_response_has_jsonrpc_field(self, client):
        """Every response must include jsonrpc: '2.0'."""
        resp = client.initialize()
        assert resp.jsonrpc_version == "2.0", (
            f"Expected jsonrpc '2.0', got '{resp.jsonrpc_version}'"
        )

    def test_response_echoes_request_id(self, client):
        """Response ID must match request ID."""
        resp = client.initialize()
        assert resp.has_id
        assert resp.id == 1  # First request gets id=1

    def test_no_extra_fields(self, client):
        """Responses should not contain fields beyond jsonrpc, id, result/error."""
        resp = client.initialize()
        extras = resp.extra_fields
        assert len(extras) == 0, (
            f"Response contains unexpected fields: {extras}"
        )

    def test_error_response_format(self, client):
        """Errors must follow JSON-RPC error object format."""
        resp = client.send("nonexistent/method")
        assert resp.is_error
        err = resp.error
        assert "code" in err, "Error must have 'code' field"
        assert "message" in err, "Error must have 'message' field"
        assert isinstance(err["code"], int), "Error code must be integer"

    def test_notification_gets_no_response(self, client):
        """JSON-RPC notifications (no ID) should not produce a response."""
        client.initialize()
        # notifications/initialized is a notification -- no response expected
        # We already sent it in initialize(), so just verify no error
        client.assert_no_errors()

    def test_malformed_request_returns_parse_error(self, client):
        """Sending invalid JSON should get a -32700 Parse Error."""
        if client._process and client._process.stdin and client._process.stdout:
            client._process.stdin.write("NOT VALID JSON\n")
            client._process.stdin.flush()
            line = client._process.stdout.readline()
            resp = json.loads(line)
            assert resp.get("error", {}).get("code") == -32700


# ---------------------------------------------------------------------------
# Test: MCP Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    """Verify proper MCP initialization and capability negotiation."""

    def test_initialize_returns_server_info(self, client):
        """Initialize response must include serverInfo and protocolVersion."""
        resp = client.initialize()
        result = resp.result
        assert "serverInfo" in result, "Missing serverInfo in initialize response"
        assert "name" in result["serverInfo"], "serverInfo must have name"
        assert "protocolVersion" in result, "Missing protocolVersion"

    def test_initialize_returns_capabilities(self, client):
        """Initialize response must declare server capabilities."""
        resp = client.initialize()
        result = resp.result
        assert "capabilities" in result, "Missing capabilities in initialize response"

    def test_tools_list_before_initialize(self):
        """Calling tools/list before initialize -- server should reject or handle."""
        c = MockMCPClient.over_stdio(SERVER_CMD)
        try:
            # Skip initialize, go straight to tools/list
            resp = c.list_tools()
            # The spec says servers SHOULD reject pre-init requests,
            # but many don't. Document the behavior either way.
            assert resp is not None, "Server should respond (even with error)"
        finally:
            c.shutdown()

    def test_protocol_version_negotiation(self, client):
        """Client sends desired version, server responds with supported version."""
        resp = client.initialize(protocol_version="1999-01-01")
        # Server should respond with its own version, not crash
        assert "protocolVersion" in resp.result

    def test_ping(self, client):
        """Server must respond to ping."""
        client.initialize()
        resp = client.ping()
        assert not resp.is_error, "Ping should succeed"


# ---------------------------------------------------------------------------
# Test: Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Verify proper error responses for invalid requests."""

    def test_unknown_method(self, client):
        """Unknown method should return -32601 Method Not Found."""
        client.initialize()
        resp = client.send("tools/nonexistent")
        assert resp.is_error
        assert resp.error["code"] == -32601

    def test_unknown_tool_call(self, client):
        """Calling a non-existent tool should return an error."""
        client.initialize()
        resp = client.call_tool("tool_that_does_not_exist", {})
        # Could be an error response or a result with an error field
        # The error could surface as a JSON-RPC error OR as an error in the result body
        result_str = json.dumps(resp.result).lower()
        has_error = resp.is_error or "error" in result_str or "unknown" in result_str
        assert has_error, (
            "Calling nonexistent tool should produce some kind of error, "
            f"but got: {resp.result}"
        )


# ---------------------------------------------------------------------------
# Test: Protocol Violations (what breaks when the server misbehaves)
# ---------------------------------------------------------------------------

class TestProtocolViolations:
    """Test client resilience against protocol-violating servers."""

    def test_wrong_jsonrpc_version(self):
        """Server responds with jsonrpc: '1.0' instead of '2.0'."""
        cmd = f"{sys.executable} -m harness.mock_server --wrong-version"
        with MockMCPClient.over_stdio(cmd) as client:
            resp = client.initialize()
            assert resp.jsonrpc_version == "1.0", "Server should return wrong version"
            # A conformant client should reject this

    def test_missing_id_in_response(self):
        """Server responds without the request ID."""
        cmd = f"{sys.executable} -m harness.mock_server --omit-id"
        with MockMCPClient.over_stdio(cmd) as client:
            resp = client.initialize()
            assert not resp.has_id, "Server should omit ID"
            # A conformant client can't match this response to a request
