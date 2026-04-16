"""
Mock MCP Client -- minimal client for probing and testing MCP servers.

Sends JSON-RPC messages to an MCP server and captures responses.
Designed for testing, not production use.

Usage:
    client = MockMCPClient.over_stdio("python mock_server.py")
    client.initialize()
    tools = client.list_tools()
    result = client.call_tool("echo", {"message": "hello"})
    client.shutdown()
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class ReadTimeoutError(TimeoutError):
    """Raised when reading from the server exceeds the configured timeout."""
    pass


class IDMismatchError(Exception):
    """Raised when a response ID does not match the request ID."""
    def __init__(self, expected_id, actual_id):
        self.expected_id = expected_id
        self.actual_id = actual_id
        super().__init__(
            f"Response ID mismatch: expected {expected_id!r}, got {actual_id!r}"
        )


@dataclass
class MCPResponse:
    """Parsed MCP response with timing metadata."""
    raw: dict
    elapsed_ms: float
    request: dict

    @property
    def result(self) -> Any:
        return self.raw.get("result")

    @property
    def error(self) -> dict | None:
        return self.raw.get("error")

    @property
    def is_error(self) -> bool:
        return "error" in self.raw

    @property
    def jsonrpc_version(self) -> str:
        return self.raw.get("jsonrpc", "missing")

    @property
    def has_id(self) -> bool:
        return "id" in self.raw and self.raw["id"] is not None

    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def id_matches_request(self) -> bool:
        """True if the response ID matches the request ID."""
        if "id" not in self.request:
            # Notification -- no ID to match
            return True
        return self.raw.get("id") == self.request.get("id")

    @property
    def extra_fields(self) -> set[str]:
        """Fields beyond the standard jsonrpc, id, result/error."""
        known = {"jsonrpc", "id", "result", "error"}
        return set(self.raw.keys()) - known


@dataclass
class RequestLog:
    """Full log of a request-response cycle."""
    request: dict
    response: MCPResponse | None
    sent_at: float
    error: str | None = None


class MockMCPClient:
    """
    Test client that communicates with MCP servers.

    Tracks all request/response pairs for post-hoc analysis in tests.

    Args:
        timeout: Read timeout in seconds. None means block indefinitely
                 (legacy behavior). Default is 5 seconds.
    """

    DEFAULT_TIMEOUT: float = 5.0

    def __init__(self, timeout: float | None = DEFAULT_TIMEOUT, strict_id: bool = False):
        self._next_id = 1
        self._log: list[RequestLog] = []
        self._process: subprocess.Popen | None = None
        self.timeout = timeout
        self.strict_id = strict_id

    # -- Factory methods ----------------------------------------------------

    @classmethod
    def over_stdio(
        cls,
        command: str | list[str],
        timeout: float | None = DEFAULT_TIMEOUT,
        strict_id: bool = False,
        **kwargs,
    ) -> "MockMCPClient":
        """Connect to an MCP server via stdio.

        Args:
            command: Server command to spawn.
            timeout: Read timeout in seconds. None disables the timeout.
            strict_id: If True, raise IDMismatchError on ID mismatches.
        """
        client = cls(timeout=timeout, strict_id=strict_id)
        client._process = subprocess.Popen(
            command if isinstance(command, list) else command.split(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )
        return client

    # -- Core protocol methods ----------------------------------------------

    def _readline_with_timeout(self) -> str | None:
        """Read a line from the server's stdout with an optional timeout.

        Returns the line string, or None if the read timed out.
        Raises ReadTimeoutError if the timeout is exceeded.
        """
        if self.timeout is None:
            # No timeout -- block indefinitely (legacy behavior)
            return self._process.stdout.readline()

        result: list[str] = []
        exc: list[Exception] = []

        def _read():
            try:
                result.append(self._process.stdout.readline())
            except Exception as e:
                exc.append(e)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=self.timeout)

        if reader.is_alive():
            # Thread is still blocking on readline -- timeout exceeded
            raise ReadTimeoutError(
                f"Server did not respond within {self.timeout}s"
            )

        if exc:
            raise exc[0]

        return result[0] if result else ""

    def send_raw(self, message: dict) -> MCPResponse | None:
        """Send a raw JSON-RPC message and capture the response.

        Raises:
            ReadTimeoutError: If the server does not respond within the
                configured timeout.
            RuntimeError: If not connected to a server.
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Not connected to a server")

        sent_at = time.time()
        raw_msg = json.dumps(message) + "\n"

        self._process.stdin.write(raw_msg)
        self._process.stdin.flush()

        start = time.monotonic()
        line = self._readline_with_timeout()
        elapsed = (time.monotonic() - start) * 1000

        if not line or not line.strip():
            log = RequestLog(request=message, response=None, sent_at=sent_at, error="Empty response")
            self._log.append(log)
            return None

        try:
            raw_response = json.loads(line)
        except json.JSONDecodeError as e:
            log = RequestLog(
                request=message, response=None, sent_at=sent_at,
                error=f"Invalid JSON: {e} -- raw: {line[:200]}"
            )
            self._log.append(log)
            return None

        response = MCPResponse(raw=raw_response, elapsed_ms=elapsed, request=message)
        log = RequestLog(request=message, response=response, sent_at=sent_at)
        self._log.append(log)

        # Verify request/response ID correlation
        if "id" in message and not response.id_matches_request:
            if self.strict_id:
                raise IDMismatchError(message["id"], response.id)

        return response

    def send(self, method: str, params: dict | None = None) -> MCPResponse | None:
        """Send a JSON-RPC request with auto-incrementing ID."""
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        self._next_id += 1
        return self.send_raw(msg)

    def notify(self, method: str, params: dict | None = None):
        """Send a JSON-RPC notification (no ID, no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if self._process and self._process.stdin:
            self._process.stdin.write(json.dumps(msg) + "\n")
            self._process.stdin.flush()

    # -- MCP protocol methods -----------------------------------------------

    def initialize(
        self,
        client_name: str = "mcp-lab-client",
        client_version: str = "0.1.0",
        protocol_version: str = "2025-03-26",
    ) -> MCPResponse | None:
        """Perform the MCP initialize handshake."""
        resp = self.send("initialize", {
            "protocolVersion": protocol_version,
            "clientInfo": {"name": client_name, "version": client_version},
            "capabilities": {},
        })
        # Send initialized notification
        self.notify("notifications/initialized")
        return resp

    def list_tools(self) -> MCPResponse | None:
        return self.send("tools/list")

    def call_tool(self, name: str, arguments: dict | None = None) -> MCPResponse | None:
        return self.send("tools/call", {"name": name, "arguments": arguments or {}})

    def ping(self) -> MCPResponse | None:
        return self.send("ping")

    # -- Lifecycle ----------------------------------------------------------

    def shutdown(self):
        if self._process:
            self._process.terminate()
            self._process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()

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

    @property
    def mismatched_ids(self) -> list[RequestLog]:
        """Entries where the response ID did not match the request ID."""
        return [
            entry for entry in self._log
            if entry.response is not None
            and "id" in entry.request
            and not entry.response.id_matches_request
        ]

    def assert_no_errors(self):
        errs = self.errors
        if errs:
            details = "\n".join(
                f"  - {e.error or e.response.error}" for e in errs
            )
            raise AssertionError(f"Client encountered {len(errs)} error(s):\n{details}")
