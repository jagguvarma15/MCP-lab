"""
mcplab CLI — test, profile, and audit MCP servers from the terminal.

Entry point for `uv run mcplab` or `mcplab` when installed.
"""

import argparse
import json
import os
import statistics
import sys
import signal
import time
from pathlib import Path

from harness import (
    MockMCPClient,
    TestReporter,
    Finding,
    Severity,
    estimate_tokens,
    configure_logging,
)
from harness.mock_client import ReadTimeoutError

# ── Colors & Symbols ─────────────────────────────────────────────────

_IS_TTY = sys.stdout.isatty()


def _c(code: str) -> str:
    return f"\033[{code}m" if _IS_TTY else ""


BOLD = _c("1")
DIM = _c("2")
RESET = _c("0")
RED = _c("31")
GREEN = _c("32")
YELLOW = _c("33")
CYAN = _c("36")
WHITE = _c("37")

CHECK = f"{GREEN}✓{RESET}" if _IS_TTY else "+"
CROSS = f"{RED}✗{RESET}" if _IS_TTY else "x"
BULLET = f"{DIM}▸{RESET}" if _IS_TTY else ">"
DOT = f"{DIM}·{RESET}" if _IS_TTY else "."
ARROW = f"{DIM}→{RESET}" if _IS_TTY else "->"

PROJECT_DIR = Path(__file__).resolve().parent.parent


# ── Output helpers ────────────────────────────────────────────────────

def banner():
    print()
    print(f"  {BOLD}{CYAN}███╗   ███╗ ██████╗██████╗     ██╗      █████╗ ██████╗ {RESET}")
    print(f"  {BOLD}{CYAN}████╗ ████║██╔════╝██╔══██╗    ██║     ██╔══██╗██╔══██╗{RESET}")
    print(f"  {BOLD}{CYAN}██╔████╔██║██║     ██████╔╝    ██║     ███████║██████╔╝{RESET}")
    print(f"  {BOLD}{CYAN}██║╚██╔╝██║██║     ██╔═══╝     ██║     ██╔══██║██╔══██╗{RESET}")
    print(f"  {BOLD}{CYAN}██║ ╚═╝ ██║╚██████╗██║         ███████╗██║  ██║██████╔╝{RESET}")
    print(f"  {BOLD}{CYAN}╚═╝     ╚═╝ ╚═════╝╚═╝         ╚══════╝╚═╝  ╚═╝╚═════╝ {RESET}")
    print(f"  {DIM}MCP Server Test Harness{RESET}  {DIM}v0.1.0{RESET}")
    print()


def divider():
    line = "─" * 60
    print(f"  {DIM}{line}{RESET}")


def section(title: str):
    print()
    print(f"  {BOLD}{WHITE}{title}{RESET}")
    divider()


def sev_icon(severity: str) -> str:
    s = severity.upper()
    if s == "CRITICAL":
        return f"{CROSS} {RED}[CRITICAL]{RESET}"
    if s == "WARNING":
        return f"{CROSS} {YELLOW}[WARNING]{RESET} "
    return f"{CHECK} {CYAN}[INFO]{RESET}    "


# ── Profile subroutines (reused from scripts/profile_server.py) ──────

def _run_conformance(client: MockMCPClient, reporter: TestReporter):
    resp = client.initialize()
    if resp is None:
        reporter.add_finding(Finding(
            title="Initialize failed",
            description="Server did not respond to initialize request",
            severity=Severity.CRITICAL, category="conformance",
        ))
        return

    if resp.jsonrpc_version != "2.0":
        reporter.add_finding(Finding(
            title="Wrong JSON-RPC version",
            description=f"Server returned jsonrpc '{resp.jsonrpc_version}' instead of '2.0'",
            severity=Severity.CRITICAL, category="conformance",
            recommendation="Server must use JSON-RPC 2.0",
        ))

    if not resp.has_id:
        reporter.add_finding(Finding(
            title="Missing response ID",
            description="Server response does not include the request ID",
            severity=Severity.CRITICAL, category="conformance",
        ))

    extras = resp.extra_fields
    if extras:
        reporter.add_finding(Finding(
            title="Extra fields in response",
            description=f"Unexpected fields: {extras}",
            severity=Severity.WARNING, category="conformance",
        ))

    result = resp.result
    if result:
        info = result.get("serverInfo", {})
        if info:
            print(f"  {CHECK} Connected: {BOLD}{info.get('name', '?')}{RESET} {DIM}v{info.get('version', '?')}{RESET}")
            caps = list(result.get("capabilities", {}).keys())
            print(f"  {BULLET} Capabilities: {DIM}{', '.join(caps)}{RESET}")
        if "serverInfo" not in result:
            reporter.add_finding(Finding(
                title="Missing serverInfo",
                description="Initialize response does not include serverInfo",
                severity=Severity.WARNING, category="conformance",
            ))
        if "capabilities" not in result:
            reporter.add_finding(Finding(
                title="Missing capabilities",
                description="Initialize response does not include capabilities",
                severity=Severity.WARNING, category="conformance",
            ))

    tools_resp = client.list_tools()
    if tools_resp is None or tools_resp.is_error:
        reporter.add_finding(Finding(
            title="tools/list failed",
            description="Server did not respond to tools/list",
            severity=Severity.WARNING, category="conformance",
        ))
    else:
        tools = tools_resp.result.get("tools", [])
        reporter.add_finding(Finding(
            title="Tools listed",
            description=f"Server exposes {len(tools)} tools",
            severity=Severity.INFO, category="conformance",
            evidence={"tool_names": [t.get("name") for t in tools]},
        ))

    ping_resp = client.ping()
    if ping_resp is None or ping_resp.is_error:
        reporter.add_finding(Finding(
            title="Ping failed",
            description="Server did not respond to ping",
            severity=Severity.WARNING, category="conformance",
        ))

    err_resp = client.send("nonexistent/method")
    if err_resp and not err_resp.is_error:
        reporter.add_finding(Finding(
            title="Unknown method accepted",
            description="Server did not return error for unknown method",
            severity=Severity.WARNING, category="conformance",
            recommendation="Unknown methods should return -32601 Method Not Found",
        ))


def _run_latency(client: MockMCPClient, reporter: TestReporter, iterations: int = 20):
    for _ in range(iterations):
        resp = client.list_tools()
        if resp:
            reporter.add_latency("tools/list", resp.elapsed_ms)

    tools_resp = client.list_tools()
    if tools_resp and tools_resp.result:
        tools = tools_resp.result.get("tools", [])
        if tools:
            tool_name = tools[0].get("name")
            for _ in range(iterations):
                resp = client.call_tool(tool_name, {})
                if resp:
                    reporter.add_latency(f"tools/call ({tool_name})", resp.elapsed_ms)

    for _ in range(iterations):
        resp = client.ping()
        if resp:
            reporter.add_latency("ping", resp.elapsed_ms)


def _run_security(client: MockMCPClient, reporter: TestReporter):
    tools_resp = client.list_tools()
    if not tools_resp or not tools_resp.result:
        return

    tools = tools_resp.result.get("tools", [])
    suspicious_names = {"password", "secret", "token", "api_key", "credential", "auth_token"}

    for tool in tools:
        desc = tool.get("description", "")
        if len(desc) > 10_000:
            reporter.add_finding(Finding(
                title=f"Very long tool description: {tool['name']}",
                description=f"Description is {len(desc)} characters",
                severity=Severity.WARNING, category="security",
                recommendation="Consider enforcing description length limits",
            ))

        props = tool.get("inputSchema", {}).get("properties", {})
        found = set(props.keys()) & suspicious_names
        if found:
            reporter.add_finding(Finding(
                title=f"Suspicious parameters in {tool['name']}",
                description=f"Parameters that may collect credentials: {found}",
                severity=Severity.WARNING, category="security",
                recommendation="Auth should use transport-level headers, not tool params",
            ))


# ── Report renderer ──────────────────────────────────────────────────

def render_report(reporter: TestReporter):
    """Render a TestReporter to the terminal."""
    section("Findings")

    crit = len([f for f in reporter.findings if f.severity == Severity.CRITICAL])
    warn = len([f for f in reporter.findings if f.severity == Severity.WARNING])
    info = len([f for f in reporter.findings if f.severity == Severity.INFO])
    total = len(reporter.findings)

    parts = []
    if crit:
        parts.append(f"{RED}{crit} critical{RESET}")
    if warn:
        parts.append(f"{YELLOW}{warn} warnings{RESET}")
    if info:
        parts.append(f"{CYAN}{info} info{RESET}")
    detail = " ".join(parts)
    print(f"  {BULLET} Total: {BOLD}{total}{RESET}  {DIM}({RESET}{detail}{DIM}){RESET}")
    print()

    for f in reporter.findings:
        print(f"  {sev_icon(f.severity.value)} {BOLD}{f.title}{RESET}")
        print(f"    {DIM}{f.description}{RESET}")
        if f.recommendation:
            print(f"    {ARROW} {f.recommendation}")
        if "tool_names" in f.evidence:
            names = ", ".join(f.evidence["tool_names"])
            print(f"    {DIM}tools: [{names}]{RESET}")

    if reporter.latency_profiles:
        section("Latency")
        print(f"  {BOLD}{'OPERATION':<32s} {'MEAN':>8s} {'P50':>8s} {'P95':>8s} {'P99':>8s} {'n':>6s}{RESET}")
        divider()
        for label, profile in reporter.latency_profiles.items():
            print(
                f"  {CYAN}{label:<32s}{RESET}"
                f" {profile.mean:>7.1f}ms"
                f" {profile.median:>7.1f}ms"
                f" {profile.p95:>7.1f}ms"
                f" {profile.p99:>7.1f}ms"
                f" {profile.count:>5d}"
            )
    print()


def render_json_report(path: str):
    """Load and render a saved JSON report."""
    with open(path) as f:
        data = json.load(f)

    section(f"Report: {data.get('suite', 'unknown')}")

    findings = data.get("findings", [])
    s = data.get("summary", {})

    parts = []
    if s.get("critical"):
        parts.append(f"{RED}{s['critical']} critical{RESET}")
    if s.get("warnings"):
        parts.append(f"{YELLOW}{s['warnings']} warnings{RESET}")
    if s.get("info"):
        parts.append(f"{CYAN}{s['info']} info{RESET}")
    detail = " ".join(parts)
    print(f"  {BULLET} Findings: {BOLD}{s.get('total_findings', 0)}{RESET}  {DIM}({RESET}{detail}{DIM}){RESET}")
    print()

    for f in findings:
        print(f"  {sev_icon(f['severity'])} {BOLD}{f['title']}{RESET}")
        if f.get("description"):
            print(f"    {DIM}{f['description']}{RESET}")
        if f.get("recommendation"):
            print(f"    {ARROW} {f['recommendation']}")
        ev = f.get("evidence", {})
        if "tool_names" in ev:
            print(f"    {DIM}tools: [{', '.join(ev['tool_names'])}]{RESET}")

    latency = data.get("latency", {})
    if latency:
        section("Latency")
        print(f"  {BOLD}{'OPERATION':<32s} {'MEAN':>8s} {'P50':>8s} {'P95':>8s} {'P99':>8s} {'n':>6s}{RESET}")
        divider()
        for label, p in latency.items():
            print(
                f"  {CYAN}{label:<32s}{RESET}"
                f" {p['mean_ms']:>7.1f}ms"
                f" {p['median_ms']:>7.1f}ms"
                f" {p['p95_ms']:>7.1f}ms"
                f" {p['p99_ms']:>7.1f}ms"
                f" {p['count']:>5d}"
            )
    print()


# ── Commands ──────────────────────────────────────────────────────────

def cmd_profile(args):
    banner()
    section("Profiling Server")
    print(f"  {BULLET} Command:  {CYAN}{args.server_command}{RESET}")
    suites = args.suites or ["conformance", "latency", "security"]
    print(f"  {BULLET} Suites:   {', '.join(suites)}")
    print(f"  {BULLET} Timeout:  {args.timeout}s")
    print()
    print(f"  {DIM}Connecting and running checks...{RESET}")

    reporter = TestReporter(suite_name=args.server_command)

    if args.timeout > 0:
        def _on_timeout(signum, frame):
            raise TimeoutError(f"Profiling exceeded {args.timeout}s global timeout")
        signal.signal(signal.SIGALRM, _on_timeout)
        signal.alarm(args.timeout)

    try:
        with MockMCPClient.over_stdio(args.server_command, timeout=10) as client:
            if "conformance" in suites:
                _run_conformance(client, reporter)
            if "latency" in suites:
                if "conformance" not in suites:
                    client.initialize()
                _run_latency(client, reporter)
            if "security" in suites:
                if "conformance" not in suites and "latency" not in suites:
                    client.initialize()
                _run_security(client, reporter)
    except (TimeoutError, ReadTimeoutError) as e:
        reporter.add_finding(Finding(
            title="Profiling timed out",
            description=str(e),
            severity=Severity.CRITICAL, category="conformance",
            recommendation="Server may be hung; investigate responsiveness",
        ))
    finally:
        if args.timeout > 0:
            signal.alarm(0)

    render_report(reporter)

    if args.json:
        reporter.save(args.json)
        print(f"  {CHECK} JSON report saved to {CYAN}{args.json}{RESET}")
        print()


def cmd_tools(args):
    banner()
    section("Tool Enumeration")
    print(f"  {BULLET} Server: {CYAN}{args.server_command}{RESET}")
    print()

    try:
        with MockMCPClient.over_stdio(args.server_command, timeout=15) as client:
            resp = client.initialize()
            if resp is None or resp.is_error:
                print(f"  {CROSS} {RED}Server failed to initialize{RESET}")
                sys.exit(1)

            info = resp.result.get("serverInfo", {})
            caps = list(resp.result.get("capabilities", {}).keys())
            print(f"  {CHECK} Connected: {BOLD}{info.get('name', '?')}{RESET} {DIM}v{info.get('version', '?')}{RESET}")
            print(f"  {BULLET} Capabilities: {DIM}{', '.join(caps)}{RESET}")
            print()

            tools_resp = client.list_tools()
            if tools_resp is None or tools_resp.is_error:
                print(f"  {CROSS} {YELLOW}Server has no tools{RESET}")
                sys.exit(1)

            tools = tools_resp.result.get("tools", [])
            print(f"  {BOLD}{'TOOL':<28s} {'TOKENS':<6s}  {'DESCRIPTION'}{RESET}")
            divider()

            total_tokens = 0
            for t in tools:
                name = t.get("name", "?")
                desc = t.get("description", "")[:70]
                tokens = estimate_tokens(json.dumps(t))
                total_tokens += tokens
                props = list(t.get("inputSchema", {}).get("properties", {}).keys())
                req = t.get("inputSchema", {}).get("required", [])

                print(f"  {CYAN}{name:<28s}{RESET} {DIM}~{tokens:<4d}{RESET}  {desc}")
                if props:
                    print(f"  {DIM}  params: [{', '.join(props)}]  required: [{', '.join(req)}]{RESET}")

            print()
            divider()
            pct = total_tokens * 100 / 200_000
            print(f"  {BOLD}{len(tools)} tools{RESET} {DOT} {BOLD}~{total_tokens} tokens{RESET} total {DIM}({pct:.1f}% of 200k context){RESET}")
            print()

    except ReadTimeoutError:
        print(f"  {CROSS} {RED}Server timed out{RESET}")
        sys.exit(1)


def cmd_test(args):
    import subprocess

    suite = args.suite or "all"
    pytest_args = [sys.executable, "-m", "pytest", "-v", "--tb=short"]

    banner()
    section("Running Tests")

    if suite == "all":
        print(f"  {BULLET} Suite: {BOLD}all{RESET}")
        pytest_args.append(str(PROJECT_DIR / "tests"))
    elif suite == "fast":
        print(f"  {BULLET} Suite: {BOLD}fast{RESET} {DIM}(excludes slow tests){RESET}")
        pytest_args += ["-m", "not slow", str(PROJECT_DIR / "tests")]
    elif suite in ("conformance", "security", "transport", "evaluation", "integration", "fuzz", "fuzzing"):
        if suite == "fuzzing":
            suite = "fuzz"
        print(f"  {BULLET} Suite: {BOLD}{suite}{RESET}")
        pytest_args += ["-m", suite, str(PROJECT_DIR / "tests")]
    else:
        print(f"  {CROSS} Unknown suite: {suite}")
        print(f"  {DIM}Available: all, conformance, security, transport, evaluation, integration, fuzz, fast{RESET}")
        sys.exit(1)

    print()
    sys.stdout.flush()
    proc = subprocess.run(pytest_args)
    sys.exit(proc.returncode)


def cmd_record(args):
    import subprocess

    banner()
    section("Recording Traffic")
    print(f"  {BULLET} Server: {CYAN}{args.server_command}{RESET}")
    print(f"  {BULLET} Output: {CYAN}{args.output}{RESET}")
    print(f"  {DIM}  Press Ctrl+C to stop recording{RESET}")
    print()

    proc = subprocess.run([
        sys.executable, "-m", "harness.interceptor",
        "--cmd", args.server_command,
        "--log", args.output,
    ])
    print()
    print(f"  {CHECK} Traffic saved to {CYAN}{args.output}{RESET}")


def cmd_report(args):
    banner()
    if not os.path.isfile(args.json_file):
        print(f"  {CROSS} {RED}File not found: {args.json_file}{RESET}")
        sys.exit(1)
    render_json_report(args.json_file)


# ── Argument parser ───────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcplab",
        description="MCP Lab — test, profile, and audit MCP servers",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="command")

    # profile
    p = sub.add_parser("profile", help="Profile a real MCP server")
    p.add_argument("server_command", help="Command to start the MCP server")
    p.add_argument("--timeout", type=int, default=60, help="Global timeout in seconds")
    p.add_argument("--json", default=None, help="Save JSON report to this path")
    p.add_argument("--suites", nargs="*", choices=["conformance", "latency", "security"],
                    help="Which suites to run (default: all)")

    # tools
    p = sub.add_parser("tools", help="List tools exposed by an MCP server")
    p.add_argument("server_command", help="Command to start the MCP server")

    # test
    p = sub.add_parser("test", help="Run test suites against the mock harness")
    p.add_argument("suite", nargs="?", default="all",
                    help="Suite to run (all, conformance, security, transport, evaluation, integration, fuzz, fast)")

    # record
    p = sub.add_parser("record", help="Record MCP traffic to a JSONL file")
    p.add_argument("server_command", help="Command to start the MCP server")
    p.add_argument("-o", "--output", default="traffic.jsonl", help="Output file")

    # report
    p = sub.add_parser("report", help="Render a JSON report in the terminal")
    p.add_argument("json_file", help="Path to the JSON report file")

    # help / version
    sub.add_parser("help", help="Show help")
    sub.add_parser("version", help="Show version")

    return parser


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None or args.command == "help":
        banner()
        parser.print_help()
        print()
        print(f"  {BOLD}{WHITE}Examples:{RESET}")
        divider()
        print(f"  {DIM}# Profile a server (conformance, latency, security){RESET}")
        print(f"  {CYAN}mcplab profile \"npx -y @modelcontextprotocol/server-filesystem /tmp\"{RESET}")
        print()
        print(f"  {DIM}# List all tools a server exposes{RESET}")
        print(f"  {CYAN}mcplab tools \"npx -y @modelcontextprotocol/server-filesystem /tmp\"{RESET}")
        print()
        print(f"  {DIM}# Run specific test suites{RESET}")
        print(f"  {CYAN}mcplab test conformance{RESET}")
        print(f"  {CYAN}mcplab test security{RESET}")
        print()
        print(f"  {DIM}# Record MCP traffic to a file{RESET}")
        print(f"  {CYAN}mcplab record \"npx -y @modelcontextprotocol/server-memory\" -o traffic.jsonl{RESET}")
        print()
        print(f"  {DIM}# Save and view reports{RESET}")
        print(f"  {CYAN}mcplab profile \"python my_server.py\" --json report.json{RESET}")
        print(f"  {CYAN}mcplab report report.json{RESET}")
        print()
        return

    if args.command == "version":
        banner()
        return

    dispatch = {
        "profile": cmd_profile,
        "tools": cmd_tools,
        "test": cmd_test,
        "record": cmd_record,
        "report": cmd_report,
    }

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
