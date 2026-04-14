"""
MCP Lab Harness -- core test infrastructure for MCP protocol testing.

Modules:
    mock_server   -- Configurable MCP server with fault injection
    mock_client   -- Minimal MCP client for probing servers
    multi_client  -- Multi-server client for integration testing
    interceptor   -- MITM proxy for inspecting/modifying MCP traffic
    reporter      -- Test result collection and formatting
    config        -- Fixture loader for server presets, schemas, payloads
    http_server   -- HTTP/SSE transport mock server
"""

from harness.mock_server import MockMCPServer, Tool, ToolParam, ServerBehaviors
from harness.mock_server import echo_tool, calculator_tool, slow_tool
from harness.mock_client import MockMCPClient, MCPResponse, RequestLog, ReadTimeoutError
from harness.async_client import AsyncMockMCPClient, AsyncMultiServerClient
from harness.interceptor import MCPInterceptor, InterceptedMessage
from harness.reporter import TestReporter, Finding, Severity, LatencyProfile
from harness.config import (
    load_server_preset,
    load_server_config,
    load_schema,
    load_payload_sequence,
    list_server_presets,
    list_schemas,
)
from harness.multi_client import MultiServerClient, ServerInfo

__all__ = [
    # mock_server
    "MockMCPServer",
    "Tool",
    "ToolParam",
    "ServerBehaviors",
    "echo_tool",
    "calculator_tool",
    "slow_tool",
    # mock_client
    "MockMCPClient",
    "MCPResponse",
    "RequestLog",
    "ReadTimeoutError",
    # async_client
    "AsyncMockMCPClient",
    "AsyncMultiServerClient",
    # multi_client
    "MultiServerClient",
    "ServerInfo",
    # interceptor
    "MCPInterceptor",
    "InterceptedMessage",
    # reporter
    "TestReporter",
    "Finding",
    "Severity",
    "LatencyProfile",
    # config
    "load_server_preset",
    "load_server_config",
    "load_schema",
    "load_payload_sequence",
    "list_server_presets",
    "list_schemas",
]
