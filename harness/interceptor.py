"""
MCP Interceptor -- sits between client and server to log and modify traffic.

This is a transparent proxy for MCP stdio communication.
It forwards messages in both directions while logging everything,
and optionally modifying messages in transit.

Usage:
    # Passive logging
    python interceptor.py --cmd "python my_server.py" --log traffic.jsonl

    # Active modification (e.g., strip tool descriptions for context cost testing)
    python interceptor.py --cmd "python my_server.py" --strip-descriptions
"""

import json
import logging
import sys
import subprocess
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, TextIO

logger = logging.getLogger(__name__)


@dataclass
class InterceptedMessage:
    direction: str  # "client->server" or "server->client"
    timestamp: float
    raw: str
    parsed: dict | None
    modified: bool = False
    original: str | None = None  # if modified, keeps the original


class MCPInterceptor:
    """
    Transparent proxy for MCP stdio transport.

    Sits between client (stdin/stdout) and a subprocess server,
    forwarding and logging all messages.
    """

    def __init__(
        self,
        server_command: str,
        log_file: str | None = None,
        request_hooks: list[Callable[[dict], dict | None]] | None = None,
        response_hooks: list[Callable[[dict], dict | None]] | None = None,
    ):
        self.server_command = server_command
        self.log_file = log_file
        self.request_hooks = request_hooks or []
        self.response_hooks = response_hooks or []
        self.messages: list[InterceptedMessage] = []
        self._log_handle: TextIO | None = None

    def start(self):
        """Start the interceptor. Blocks until stdin closes."""
        if self.log_file:
            self._log_handle = open(self.log_file, "a")

        # Start the real server as a subprocess
        proc = subprocess.Popen(
            self.server_command.split(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
        )

        # Thread to read server responses and forward to client
        def forward_responses():
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                msg = self._intercept(line, "server->client", self.response_hooks)
                if msg is not None:
                    sys.stdout.write(msg + "\n")
                    sys.stdout.flush()

        reader_thread = threading.Thread(target=forward_responses, daemon=True)
        reader_thread.start()

        # Main thread: read client requests and forward to server
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                msg = self._intercept(line, "client->server", self.request_hooks)
                if msg is not None and proc.stdin:
                    proc.stdin.write(msg + "\n")
                    proc.stdin.flush()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            if self._log_handle:
                self._log_handle.close()

    def _intercept(
        self,
        raw: str,
        direction: str,
        hooks: list[Callable[[dict], dict | None]],
    ) -> str | None:
        """Process a message through hooks, log it, return the (possibly modified) message."""
        timestamp = time.time()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Can't parse -- log and forward as-is
            entry = InterceptedMessage(direction=direction, timestamp=timestamp, raw=raw, parsed=None)
            self.messages.append(entry)
            self._log_entry(entry)
            return raw

        original = raw
        modified = False

        for hook in hooks:
            result = hook(parsed)
            if result is not None:
                parsed = result
                modified = True

        output = json.dumps(parsed) if modified else raw

        entry = InterceptedMessage(
            direction=direction,
            timestamp=timestamp,
            raw=output,
            parsed=parsed,
            modified=modified,
            original=original if modified else None,
        )
        self.messages.append(entry)
        self._log_entry(entry)
        return output

    def _log_entry(self, entry: InterceptedMessage):
        logger.debug(
            "%s%s %s",
            entry.direction,
            " [modified]" if entry.modified else "",
            entry.raw,
        )
        if self._log_handle:
            log = {
                "direction": entry.direction,
                "timestamp": entry.timestamp,
                "modified": entry.modified,
                "message": entry.parsed or entry.raw,
            }
            if entry.original:
                log["original"] = entry.original
            self._log_handle.write(json.dumps(log) + "\n")
            self._log_handle.flush()


# ---------------------------------------------------------------------------
# Pre-built hooks
# ---------------------------------------------------------------------------

def strip_descriptions(message: dict) -> dict | None:
    """Remove tool descriptions from tools/list responses (for context cost testing)."""
    result = message.get("result", {})
    tools = result.get("tools", [])
    if tools:
        for tool in tools:
            tool["description"] = ""
        return message
    return None


def inject_description_suffix(suffix: str) -> Callable[[dict], dict | None]:
    """Append text to all tool descriptions."""
    def hook(message: dict) -> dict | None:
        result = message.get("result", {})
        tools = result.get("tools", [])
        if tools:
            for tool in tools:
                tool["description"] = tool.get("description", "") + suffix
            return message
        return None
    return hook


def add_latency_metadata(message: dict) -> dict | None:
    """Tag responses with timing metadata."""
    if "result" in message:
        message["_interceptor_timestamp"] = time.time()
        return message
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP Interceptor Proxy")
    parser.add_argument("--cmd", required=True, help="Server command to proxy")
    parser.add_argument("--log", default=None, help="Log file path (JSONL)")
    parser.add_argument("--strip-descriptions", action="store_true", help="Remove tool descriptions")
    parser.add_argument("--inject-suffix", type=str, default=None, help="Append to tool descriptions")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="Log level for proxy diagnostics (DEBUG shows every message)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional diagnostic log file (distinct from the JSONL --log capture)",
    )

    args = parser.parse_args()

    from harness.logging_config import configure_logging
    configure_logging(args.log_level, args.log_file)

    response_hooks = []
    if args.strip_descriptions:
        response_hooks.append(strip_descriptions)
    if args.inject_suffix:
        response_hooks.append(inject_description_suffix(args.inject_suffix))

    interceptor = MCPInterceptor(
        server_command=args.cmd,
        log_file=args.log,
        response_hooks=response_hooks,
    )
    interceptor.start()
