"""
HTTP/SSE Transport Mock Server -- serves MCP over HTTP instead of stdio.

Wraps the same MockMCPServer logic but exposes it via HTTP endpoints.
Used for transport comparison tests between stdio and HTTP/SSE.

Requires: aiohttp (pip install aiohttp)

Usage:
    python -m harness.http_server --port 8080
    python -m harness.http_server --port 8080 --delay 100 --error-rate 0.1

Endpoints:
    POST /mcp    -- JSON-RPC endpoint (request/response)
    GET  /sse    -- SSE endpoint (server-sent events for notifications)
    GET  /health -- Health check
"""

import json
import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from harness.mock_server import (
    MockMCPServer, Tool, ServerBehaviors,
    echo_tool, calculator_tool, slow_tool,
    jsonrpc_response, jsonrpc_error,
)


class HTTPMCPServer:
    """
    MCP server that communicates over HTTP instead of stdio.

    Wraps MockMCPServer and exposes its handlers via HTTP endpoints.
    """

    def __init__(
        self,
        server: MockMCPServer | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
    ):
        self.server = server or MockMCPServer(
            tools=[echo_tool, calculator_tool, slow_tool]
        )
        self.host = host
        self.port = port
        self._sse_clients: list[web.StreamResponse] = []
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    def _create_app(self) -> "web.Application":
        app = web.Application()
        app.router.add_post("/mcp", self._handle_mcp)
        app.router.add_get("/sse", self._handle_sse)
        app.router.add_get("/health", self._handle_health)
        return app

    async def _handle_mcp(self, request: "web.Request") -> "web.Response":
        """Handle JSON-RPC requests over HTTP POST."""
        try:
            body = await request.text()
        except Exception:
            return web.json_response(
                jsonrpc_error(None, -32700, "Could not read request body"),
                status=200,
            )

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return web.json_response(
                jsonrpc_error(None, -32700, "Parse error"),
                status=200,
            )

        # Log the request
        self.server._request_log.append({"raw": body, "time": time.time()})

        # Process through the server's behavior pipeline
        response = self.server._process_with_behaviors(parsed)

        if response is None:
            # Notification or dropped request
            return web.Response(status=204)

        # Check auth header if present
        auth = request.headers.get("Authorization", "")
        if auth:
            response["_auth_received"] = True

        return web.json_response(response)

    async def _handle_sse(self, request: "web.Request") -> "web.StreamResponse":
        """Handle SSE connections for server-sent events."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        self._sse_clients.append(response)

        # Send initial connection event
        await response.write(
            f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n".encode()
        )

        # Keep connection alive until client disconnects
        try:
            while True:
                await asyncio.sleep(30)
                # Send keepalive
                await response.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if response in self._sse_clients:
                self._sse_clients.remove(response)

        return response

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "server_name": self.server.server_name,
            "server_version": self.server.server_version,
            "tool_count": len(self.server.tools),
        })

    async def send_sse_event(self, event_type: str, data: dict):
        """Broadcast an SSE event to all connected clients."""
        message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        disconnected = []
        for client in self._sse_clients:
            try:
                await client.write(message.encode())
            except (ConnectionResetError, Exception):
                disconnected.append(client)
        for client in disconnected:
            self._sse_clients.remove(client)

    async def start(self):
        """Start the HTTP server."""
        if not HAS_AIOHTTP:
            raise ImportError(
                "aiohttp is required for HTTP transport. "
                "Install it with: pip install aiohttp"
            )
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("HTTP MCP Server running at http://%s:%s", self.host, self.port)

    async def stop(self):
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()

    def run(self):
        """Run the HTTP server (blocking)."""
        if not HAS_AIOHTTP:
            raise ImportError(
                "aiohttp is required for HTTP transport. "
                "Install it with: pip install aiohttp"
            )
        self._app = self._create_app()
        web.run_app(self._app, host=self.host, port=self.port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HTTP/SSE MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--delay", type=int, default=0, help="Response delay in ms")
    parser.add_argument("--error-rate", type=float, default=0.0, help="Error probability")
    parser.add_argument("--drop-rate", type=float, default=0.0, help="Drop probability")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional log file path (in addition to stderr)",
    )

    args = parser.parse_args()

    from harness.logging_config import configure_logging
    configure_logging(args.log_level, args.log_file)

    behaviors = ServerBehaviors(
        delay_ms=args.delay,
        error_rate=args.error_rate,
        drop_rate=args.drop_rate,
    )

    server = MockMCPServer(
        tools=[echo_tool, calculator_tool, slow_tool],
        behaviors=behaviors,
    )

    http_server = HTTPMCPServer(server=server, host=args.host, port=args.port)
    http_server.run()
