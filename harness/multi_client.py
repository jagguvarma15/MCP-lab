"""
Multi-Server Client -- manages connections to multiple MCP servers simultaneously.

Wraps N MockMCPClient instances for integration testing scenarios where
a single client interacts with multiple servers at once.

Usage:
    multi = MultiServerClient([
        "python -m harness.mock_server",
        "python -m harness.mock_server --shadow-tool read_file",
    ])
    multi.initialize_all()
    all_tools = multi.list_all_tools()
    result = multi.call_tool("echo", {"message": "hello"})
    multi.shutdown_all()
"""

import json
import sys
from dataclasses import dataclass, field
from typing import Any

from harness.mock_client import MockMCPClient, MCPResponse


@dataclass
class ServerInfo:
    """Metadata about a connected server."""
    command: str | list[str]
    client: MockMCPClient
    server_name: str = ""
    server_version: str = ""
    tools: list[dict] = field(default_factory=list)
    initialized: bool = False


class MultiServerClient:
    """
    Client that manages connections to multiple MCP servers.

    Provides methods to initialize all servers, aggregate tool lists,
    and route tool calls to the correct server.
    """

    def __init__(self, server_commands: list[str | list[str]]):
        self.servers: list[ServerInfo] = []
        for cmd in server_commands:
            client = MockMCPClient.over_stdio(cmd)
            self.servers.append(ServerInfo(command=cmd, client=client))

    def initialize_all(self) -> list[MCPResponse | None]:
        """Initialize all connected servers and cache their info."""
        responses = []
        for server in self.servers:
            resp = server.client.initialize()
            if resp and resp.result:
                info = resp.result.get("serverInfo", {})
                server.server_name = info.get("name", "unknown")
                server.server_version = info.get("version", "unknown")
                server.initialized = True
            responses.append(resp)
        return responses

    def list_all_tools(self) -> dict[str, list[dict]]:
        """List tools from all servers, keyed by server name.

        Returns:
            Dict mapping server name to list of tool schemas.
            If multiple servers share a name, an index suffix is added.
        """
        result: dict[str, list[dict]] = {}
        name_counts: dict[str, int] = {}

        for server in self.servers:
            resp = server.client.list_tools()
            tools = []
            if resp and resp.result:
                tools = resp.result.get("tools", [])
            server.tools = tools

            name = server.server_name or "unknown"
            if name in name_counts:
                name_counts[name] += 1
                key = f"{name}_{name_counts[name]}"
            else:
                name_counts[name] = 0
                key = name
            result[key] = tools

        return result

    def get_all_tools_flat(self) -> list[dict]:
        """Get a flat list of all tools from all servers.

        Returns tools in server order. Does not deduplicate.
        """
        all_tools = []
        for server in self.servers:
            if not server.tools:
                resp = server.client.list_tools()
                if resp and resp.result:
                    server.tools = resp.result.get("tools", [])
            all_tools.extend(server.tools)
        return all_tools

    def find_tool_server(self, tool_name: str) -> ServerInfo | None:
        """Find which server owns a tool by name.

        Returns the first server that has a tool with the given name.
        """
        for server in self.servers:
            for tool in server.tools:
                if tool.get("name") == tool_name:
                    return server
        return None

    def call_tool(self, name: str, arguments: dict | None = None) -> MCPResponse | None:
        """Call a tool, routing to the correct server.

        Looks up which server provides the named tool and dispatches
        the call there. Returns None if no server has the tool.
        """
        server = self.find_tool_server(name)
        if server is None:
            return None
        return server.client.call_tool(name, arguments)

    def call_tool_on_server(
        self, server_index: int, name: str, arguments: dict | None = None
    ) -> MCPResponse | None:
        """Call a tool on a specific server by index."""
        if server_index < 0 or server_index >= len(self.servers):
            raise IndexError(f"Server index {server_index} out of range (0-{len(self.servers)-1})")
        return self.servers[server_index].client.call_tool(name, arguments)

    def get_tool_collisions(self) -> dict[str, list[str]]:
        """Find tool names that appear on multiple servers.

        Returns:
            Dict mapping tool name to list of server names that provide it.
        """
        tool_providers: dict[str, list[str]] = {}
        for server in self.servers:
            for tool in server.tools:
                tool_name = tool.get("name", "")
                if tool_name not in tool_providers:
                    tool_providers[tool_name] = []
                tool_providers[tool_name].append(server.server_name)

        return {name: providers for name, providers in tool_providers.items()
                if len(providers) > 1}

    def shutdown_server(self, index: int):
        """Shut down a specific server by index."""
        if 0 <= index < len(self.servers):
            self.servers[index].client.shutdown()
            self.servers[index].initialized = False

    def shutdown_all(self):
        """Shut down all connected servers."""
        for server in self.servers:
            try:
                server.client.shutdown()
            except Exception:
                pass
            server.initialized = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown_all()

    @property
    def server_count(self) -> int:
        return len(self.servers)

    @property
    def active_servers(self) -> list[ServerInfo]:
        return [s for s in self.servers if s.initialized]
