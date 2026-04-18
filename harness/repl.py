"""
Interactive REPL shell for MCP Lab.

Provides a prompt_toolkit-based interactive session for connecting to
MCP servers, exploring tools, calling them, and recording traffic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import IO

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from rich.console import Console

from harness.mock_client import MockMCPClient
from harness.config import list_server_presets
from harness.repl_commands import COMMAND_NAMES, dispatch


@dataclass
class REPLSession:
    """State for the interactive REPL."""
    clients: dict[str, MockMCPClient] = field(default_factory=dict)
    commands: dict[str, str] = field(default_factory=dict)       # alias -> spawn command
    tools_cache: dict[str, list[dict]] = field(default_factory=dict)
    recording: bool = False
    record_path: str | None = None
    record_file: IO | None = None
    history: list[dict] = field(default_factory=list)
    console: Console = field(default_factory=Console)


class MCPLabCompleter(Completer):
    """Dynamic completer that reads from session state."""

    def __init__(self, session: REPLSession):
        self.session = session

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        if not words or (len(words) == 1 and not text.endswith(" ")):
            # Complete command names
            prefix = words[0] if words else ""
            for cmd in COMMAND_NAMES:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        cmd = words[0].lower()
        # We're completing the argument(s)

        if cmd == "connect" and len(words) <= 2:
            prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            if not prefix or prefix.startswith("-"):
                if "--preset".startswith(prefix):
                    yield Completion("--preset", start_position=-len(prefix))
            return

        if cmd == "connect" and len(words) >= 2 and words[1] == "--preset":
            prefix = words[2] if len(words) > 2 and not text.endswith(" ") else ""
            for name in list_server_presets():
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return

        if cmd in ("disconnect", "tools", "profile"):
            prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for alias in self.session.clients:
                if alias.startswith(prefix):
                    yield Completion(alias, start_position=-len(prefix))
            return

        if cmd in ("call", "inspect") and len(words) <= 2:
            prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            seen = set()
            for tools in self.session.tools_cache.values():
                for t in tools:
                    name = t.get("name", "")
                    if name.startswith(prefix) and name not in seen:
                        seen.add(name)
                        yield Completion(name, start_position=-len(prefix))
            return

        if cmd == "record" and len(words) <= 2:
            prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            for opt in ("start", "stop"):
                if opt.startswith(prefix):
                    yield Completion(opt, start_position=-len(prefix))
            return

        if cmd == "test" and len(words) <= 2:
            prefix = words[1] if len(words) > 1 and not text.endswith(" ") else ""
            suites = ["all", "conformance", "security", "transport", "evaluation", "integration", "fuzz", "fast"]
            for s in suites:
                if s.startswith(prefix):
                    yield Completion(s, start_position=-len(prefix))
            return


def _build_prompt(session: REPLSession) -> str:
    """Build the prompt string based on session state."""
    n = len(session.clients)
    if n == 0:
        return "mcplab> "
    elif n == 1:
        alias = next(iter(session.clients))
        return f"mcplab[{alias}]> "
    else:
        return f"mcplab[{n} servers]> "


def _cleanup(session: REPLSession):
    """Disconnect all servers and close files."""
    for alias, client in list(session.clients.items()):
        try:
            client.shutdown()
        except Exception:
            pass
    session.clients.clear()
    session.tools_cache.clear()
    session.commands.clear()

    if session.record_file:
        session.record_file.close()
        session.record_file = None
        session.recording = False


def start_repl():
    """Start the interactive MCP Lab REPL."""
    console = Console()
    session = REPLSession(console=console)
    completer = MCPLabCompleter(session)

    history_path = os.path.expanduser("~/.mcplab_history")
    pt_session = PromptSession(
        completer=completer,
        history=FileHistory(history_path),
    )

    # Banner
    console.print()
    console.print("  [bold cyan]MCP Lab[/bold cyan] [dim]interactive shell[/dim]")
    console.print("  [dim]Type 'help' for commands, 'exit' to quit[/dim]")
    console.print()

    try:
        while True:
            try:
                prompt = _build_prompt(session)
                text = pt_session.prompt(prompt).strip()
                if dispatch(session, text):
                    break
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
    finally:
        _cleanup(session)
        console.print("[dim]Goodbye.[/dim]")
