"""
Config Loader -- reads fixture files and returns configured harness objects.

Loads server presets from fixtures/servers/, tool schemas from
fixtures/schemas/, and JSON-RPC payload sequences from fixtures/payloads/.

Usage:
    server = load_server_preset("honest")
    schema = load_schema("valid/realistic.json")
    messages = load_payload_sequence("requests/valid_lifecycle.json")
"""

import json
from pathlib import Path
from typing import Any

from harness.mock_server import MockMCPServer, Tool, ToolParam, ServerBehaviors
from harness.mock_server import echo_tool, calculator_tool, slow_tool


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SERVERS_DIR = FIXTURES_DIR / "servers"
SCHEMAS_DIR = FIXTURES_DIR / "schemas"
PAYLOADS_DIR = FIXTURES_DIR / "payloads"

# Map of tool names to pre-built tool instances
BUILTIN_TOOLS = {
    "echo": echo_tool,
    "calculator": calculator_tool,
    "slow_operation": slow_tool,
}


def load_server_preset(name: str) -> MockMCPServer:
    """Load a named server preset from fixtures/servers/.

    Args:
        name: Preset name (without .json extension), e.g. "honest",
              "adversarial_injector", "slow_unreliable".

    Returns:
        A configured MockMCPServer ready to use.
    """
    path = SERVERS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Server preset not found: {path}")

    with open(path) as f:
        config = json.load(f)

    # Build tools list from names
    tools = []
    for tool_name in config.get("tools", []):
        if tool_name in BUILTIN_TOOLS:
            tools.append(BUILTIN_TOOLS[tool_name])
        else:
            # Create a generic tool for unknown names
            tools.append(Tool(
                name=tool_name,
                description=f"Tool: {tool_name}",
                params=[ToolParam(name="input", type="string")],
            ))

    # Build behaviors from config
    behavior_data = config.get("behaviors", {})
    behaviors = ServerBehaviors(
        delay_ms=behavior_data.get("delay_ms", 0),
        delay_jitter_ms=behavior_data.get("delay_jitter_ms", 0),
        drop_rate=behavior_data.get("drop_rate", 0.0),
        error_rate=behavior_data.get("error_rate", 0.0),
        omit_id=behavior_data.get("omit_id", False),
        wrong_jsonrpc_version=behavior_data.get("wrong_jsonrpc_version", False),
        extra_fields=behavior_data.get("extra_fields", {}),
        malformed_json_rate=behavior_data.get("malformed_json_rate", 0.0),
        tool_description_suffix=behavior_data.get("tool_description_suffix", ""),
        shadow_tool_name=behavior_data.get("shadow_tool_name"),
    )

    return MockMCPServer(
        tools=tools,
        server_name=config.get("name", "preset-server"),
        behaviors=behaviors,
    )


def load_server_config(path: str) -> tuple[list[Tool], ServerBehaviors]:
    """Load a server config and return tools and behaviors separately.

    Args:
        path: Path to the server config JSON file.

    Returns:
        Tuple of (tools list, ServerBehaviors).
    """
    server = load_server_preset(Path(path).stem)
    return list(server.tools.values()), server.behaviors


def load_schema(path: str) -> dict:
    """Load a tool schema from fixtures/schemas/.

    Args:
        path: Relative path within fixtures/schemas/, e.g.
              "valid/realistic.json" or "malformed/missing_type.json".

    Returns:
        The parsed JSON schema as a dict.
    """
    full_path = SCHEMAS_DIR / path
    if not full_path.exists():
        raise FileNotFoundError(f"Schema not found: {full_path}")

    with open(full_path) as f:
        return json.load(f)


def load_payload_sequence(path: str) -> list[dict]:
    """Load a sequence of JSON-RPC messages from fixtures/payloads/.

    Args:
        path: Relative path within fixtures/payloads/, e.g.
              "requests/valid_lifecycle.json".

    Returns:
        List of message dicts. Each dict contains at minimum a "request"
        key, and optionally an "expected_response" key.
    """
    full_path = PAYLOADS_DIR / path
    if not full_path.exists():
        raise FileNotFoundError(f"Payload sequence not found: {full_path}")

    with open(full_path) as f:
        return json.load(f)


def list_server_presets() -> list[str]:
    """List available server preset names."""
    return [p.stem for p in SERVERS_DIR.glob("*.json")]


def list_schemas(category: str | None = None) -> list[str]:
    """List available schema paths, optionally filtered by category.

    Args:
        category: One of "valid", "malformed", "adversarial", or None for all.
    """
    if category:
        base = SCHEMAS_DIR / category
        return [f"{category}/{p.name}" for p in base.glob("*.json")] if base.exists() else []
    results = []
    for cat_dir in SCHEMAS_DIR.iterdir():
        if cat_dir.is_dir():
            for p in cat_dir.glob("*.json"):
                results.append(f"{cat_dir.name}/{p.name}")
    return sorted(results)
