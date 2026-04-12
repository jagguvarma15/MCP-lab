#!/usr/bin/env python3
"""
Profile a real MCP server -- run conformance, latency, and security suites
against any stdio-based MCP server and produce a JSON report.

Usage:
    python scripts/profile_server.py "npx -y @modelcontextprotocol/server-filesystem /tmp"
    python scripts/profile_server.py "python my_server.py" --output report.json
    python scripts/profile_server.py "python my_server.py" --tests conformance latency
"""

import argparse
import json
import sys
import os
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness import MockMCPClient, TestReporter, Finding, Severity


def run_conformance(client: MockMCPClient, reporter: TestReporter):
    """Run conformance checks against the server."""
    # JSON-RPC version check
    resp = client.initialize()
    if resp is None:
        reporter.add_finding(Finding(
            title="Initialize failed",
            description="Server did not respond to initialize request",
            severity=Severity.CRITICAL,
            category="conformance",
        ))
        return

    if resp.jsonrpc_version != "2.0":
        reporter.add_finding(Finding(
            title="Wrong JSON-RPC version",
            description=f"Server returned jsonrpc '{resp.jsonrpc_version}' instead of '2.0'",
            severity=Severity.CRITICAL,
            category="conformance",
            recommendation="Server must use JSON-RPC 2.0",
        ))

    if not resp.has_id:
        reporter.add_finding(Finding(
            title="Missing response ID",
            description="Server response does not include the request ID",
            severity=Severity.CRITICAL,
            category="conformance",
        ))

    extras = resp.extra_fields
    if extras:
        reporter.add_finding(Finding(
            title="Extra fields in response",
            description=f"Unexpected fields: {extras}",
            severity=Severity.WARNING,
            category="conformance",
        ))

    result = resp.result
    if "serverInfo" not in result:
        reporter.add_finding(Finding(
            title="Missing serverInfo",
            description="Initialize response does not include serverInfo",
            severity=Severity.WARNING,
            category="conformance",
        ))
    if "capabilities" not in result:
        reporter.add_finding(Finding(
            title="Missing capabilities",
            description="Initialize response does not include capabilities",
            severity=Severity.WARNING,
            category="conformance",
        ))

    # tools/list check
    tools_resp = client.list_tools()
    if tools_resp is None or tools_resp.is_error:
        reporter.add_finding(Finding(
            title="tools/list failed",
            description="Server did not respond to tools/list",
            severity=Severity.WARNING,
            category="conformance",
        ))
    else:
        tools = tools_resp.result.get("tools", [])
        reporter.add_finding(Finding(
            title="Tools listed",
            description=f"Server exposes {len(tools)} tools",
            severity=Severity.INFO,
            category="conformance",
            evidence={"tool_names": [t.get("name") for t in tools]},
        ))

    # ping check
    ping_resp = client.ping()
    if ping_resp is None or ping_resp.is_error:
        reporter.add_finding(Finding(
            title="Ping failed",
            description="Server did not respond to ping",
            severity=Severity.WARNING,
            category="conformance",
        ))

    # Unknown method check
    err_resp = client.send("nonexistent/method")
    if err_resp and not err_resp.is_error:
        reporter.add_finding(Finding(
            title="Unknown method accepted",
            description="Server did not return error for unknown method",
            severity=Severity.WARNING,
            category="conformance",
            recommendation="Unknown methods should return -32601 Method Not Found",
        ))


def run_latency(client: MockMCPClient, reporter: TestReporter, iterations: int = 20):
    """Run latency profiling against the server."""
    # tools/list latency
    for _ in range(iterations):
        resp = client.list_tools()
        if resp:
            reporter.add_latency("tools/list", resp.elapsed_ms)

    # tool call latency (if tools are available)
    tools_resp = client.list_tools()
    if tools_resp and tools_resp.result:
        tools = tools_resp.result.get("tools", [])
        if tools:
            tool_name = tools[0].get("name")
            for i in range(iterations):
                resp = client.call_tool(tool_name, {})
                if resp:
                    reporter.add_latency(f"tools/call ({tool_name})", resp.elapsed_ms)

    # ping latency
    for _ in range(iterations):
        resp = client.ping()
        if resp:
            reporter.add_latency("ping", resp.elapsed_ms)


def run_security(client: MockMCPClient, reporter: TestReporter):
    """Run basic security checks against the server."""
    tools_resp = client.list_tools()
    if not tools_resp or not tools_resp.result:
        return

    tools = tools_resp.result.get("tools", [])

    for tool in tools:
        desc = tool.get("description", "")

        # Check description length
        if len(desc) > 10_000:
            reporter.add_finding(Finding(
                title=f"Very long tool description: {tool['name']}",
                description=f"Description is {len(desc)} characters",
                severity=Severity.WARNING,
                category="security",
                recommendation="Consider enforcing description length limits",
            ))

        # Check for suspicious parameter names
        props = tool.get("inputSchema", {}).get("properties", {})
        suspicious = {"password", "secret", "token", "api_key", "credential", "auth_token"}
        found = set(props.keys()) & suspicious
        if found:
            reporter.add_finding(Finding(
                title=f"Suspicious parameters in {tool['name']}",
                description=f"Parameters that may collect credentials: {found}",
                severity=Severity.WARNING,
                category="security",
                recommendation="Auth should use transport-level headers, not tool params",
            ))


def main():
    parser = argparse.ArgumentParser(
        description="Profile a real MCP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python scripts/profile_server.py "python my_server.py"\n'
            '  python scripts/profile_server.py "npx server" --output report.json\n'
            '  python scripts/profile_server.py "python server.py" --tests conformance latency'
        ),
    )
    parser.add_argument("server_command", help="Command to start the stdio MCP server")
    parser.add_argument("--output", default=None, help="Output path for JSON report")
    parser.add_argument(
        "--tests",
        nargs="*",
        default=["conformance", "latency", "security"],
        choices=["conformance", "latency", "security"],
        help="Which test suites to run",
    )

    args = parser.parse_args()
    reporter = TestReporter(suite_name=f"profile: {args.server_command}")

    print(f"Profiling server: {args.server_command}")
    print(f"Running: {', '.join(args.tests)}")
    print()

    with MockMCPClient.over_stdio(args.server_command) as client:
        if "conformance" in args.tests:
            print("Running conformance checks...")
            run_conformance(client, reporter)

        if "latency" in args.tests:
            print("Running latency profiling...")
            # Re-initialize if conformance already initialized
            if "conformance" not in args.tests:
                client.initialize()
            run_latency(client, reporter)

        if "security" in args.tests:
            print("Running security checks...")
            if "conformance" not in args.tests and "latency" not in args.tests:
                client.initialize()
            run_security(client, reporter)

    print()
    print(reporter.summary())

    if args.output:
        reporter.save(args.output)
        print(f"\nJSON report saved to: {args.output}")
    else:
        output_path = "profile_results.json"
        reporter.save(output_path)
        print(f"\nJSON report saved to: {output_path}")


if __name__ == "__main__":
    main()
