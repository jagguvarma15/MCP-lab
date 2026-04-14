"""
Async MCP Client -- asyncio-based client for concurrent MCP server testing.

Supports concurrent tool calls, timeouts, and parallel multi-server
operations. Complements MockMCPClient for scenarios requiring concurrency.

Usage:
    async with AsyncMockMCPClient.over_stdio("python mock_server.py") as client:
        await client.initialize()
        tools = await client.list_tools()

        # Concurrent tool calls
        results = await client.call_tools_concurrent([
            ("echo", {"message": "hello"}),
            ("calculator", {"expression": "2+2"}),
        ])

    # Multi-server concurrency
    async with AsyncMultiServerClient(["cmd1", "cmd2"]) as multi:
        await multi.initialize_all()
        results = await multi.call_tool_on_all("echo", {"message": "hi"})
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from harness.mock_client import MCPResponse, RequestLog, ReadTimeoutError


class AsyncMockMCPClient:
    """
    Async MCP test client using asyncio subprocess streams.

    Provides the same interface as MockMCPClient but with async methods
    and built-in timeout support via asyncio.wait_for.

    Args:
        timeout: Read timeout in seconds. None means no timeout. Default is 5.
    """

    DEFAULT_TIMEOUT: float = 5.0

    def __init__(self, timeout: float | None = DEFAULT_TIMEOUT):
        self._next_id = 1
        self._log: list[RequestLog] = []
        self._process: asyncio.subprocess.Process | None = None
        self.timeout = timeout
        self._cmd: str | list[str] = ""

    # -- Factory methods ----------------------------------------------------

    @classmethod
    async def over_stdio(
        cls,
        command: str | list[str],
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> "AsyncMockMCPClient":
        """Connect to an MCP server via stdio.

        Args:
            command: Server command to spawn.
            timeout: Read timeout in seconds. None disables the timeout.
        """
        client = cls(timeout=timeout)
        client._cmd = command

        if isinstance(command, str):
            client._process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            client._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        return client

    # -- Core protocol methods ----------------------------------------------

    async def send_raw(self, message: dict) -> MCPResponse | None:
        """Send a raw JSON-RPC message and capture the response.

        Raises:
            ReadTimeoutError: If the server does not respond within the
                configured timeout.
            RuntimeError: If not connected to a server.
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Not connected to a server")

        sent_at = time.time()
        raw_msg = (json.dumps(message) + "\n").encode()

        self._process.stdin.write(raw_msg)
        await self._process.stdin.drain()

        start = time.monotonic()
        try:
            if self.timeout is not None:
                raw_line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=self.timeout,
                )
            else:
                raw_line = await self._process.stdout.readline()
        except asyncio.TimeoutError:
            raise ReadTimeoutError(
                f"Server did not respond within {self.timeout}s"
            )

        elapsed = (time.monotonic() - start) * 1000
        line = raw_line.decode()

        if not line.strip():
            log = RequestLog(request=message, response=None, sent_at=sent_at, error="Empty response")
            self._log.append(log)
            return None

        try:
            raw_response = json.loads(line)
        except json.JSONDecodeError as e:
            log = RequestLog(
                request=message, response=None, sent_at=sent_at,
                error=f"Invalid JSON: {e} -- raw: {line[:200]}",
            )
            self._log.append(log)
            return None

        response = MCPResponse(raw=raw_response, elapsed_ms=elapsed, request=message)
        log = RequestLog(request=message, response=response, sent_at=sent_at)
        self._log.append(log)
        return response

    async def send(self, method: str, params: dict | None = None) -> MCPResponse | None:
        """Send a JSON-RPC request with auto-incrementing ID."""
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        self._next_id += 1
        return await self.send_raw(msg)

    async def notify(self, method: str, params: dict | None = None):
        """Send a JSON-RPC notification (no ID, no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if self._process and self._process.stdin:
            self._process.stdin.write((json.dumps(msg) + "\n").encode())
            await self._process.stdin.drain()

    # -- MCP protocol methods -----------------------------------------------

    async def initialize(
        self,
        client_name: str = "mcp-lab-async-client",
        client_version: str = "0.1.0",
        protocol_version: str = "2025-03-26",
    ) -> MCPResponse | None:
        """Perform the MCP initialize handshake."""
        resp = await self.send("initialize", {
            "protocolVersion": protocol_version,
            "clientInfo": {"name": client_name, "version": client_version},
            "capabilities": {},
        })
        await self.notify("notifications/initialized")
        return resp

    async def list_tools(self) -> MCPResponse | None:
        return await self.send("tools/list")

    async def call_tool(self, name: str, arguments: dict | None = None) -> MCPResponse | None:
        return await self.send("tools/call", {"name": name, "arguments": arguments or {}})

    async def ping(self) -> MCPResponse | None:
        return await self.send("ping")

    # -- Concurrency helpers ------------------------------------------------

    async def call_tools_concurrent(
        self,
        calls: list[tuple[str, dict | None]],
    ) -> list[MCPResponse | None]:
        """Call multiple tools concurrently on this server.

        Note: The underlying stdio transport is sequential, so calls are
        dispatched as fast as possible but responses are read in order.
        For true parallelism across servers, use AsyncMultiServerClient.

        Args:
            calls: List of (tool_name, arguments) tuples.

        Returns:
            List of responses in the same order as calls.
        """
        # stdio is inherently sequential per-process, so we send all
        # requests first, then read all responses in order.
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Not connected to a server")

        messages = []
        for name, arguments in calls:
            msg = {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
            self._next_id += 1
            messages.append(msg)

        # Send all requests
        for msg in messages:
            raw_msg = (json.dumps(msg) + "\n").encode()
            self._process.stdin.write(raw_msg)
        await self._process.stdin.drain()

        # Read all responses
        responses: list[MCPResponse | None] = []
        for msg in messages:
            sent_at = time.time()
            start = time.monotonic()
            try:
                if self.timeout is not None:
                    raw_line = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self.timeout,
                    )
                else:
                    raw_line = await self._process.stdout.readline()
            except asyncio.TimeoutError:
                raise ReadTimeoutError(
                    f"Server did not respond within {self.timeout}s "
                    f"(during concurrent call batch)"
                )

            elapsed = (time.monotonic() - start) * 1000
            line = raw_line.decode()

            if not line.strip():
                log = RequestLog(request=msg, response=None, sent_at=sent_at, error="Empty response")
                self._log.append(log)
                responses.append(None)
                continue

            try:
                raw_response = json.loads(line)
            except json.JSONDecodeError as e:
                log = RequestLog(
                    request=msg, response=None, sent_at=sent_at,
                    error=f"Invalid JSON: {e} -- raw: {line[:200]}",
                )
                self._log.append(log)
                responses.append(None)
                continue

            response = MCPResponse(raw=raw_response, elapsed_ms=elapsed, request=msg)
            log = RequestLog(request=msg, response=response, sent_at=sent_at)
            self._log.append(log)
            responses.append(response)

        return responses

    # -- Lifecycle ----------------------------------------------------------

    async def shutdown(self):
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.shutdown()

    # -- Introspection for tests --------------------------------------------

    @property
    def log(self) -> list[RequestLog]:
        return self._log

    @property
    def response_times_ms(self) -> list[float]:
        return [
            entry.response.elapsed_ms
            for entry in self._log
            if entry.response is not None
        ]

    @property
    def errors(self) -> list[RequestLog]:
        return [entry for entry in self._log if entry.error or (entry.response and entry.response.is_error)]

    def assert_no_errors(self):
        errs = self.errors
        if errs:
            details = "\n".join(
                f"  - {e.error or e.response.error}" for e in errs
            )
            raise AssertionError(f"Client encountered {len(errs)} error(s):\n{details}")


class AsyncMultiServerClient:
    """
    Async client managing concurrent connections to multiple MCP servers.

    Unlike the synchronous MultiServerClient, this initializes and queries
    servers in parallel using asyncio.gather.
    """

    def __init__(self, server_commands: list[str | list[str]], timeout: float | None = 5.0):
        self._commands = server_commands
        self._timeout = timeout
        self.clients: list[AsyncMockMCPClient] = []
        self.server_names: list[str] = []
        self.server_tools: list[list[dict]] = []

    async def connect_all(self):
        """Spawn and connect to all servers concurrently."""
        self.clients = await asyncio.gather(*[
            AsyncMockMCPClient.over_stdio(cmd, timeout=self._timeout)
            for cmd in self._commands
        ])
        self.server_names = ["" for _ in self.clients]
        self.server_tools = [[] for _ in self.clients]

    async def initialize_all(self) -> list[MCPResponse | None]:
        """Initialize all servers concurrently."""
        responses = await asyncio.gather(*[
            client.initialize() for client in self.clients
        ])
        for i, resp in enumerate(responses):
            if resp and resp.result:
                info = resp.result.get("serverInfo", {})
                self.server_names[i] = info.get("name", "unknown")
        return responses

    async def list_all_tools(self) -> dict[str, list[dict]]:
        """List tools from all servers concurrently, keyed by server name."""
        responses = await asyncio.gather(*[
            client.list_tools() for client in self.clients
        ])

        result: dict[str, list[dict]] = {}
        name_counts: dict[str, int] = {}

        for i, resp in enumerate(responses):
            tools = []
            if resp and resp.result:
                tools = resp.result.get("tools", [])
            self.server_tools[i] = tools

            name = self.server_names[i] or "unknown"
            if name in name_counts:
                name_counts[name] += 1
                key = f"{name}_{name_counts[name]}"
            else:
                name_counts[name] = 0
                key = name
            result[key] = tools

        return result

    async def call_tool_on_all(
        self,
        name: str,
        arguments: dict | None = None,
    ) -> list[MCPResponse | None]:
        """Call the same tool on all servers concurrently.

        Useful for comparing behavior across servers or testing
        namespace collisions.
        """
        return await asyncio.gather(*[
            client.call_tool(name, arguments) for client in self.clients
        ])

    async def call_tools_on_servers(
        self,
        calls: list[tuple[int, str, dict | None]],
    ) -> list[MCPResponse | None]:
        """Call different tools on different servers concurrently.

        Args:
            calls: List of (server_index, tool_name, arguments) tuples.

        Returns:
            List of responses in the same order as calls.
        """
        return await asyncio.gather(*[
            self.clients[idx].call_tool(name, args)
            for idx, name, args in calls
        ])

    async def shutdown_all(self):
        """Shut down all connected servers concurrently."""
        await asyncio.gather(*[
            client.shutdown() for client in self.clients
        ], return_exceptions=True)

    async def __aenter__(self):
        await self.connect_all()
        return self

    async def __aexit__(self, *args):
        await self.shutdown_all()
