"""
Transport Tests -- HTTP/SSE endpoint coverage for the MCP HTTP server.

Mirrors the stdio transport tests (test_latency.py) but targets the
HTTP/SSE endpoints exposed by harness.http_server.HTTPMCPServer.

Requires: aiohttp (pip install mcp-lab[http])

Run: pytest tests/transport/test_http.py -v
"""

import json
import time
import statistics
import pytest
import pytest_asyncio

aiohttp = pytest.importorskip("aiohttp", reason="aiohttp required for HTTP transport tests")

from aiohttp.test_utils import TestClient, TestServer

from harness.http_server import HTTPMCPServer
from harness.mock_server import (
    MockMCPServer,
    ServerBehaviors,
    echo_tool,
    calculator_tool,
    slow_tool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_server(behaviors: ServerBehaviors | None = None) -> HTTPMCPServer:
    """Create an HTTPMCPServer with optional behavior overrides."""
    server = MockMCPServer(
        tools=[echo_tool, calculator_tool, slow_tool],
        behaviors=behaviors or ServerBehaviors(),
    )
    return HTTPMCPServer(server=server)


def _jsonrpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    """Build a JSON-RPC 2.0 request dict."""
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


_INIT_PARAMS = {
    "protocolVersion": "2025-03-26",
    "clientInfo": {"name": "test-http", "version": "0.1.0"},
    "capabilities": {},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def http_client():
    """Test client wired to a vanilla HTTPMCPServer."""
    srv = _make_http_server()
    app = srv._create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest_asyncio.fixture
async def delayed_client():
    """Test client wired to an HTTPMCPServer with 100ms delay."""
    srv = _make_http_server(ServerBehaviors(delay_ms=100))
    app = srv._create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest_asyncio.fixture
async def error_client():
    """Test client wired to an HTTPMCPServer with 50% error rate."""
    srv = _make_http_server(ServerBehaviors(error_rate=0.5))
    app = srv._create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


# ---------------------------------------------------------------------------
# Test: Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Verify the /health endpoint responds correctly."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, http_client):
        resp = await http_client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["tool_count"] == 3

    @pytest.mark.asyncio
    async def test_health_includes_server_info(self, http_client):
        resp = await http_client.get("/health")
        body = await resp.json()
        assert "server_name" in body
        assert "server_version" in body


# ---------------------------------------------------------------------------
# Test: JSON-RPC over HTTP (POST /mcp)
# ---------------------------------------------------------------------------

class TestJsonRpcOverHttp:
    """Verify the /mcp endpoint handles JSON-RPC correctly."""

    @pytest.mark.asyncio
    async def test_initialize(self, http_client):
        """Initialize handshake over HTTP."""
        resp = await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))
        assert resp.status == 200
        body = await resp.json()
        assert body["jsonrpc"] == "2.0"
        assert "serverInfo" in body["result"]
        assert "protocolVersion" in body["result"]

    @pytest.mark.asyncio
    async def test_tools_list(self, http_client):
        """List tools after initialize."""
        await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))
        resp = await http_client.post("/mcp", json=_jsonrpc("tools/list", {}, req_id=2))
        body = await resp.json()
        assert not body.get("error")
        tool_names = [t["name"] for t in body["result"]["tools"]]
        assert "echo" in tool_names
        assert "calculator" in tool_names

    @pytest.mark.asyncio
    async def test_tool_call_echo(self, http_client):
        """Call the echo tool over HTTP."""
        await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))
        resp = await http_client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "echo", "arguments": {"message": "hello-http"}},
                req_id=2,
            ),
        )
        body = await resp.json()
        assert not body.get("error")
        parsed = json.loads(body["result"]["content"][0]["text"])
        assert parsed["echoed"] == "hello-http"

    @pytest.mark.asyncio
    async def test_tool_call_calculator(self, http_client):
        """Call the calculator tool over HTTP."""
        await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))
        resp = await http_client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "calculator", "arguments": {"expression": "2 + 3"}},
                req_id=2,
            ),
        )
        body = await resp.json()
        assert not body.get("error")

    @pytest.mark.asyncio
    async def test_ping(self, http_client):
        """Ping over HTTP."""
        resp = await http_client.post("/mcp", json=_jsonrpc("ping"))
        body = await resp.json()
        assert body["jsonrpc"] == "2.0"
        assert not body.get("error")

    @pytest.mark.asyncio
    async def test_unknown_method(self, http_client):
        """Unknown method should return JSON-RPC error."""
        await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))
        resp = await http_client.post(
            "/mcp", json=_jsonrpc("tools/nonexistent", {}, req_id=2)
        )
        body = await resp.json()
        assert body.get("error")
        assert body["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_malformed_json(self, http_client):
        """Invalid JSON should return parse error."""
        resp = await http_client.post(
            "/mcp",
            data="NOT VALID JSON",
            headers={"Content-Type": "application/json"},
        )
        body = await resp.json()
        assert body["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_notification_returns_204(self, http_client):
        """Notifications (no id) should get 204 No Content."""
        msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        resp = await http_client.post("/mcp", json=msg)
        assert resp.status == 204

    @pytest.mark.asyncio
    async def test_response_echoes_id(self, http_client):
        """Response id must match request id."""
        resp = await http_client.post("/mcp", json=_jsonrpc("ping", req_id=42))
        body = await resp.json()
        assert body["id"] == 42


# ---------------------------------------------------------------------------
# Test: HTTP latency profiling
# ---------------------------------------------------------------------------

class TestHttpLatency:
    """Measure round-trip latency over HTTP and compare with expectations."""

    @pytest.mark.asyncio
    async def test_initialize_latency(self, http_client):
        """Measure initialize handshake latency over HTTP."""
        timings = []
        for i in range(10):
            start = time.perf_counter()
            await http_client.post(
                "/mcp", json=_jsonrpc("initialize", _INIT_PARAMS, req_id=i)
            )
            timings.append((time.perf_counter() - start) * 1000)

        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        print(f"\nHTTP initialize latency: avg={avg:.1f}ms  p95={p95:.1f}ms")

    @pytest.mark.asyncio
    async def test_tool_call_latency(self, http_client):
        """Measure tool call round-trip over HTTP."""
        await http_client.post("/mcp", json=_jsonrpc("initialize", _INIT_PARAMS))

        timings = []
        for i in range(50):
            start = time.perf_counter()
            await http_client.post(
                "/mcp",
                json=_jsonrpc(
                    "tools/call",
                    {"name": "echo", "arguments": {"message": f"bench-{i}"}},
                    req_id=i + 2,
                ),
            )
            timings.append((time.perf_counter() - start) * 1000)

        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        print(f"\nHTTP tool call latency: avg={avg:.1f}ms  p95={p95:.1f}ms  n={len(timings)}")


# ---------------------------------------------------------------------------
# Test: Delay behavior over HTTP
# ---------------------------------------------------------------------------

class TestHttpDelayBehavior:
    """Verify delay injection works over HTTP transport."""

    @pytest.mark.asyncio
    async def test_fixed_delay(self, delayed_client):
        """100ms delay should be measurable in HTTP responses."""
        await delayed_client.post(
            "/mcp", json=_jsonrpc("initialize", _INIT_PARAMS)
        )
        start = time.perf_counter()
        resp = await delayed_client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "echo", "arguments": {"message": "slow"}},
                req_id=2,
            ),
        )
        elapsed = (time.perf_counter() - start) * 1000
        body = await resp.json()
        assert not body.get("error")
        assert elapsed >= 80, f"Expected ~100ms delay, got {elapsed:.1f}ms"


# ---------------------------------------------------------------------------
# Test: Error rate resilience over HTTP
# ---------------------------------------------------------------------------

class TestHttpErrorResilience:
    """Verify error injection works over HTTP transport."""

    @pytest.mark.asyncio
    async def test_partial_errors(self, error_client):
        """50% error rate should produce a mix of successes and errors."""
        await error_client.post(
            "/mcp", json=_jsonrpc("initialize", _INIT_PARAMS)
        )
        successes = 0
        errors = 0
        for i in range(40):
            resp = await error_client.post(
                "/mcp",
                json=_jsonrpc(
                    "tools/call",
                    {"name": "echo", "arguments": {"message": f"test-{i}"}},
                    req_id=i + 2,
                ),
            )
            body = await resp.json()
            if body.get("error"):
                errors += 1
            else:
                successes += 1

        total = successes + errors
        error_rate = errors / total
        print(f"\nHTTP error rate: {error_rate:.1%} ({errors}/{total})")
        assert successes > 0, "Expected at least some successes"
        assert errors > 0, "Expected at least some errors at 50% rate"


# ---------------------------------------------------------------------------
# Test: SSE endpoint
# ---------------------------------------------------------------------------

class TestSseEndpoint:
    """Verify the /sse server-sent events endpoint."""

    @pytest.mark.asyncio
    async def test_sse_connection_event(self, http_client):
        """SSE endpoint should send a 'connected' event on open."""
        resp = await http_client.get("/sse")
        assert resp.status == 200
        assert "text/event-stream" in resp.headers["Content-Type"]

        # Read the initial connected event
        data = b""
        async for chunk in resp.content.iter_any():
            data += chunk
            if b"\n\n" in data:
                break

        text = data.decode()
        assert "event: connected" in text
        assert '"status": "connected"' in text
