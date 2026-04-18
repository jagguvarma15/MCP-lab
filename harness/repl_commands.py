"""
REPL command handlers for the interactive MCP Lab shell.

Each command is a function: cmd_xxx(session, args) -> None
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from harness.mock_client import MockMCPClient, ReadTimeoutError
from harness.config import list_server_presets
from harness.tokenizer import estimate_tokens

if TYPE_CHECKING:
    from harness.repl import REPLSession


def _find_tool_owner(session: REPLSession, tool_name: str) -> str | None:
    """Find which server alias owns a tool."""
    for alias, tools in session.tools_cache.items():
        for t in tools:
            if t.get("name") == tool_name:
                return alias
    return None


def _all_tool_names(session: REPLSession) -> list[str]:
    """Get all tool names across all connected servers."""
    names = []
    for tools in session.tools_cache.values():
        for t in tools:
            names.append(t.get("name", ""))
    return names


# ── Commands ──────────────────────────────────────────────────────────


def cmd_connect(session: REPLSession, args: list[str]):
    if not args:
        session.console.print("[red]Usage: connect <command> or connect --preset <name>[/red]")
        return

    if args[0] == "--preset":
        if len(args) < 2:
            presets = list_server_presets()
            session.console.print(f"Available presets: {', '.join(presets)}")
            return
        preset_name = args[1]
        command = f"{sys.executable} -m harness.mock_server --preset {preset_name}"
    else:
        command = " ".join(args)

    session.console.print(f"[dim]Connecting to: {command}[/dim]")

    try:
        client = MockMCPClient.over_stdio(command, timeout=15)
        resp = client.initialize()
    except Exception as e:
        session.console.print(f"[red]Failed to connect: {e}[/red]")
        return

    if resp is None or resp.is_error:
        err = resp.error if resp else "No response"
        session.console.print(f"[red]Server failed to initialize: {err}[/red]")
        try:
            client.shutdown()
        except Exception:
            pass
        return

    # Extract server info
    result = resp.result or {}
    info = result.get("serverInfo", {})
    server_name = info.get("name", "server")
    version = info.get("version", "?")
    caps = list(result.get("capabilities", {}).keys())

    # Generate unique alias
    alias = server_name
    counter = 2
    while alias in session.clients:
        alias = f"{server_name}_{counter}"
        counter += 1

    # Cache tools
    tools_resp = client.list_tools()
    tools = []
    if tools_resp and tools_resp.result:
        tools = tools_resp.result.get("tools", [])

    session.clients[alias] = client
    session.commands[alias] = command
    session.tools_cache[alias] = tools

    session.console.print(Panel(
        f"[bold]{server_name}[/bold] v{version}\n"
        f"Capabilities: [dim]{', '.join(caps) or 'none'}[/dim]\n"
        f"Tools: [bold]{len(tools)}[/bold]",
        title=f"[green]Connected[/green] [{alias}]",
        border_style="green",
    ))


def cmd_disconnect(session: REPLSession, args: list[str]):
    if not session.clients:
        session.console.print("[yellow]No servers connected[/yellow]")
        return

    alias = args[0] if args else None
    if alias is None:
        if len(session.clients) == 1:
            alias = next(iter(session.clients))
        else:
            session.console.print("[yellow]Multiple servers connected. Specify which: disconnect <alias>[/yellow]")
            return

    if alias not in session.clients:
        session.console.print(f"[red]Unknown server: {alias}[/red]")
        return

    client = session.clients.pop(alias)
    session.tools_cache.pop(alias, None)
    session.commands.pop(alias, None)
    try:
        client.shutdown()
    except Exception:
        pass
    session.console.print(f"[dim]Disconnected from {alias}[/dim]")


def cmd_servers(session: REPLSession, args: list[str]):
    if not session.clients:
        session.console.print("[dim]No servers connected. Use 'connect <command>' to connect.[/dim]")
        return

    table = Table(title="Connected Servers")
    table.add_column("Alias", style="cyan")
    table.add_column("Tools", justify="right")
    table.add_column("Command", style="dim")

    for alias in session.clients:
        tool_count = len(session.tools_cache.get(alias, []))
        cmd = session.commands.get(alias, "?")
        table.add_row(alias, str(tool_count), cmd)

    session.console.print(table)


def cmd_tools(session: REPLSession, args: list[str]):
    if not session.clients:
        session.console.print("[dim]No servers connected.[/dim]")
        return

    # Filter to specific server if specified
    if args:
        aliases = [a for a in args if a in session.clients]
        if not aliases:
            session.console.print(f"[red]Unknown server: {args[0]}[/red]")
            return
    else:
        aliases = list(session.clients.keys())

    table = Table(title="Tools")
    table.add_column("Tool", style="cyan")
    if len(aliases) > 1:
        table.add_column("Server", style="dim")
    table.add_column("Tokens", justify="right", style="dim")
    table.add_column("Description")

    total_tokens = 0
    total_tools = 0

    for alias in aliases:
        for t in session.tools_cache.get(alias, []):
            name = t.get("name", "?")
            desc = t.get("description", "")[:80]
            tokens = estimate_tokens(json.dumps(t))
            total_tokens += tokens
            total_tools += 1

            row = [name]
            if len(aliases) > 1:
                row.append(alias)
            row.extend([f"~{tokens}", desc])
            table.add_row(*row)

    session.console.print(table)
    session.console.print(
        f"[bold]{total_tools} tools[/bold] [dim]|[/dim] "
        f"[bold]~{total_tokens} tokens[/bold] [dim]({total_tokens * 100 / 200_000:.1f}% of 200k context)[/dim]"
    )


def cmd_call(session: REPLSession, args: list[str], raw_line: str = ""):
    if not args:
        session.console.print("[red]Usage: call <tool_name> [json_args][/red]")
        return

    tool_name = args[0]
    alias = _find_tool_owner(session, tool_name)
    if alias is None:
        session.console.print(f"[red]Tool not found: {tool_name}[/red]")
        return

    # Extract JSON from raw line to preserve quotes in keys
    tool_args = {}
    brace_pos = raw_line.find("{")
    if brace_pos != -1:
        raw_json = raw_line[brace_pos:]
        try:
            tool_args = json.loads(raw_json)
        except json.JSONDecodeError as e:
            session.console.print(f"[red]Invalid JSON: {e}[/red]")
            return

    client = session.clients[alias]
    try:
        resp = client.call_tool(tool_name, tool_args)
    except ReadTimeoutError:
        session.console.print(f"[red]Server timed out[/red]")
        return

    if resp is None:
        session.console.print("[red]No response from server[/red]")
        return

    elapsed = f"{resp.elapsed_ms:.1f}ms"

    # Record if recording
    if session.recording and session.record_file:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "server": alias,
            "tool": tool_name,
            "arguments": tool_args,
            "response": resp.raw,
            "elapsed_ms": resp.elapsed_ms,
        }
        session.record_file.write(json.dumps(entry) + "\n")
        session.record_file.flush()

    # Add to history
    session.history.append({
        "time": datetime.now().isoformat(),
        "command": f"call {tool_name}",
        "server": alias,
        "elapsed_ms": resp.elapsed_ms,
    })

    result_json = json.dumps(resp.raw, indent=2)
    session.console.print(Panel(
        Syntax(result_json, "json", theme="monokai"),
        title=f"[cyan]{tool_name}[/cyan] [dim]({elapsed} via {alias})[/dim]",
        border_style="cyan" if not resp.is_error else "red",
    ))


def cmd_inspect(session: REPLSession, args: list[str]):
    if not args:
        session.console.print("[red]Usage: inspect <tool_name>[/red]")
        return

    tool_name = args[0]

    # Find the tool schema
    for alias, tools in session.tools_cache.items():
        for t in tools:
            if t.get("name") == tool_name:
                schema_json = json.dumps(t, indent=2)
                session.console.print(Panel(
                    Syntax(schema_json, "json", theme="monokai"),
                    title=f"[cyan]{tool_name}[/cyan] [dim](from {alias})[/dim]",
                    border_style="cyan",
                ))

                # Show parameter summary
                props = t.get("inputSchema", {}).get("properties", {})
                required = set(t.get("inputSchema", {}).get("required", []))
                if props:
                    ptable = Table(title="Parameters")
                    ptable.add_column("Name", style="cyan")
                    ptable.add_column("Type")
                    ptable.add_column("Required")
                    ptable.add_column("Description", style="dim")
                    for pname, pschema in props.items():
                        ptable.add_row(
                            pname,
                            pschema.get("type", "?"),
                            "[green]yes[/green]" if pname in required else "no",
                            pschema.get("description", ""),
                        )
                    session.console.print(ptable)
                return

    session.console.print(f"[red]Tool not found: {tool_name}[/red]")


def cmd_record(session: REPLSession, args: list[str]):
    if not args:
        session.console.print("[red]Usage: record start [filename] | record stop[/red]")
        return

    action = args[0]

    if action == "start":
        if session.recording:
            session.console.print(f"[yellow]Already recording to {session.record_path}[/yellow]")
            return
        path = args[1] if len(args) > 1 else "traffic.jsonl"
        session.record_path = path
        session.record_file = open(path, "a")
        session.recording = True
        session.console.print(f"[green]Recording traffic to {path}[/green]")

    elif action == "stop":
        if not session.recording:
            session.console.print("[yellow]Not recording[/yellow]")
            return
        session.recording = False
        if session.record_file:
            session.record_file.close()
            session.record_file = None
        session.console.print(f"[green]Recording stopped. Saved to {session.record_path}[/green]")
        session.record_path = None

    else:
        session.console.print("[red]Usage: record start [filename] | record stop[/red]")


def cmd_test(session: REPLSession, args: list[str]):
    suite = args[0] if args else "all"
    valid_suites = ["all", "conformance", "security", "transport", "evaluation", "integration", "fuzz", "fast"]

    if suite not in valid_suites:
        session.console.print(f"[red]Unknown suite: {suite}[/red]")
        session.console.print(f"[dim]Available: {', '.join(valid_suites)}[/dim]")
        return

    project_dir = Path(__file__).resolve().parent.parent
    pytest_args = [sys.executable, "-m", "pytest", "-v", "--tb=short"]

    if suite == "all":
        pytest_args.append(str(project_dir / "tests"))
    elif suite == "fast":
        pytest_args += ["-m", "not slow", str(project_dir / "tests")]
    else:
        pytest_args += ["-m", suite, str(project_dir / "tests")]

    session.console.print(f"[dim]Running {suite} tests...[/dim]")
    subprocess.run(pytest_args)


def cmd_profile(session: REPLSession, args: list[str]):
    if not session.clients:
        session.console.print("[dim]No servers connected. Connect first with 'connect <command>'.[/dim]")
        return

    alias = args[0] if args else None
    if alias is None:
        if len(session.clients) == 1:
            alias = next(iter(session.clients))
        else:
            session.console.print("[yellow]Multiple servers connected. Specify which: profile <alias>[/yellow]")
            return

    if alias not in session.clients:
        session.console.print(f"[red]Unknown server: {alias}[/red]")
        return

    command = session.commands.get(alias)
    if not command:
        session.console.print("[red]Cannot profile: unknown server command[/red]")
        return

    # Profile with a fresh client to avoid state issues
    session.console.print(f"[dim]Profiling {alias}...[/dim]")

    from harness.cli import _run_conformance, _run_latency, _run_security, render_report
    from harness.reporter import TestReporter

    try:
        with MockMCPClient.over_stdio(command, timeout=10) as client:
            reporter = TestReporter(suite_name=alias)
            _run_conformance(client, reporter)
            _run_latency(client, reporter)
            _run_security(client, reporter)
            render_report(reporter)
    except (ReadTimeoutError, TimeoutError) as e:
        session.console.print(f"[red]Profiling timed out: {e}[/red]")


def cmd_history(session: REPLSession, args: list[str]):
    if not session.history:
        session.console.print("[dim]No history yet.[/dim]")
        return

    table = Table(title="History")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Time", style="dim")
    table.add_column("Command")
    table.add_column("Server", style="cyan")
    table.add_column("Elapsed", justify="right")

    for i, entry in enumerate(session.history, 1):
        table.add_row(
            str(i),
            entry.get("time", "?")[-12:],
            entry.get("command", "?"),
            entry.get("server", "-"),
            f"{entry.get('elapsed_ms', 0):.1f}ms" if entry.get("elapsed_ms") else "-",
        )

    session.console.print(table)


def cmd_help(session: REPLSession, args: list[str]):
    table = Table(title="Commands", show_header=False, box=None, padding=(0, 2))
    table.add_column("Command", style="cyan bold")
    table.add_column("Description")

    commands = [
        ("connect <cmd>", "Connect to an MCP server via stdio"),
        ("connect --preset <name>", "Connect using a fixture preset"),
        ("disconnect [server]", "Disconnect from a server"),
        ("servers", "List connected servers"),
        ("tools [server]", "List available tools"),
        ("call <tool> [json]", "Call a tool with optional JSON arguments"),
        ("inspect <tool>", "Show full tool schema"),
        ("record start [file]", "Start recording traffic to JSONL"),
        ("record stop", "Stop recording"),
        ("test [suite]", "Run a test suite"),
        ("profile [server]", "Profile a connected server"),
        ("history", "Show call history"),
        ("help", "Show this help"),
        ("exit / quit", "Exit the REPL"),
    ]

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    session.console.print(table)


# ── Dispatch ──────────────────────────────────────────────────────────

COMMANDS = {
    "connect": cmd_connect,
    "disconnect": cmd_disconnect,
    "servers": cmd_servers,
    "tools": cmd_tools,
    "call": cmd_call,
    "inspect": cmd_inspect,
    "record": cmd_record,
    "test": cmd_test,
    "profile": cmd_profile,
    "history": cmd_history,
    "help": cmd_help,
}

COMMAND_NAMES = list(COMMANDS.keys()) + ["exit", "quit"]


def dispatch(session: REPLSession, line: str) -> bool:
    """Dispatch a command line. Returns True to exit the REPL."""
    if not line:
        return False

    try:
        parts = shlex.split(line)
    except ValueError as e:
        session.console.print(f"[red]Parse error: {e}[/red]")
        return False

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("exit", "quit"):
        return True

    handler = COMMANDS.get(cmd)
    if handler is None:
        session.console.print(f"[red]Unknown command: {cmd}[/red]. Type 'help' for available commands.")
        return False

    try:
        handler(session, args)
    except Exception as e:
        session.console.print(f"[red]Error: {e}[/red]")

    return False
