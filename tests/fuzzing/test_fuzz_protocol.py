"""
Fuzz Tests -- property-based and mutation testing for MCP protocol robustness.

Uses Hypothesis to generate random/adversarial inputs and verify the server
never crashes, hangs, or produces malformed output. Covers:
- JSON-RPC field mutations (missing fields, wrong types, extra nesting)
- Tool schema generation and validation
- Malformed UTF-8, oversized payloads, binary injection
- Boundary conditions on all string/numeric fields

Run: pytest tests/fuzzing/ -v
Run (quick): pytest tests/fuzzing/ -v --hypothesis-seed=0 -x
"""

import json
import sys
import pytest

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from harness import MockMCPClient, Tool, ToolParam


SERVER_CMD = [sys.executable, "-m", "harness.mock_server"]

# Max time per example to prevent test hangs from server issues
EXAMPLE_DEADLINE_MS = 5000


# ---------------------------------------------------------------------------
# Hypothesis strategies for MCP/JSON-RPC data
# ---------------------------------------------------------------------------

# JSON-RPC scalar values (no recursive structures)
json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=200),
)

# Recursive JSON values (dicts, lists, scalars)
json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

# JSON-RPC IDs (spec allows string, number, or null)
jsonrpc_ids = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=2**31),
    st.text(min_size=1, max_size=50),
)

# Valid JSON-RPC method names
jsonrpc_methods = st.one_of(
    st.just("initialize"),
    st.just("tools/list"),
    st.just("tools/call"),
    st.just("ping"),
    st.just("notifications/initialized"),
    # Unknown methods the server should handle gracefully
    st.text(min_size=1, max_size=100).filter(
        lambda m: m not in {"initialize", "tools/list", "tools/call", "ping", "notifications/initialized"}
    ),
)

# Tool parameter types per JSON Schema spec
param_types = st.sampled_from(["string", "number", "integer", "boolean", "array", "object"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_and_check_no_crash(client: MockMCPClient, message: dict) -> dict | None:
    """Send a message and verify the server didn't crash.

    Returns the parsed response or None (for notifications/drops).
    Never raises -- logs all failures.
    """
    resp = client.send_raw(message)
    # Server must still be alive
    assert client._process.poll() is None, "Server process crashed"
    return resp


# ---------------------------------------------------------------------------
# Test: JSON-RPC field mutations
# ---------------------------------------------------------------------------

@pytest.mark.fuzz
class TestJsonRpcFieldMutations:
    """Verify the server handles malformed JSON-RPC messages gracefully."""

    @given(
        method=jsonrpc_methods,
        req_id=jsonrpc_ids,
        params=st.one_of(st.none(), st.dictionaries(st.text(max_size=20), json_values, max_size=5)),
    )
    @settings(max_examples=50, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_random_valid_structure(self, method, req_id, params):
        """Well-structured requests with random content should never crash the server."""
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            resp = send_and_check_no_crash(client, msg)
            if resp is not None:
                # Response must be valid JSON-RPC
                assert resp.raw.get("jsonrpc") == "2.0"
                assert "result" in resp.raw or "error" in resp.raw

    @given(
        extra_fields=st.dictionaries(
            st.text(min_size=1, max_size=30),
            json_values,
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=30, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_extra_top_level_fields(self, extra_fields):
        """Messages with unexpected top-level fields should not crash the server."""
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "params": {},
        }
        msg.update(extra_fields)

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            send_and_check_no_crash(client, msg)

    @given(
        jsonrpc_field=st.one_of(
            st.just("2.0"),
            st.just("1.0"),
            st.just("3.0"),
            st.text(max_size=20),
            st.integers(),
            st.none(),
        ),
    )
    @settings(max_examples=20, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_mutated_jsonrpc_version(self, jsonrpc_field):
        """Any value in the jsonrpc field should be handled without crashing."""
        msg = {"jsonrpc": jsonrpc_field, "id": 1, "method": "ping", "params": {}}

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            send_and_check_no_crash(client, msg)

    @given(data=st.data())
    @settings(max_examples=30, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_missing_required_fields(self, data):
        """Omitting required JSON-RPC fields should produce errors, not crashes."""
        full_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "params": {},
        }
        # Randomly remove 1-3 fields
        keys_to_remove = data.draw(
            st.lists(
                st.sampled_from(list(full_msg.keys())),
                min_size=1,
                max_size=3,
                unique=True,
            )
        )
        for key in keys_to_remove:
            del full_msg[key]

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            send_and_check_no_crash(client, full_msg)

    @given(
        wrong_type_params=st.one_of(
            st.integers(),
            st.text(max_size=50),
            st.lists(json_values, max_size=3),
            st.booleans(),
        ),
    )
    @settings(max_examples=20, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_params_wrong_type(self, wrong_type_params):
        """Non-object params should be handled gracefully."""
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": wrong_type_params,
        }

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            send_and_check_no_crash(client, msg)


# ---------------------------------------------------------------------------
# Test: Tool schema fuzzing
# ---------------------------------------------------------------------------

@pytest.mark.fuzz
class TestToolSchemaFuzzing:
    """Verify Tool/ToolParam schema generation handles arbitrary input."""

    @given(
        name=st.text(min_size=1, max_size=100),
        description=st.text(max_size=500),
        param_count=st.integers(min_value=0, max_value=20),
        data=st.data(),
    )
    @settings(max_examples=50, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_arbitrary_tool_schema_serialization(self, name, description, param_count, data):
        """Any tool with valid structure should produce valid JSON schema."""
        params = []
        for i in range(param_count):
            p = ToolParam(
                name=data.draw(st.text(min_size=1, max_size=50), label=f"param_{i}_name"),
                type=data.draw(param_types, label=f"param_{i}_type"),
                description=data.draw(st.text(max_size=200), label=f"param_{i}_desc"),
                required=data.draw(st.booleans(), label=f"param_{i}_required"),
            )
            params.append(p)

        tool = Tool(name=name, description=description, params=params)
        schema = tool.to_schema()

        # Schema must be JSON-serializable
        serialized = json.dumps(schema)
        assert json.loads(serialized) == schema

        # Must have required top-level keys
        assert "name" in schema
        assert "description" in schema
        assert "inputSchema" in schema
        assert schema["inputSchema"]["type"] == "object"

    @given(
        name=st.text(min_size=0, max_size=5).filter(lambda x: x.strip() == ""),
        description=st.text(max_size=10),
    )
    @settings(max_examples=10, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_or_whitespace_tool_name(self, name, description):
        """Tools with empty/whitespace names should still produce valid schemas."""
        tool = Tool(name=name, description=description)
        schema = tool.to_schema()
        # Should serialize without error
        json.dumps(schema)

    @given(
        description=st.text(min_size=1000, max_size=10000),
    )
    @settings(max_examples=5, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_large_description(self, description):
        """Very large descriptions should not break schema generation."""
        tool = Tool(name="test", description=description)
        schema = tool.to_schema()
        assert schema["description"] == description


# ---------------------------------------------------------------------------
# Test: Malformed payloads (UTF-8, binary, oversized)
# ---------------------------------------------------------------------------

@pytest.mark.fuzz
class TestMalformedPayloads:
    """Verify the server survives hostile raw input."""

    @given(
        garbage=st.binary(min_size=1, max_size=500),
    )
    @settings(max_examples=20, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_binary_injection(self, garbage):
        """Raw binary data should not crash the server."""
        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            try:
                # Write raw bytes directly, bypassing JSON serialization
                if client._process and client._process.stdin:
                    client._process.stdin.write(garbage.decode("utf-8", errors="replace") + "\n")
                    client._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

            # Server should still be alive (or have exited cleanly)
            poll = client._process.poll()
            assert poll is None or poll == 0, f"Server crashed with exit code {poll}"

    @given(
        text_with_special_chars=st.text(
            alphabet=st.characters(
                categories=("L", "N", "P", "S", "Z", "C"),
            ),
            min_size=1,
            max_size=500,
        ),
    )
    @settings(max_examples=20, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_unicode_edge_cases(self, text_with_special_chars):
        """Unicode control chars, RTL marks, ZWJ, surrogates should not crash the server."""
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {"message": text_with_special_chars},
            },
        }

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            try:
                send_and_check_no_crash(client, msg)
            except Exception:
                # Any exception is fine as long as the server didn't crash
                assert client._process.poll() is None or client._process.poll() == 0

    @given(
        repeat=st.integers(min_value=1000, max_value=50000),
    )
    @settings(max_examples=5, deadline=EXAMPLE_DEADLINE_MS * 2, suppress_health_check=[HealthCheck.too_slow])
    def test_oversized_string_field(self, repeat):
        """Very large string fields should not crash the server."""
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {"message": "A" * repeat},
            },
        }

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=5) as client:
            resp = send_and_check_no_crash(client, msg)
            if resp and resp.result:
                content = resp.result.get("content", [])
                if content:
                    result_text = content[0].get("text", "")
                    parsed = json.loads(result_text)
                    assert len(parsed["echoed"]) == repeat

    @given(depth=st.integers(min_value=5, max_value=50))
    @settings(max_examples=10, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_deeply_nested_params(self, depth):
        """Deeply nested JSON should not cause stack overflow in the server."""
        # Build nested structure: {"a": {"a": {"a": ... "leaf"}}}
        value: dict | str = "leaf"
        for _ in range(depth):
            value = {"a": value}

        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "echo",
                "arguments": {"message": json.dumps(value)},
            },
        }

        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            send_and_check_no_crash(client, msg)


# ---------------------------------------------------------------------------
# Test: Tool call argument fuzzing
# ---------------------------------------------------------------------------

@pytest.mark.fuzz
class TestToolCallFuzzing:
    """Fuzz tool call arguments to find crashes in tool handlers."""

    @given(
        tool_name=st.one_of(
            st.just("echo"),
            st.just("calculator"),
            st.just("slow_operation"),
            st.just("nonexistent"),
            st.text(min_size=0, max_size=100),
        ),
        arguments=st.one_of(
            st.none(),
            st.dictionaries(st.text(max_size=20), json_values, max_size=5),
        ),
    )
    @settings(max_examples=50, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_fuzz_tool_call_arguments(self, tool_name, arguments):
        """Random tool names and arguments should never crash the server."""
        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            client.initialize()
            msg = {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
            }
            send_and_check_no_crash(client, msg)

    @given(
        expression=st.text(max_size=200),
    )
    @settings(max_examples=30, deadline=EXAMPLE_DEADLINE_MS, suppress_health_check=[HealthCheck.too_slow])
    def test_fuzz_calculator_expressions(self, expression):
        """Random strings as calculator expressions should not crash the server."""
        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            client.initialize()
            msg = {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {
                    "name": "calculator",
                    "arguments": {"expression": expression},
                },
            }
            # May return error, but must not crash
            send_and_check_no_crash(client, msg)


# ---------------------------------------------------------------------------
# Test: Rapid-fire / sequence fuzzing
# ---------------------------------------------------------------------------

@pytest.mark.fuzz
class TestSequenceFuzzing:
    """Test random sequences of valid operations."""

    @given(
        methods=st.lists(
            st.sampled_from(["initialize", "tools/list", "tools/call", "ping"]),
            min_size=3,
            max_size=15,
        ),
    )
    @settings(max_examples=20, deadline=EXAMPLE_DEADLINE_MS * 3, suppress_health_check=[HealthCheck.too_slow])
    def test_random_method_sequence(self, methods):
        """Random sequences of valid methods should not crash the server."""
        with MockMCPClient.over_stdio(SERVER_CMD, timeout=3) as client:
            for method in methods:
                params = {}
                if method == "initialize":
                    params = {
                        "protocolVersion": "2025-03-26",
                        "clientInfo": {"name": "fuzz", "version": "0.0.1"},
                        "capabilities": {},
                    }
                elif method == "tools/call":
                    params = {"name": "echo", "arguments": {"message": "fuzz"}}

                msg = {
                    "jsonrpc": "2.0",
                    "id": client._next_id,
                    "method": method,
                    "params": params,
                }
                client._next_id += 1
                send_and_check_no_crash(client, msg)

    @given(
        count=st.integers(min_value=10, max_value=50),
    )
    @settings(max_examples=5, deadline=EXAMPLE_DEADLINE_MS * 3, suppress_health_check=[HealthCheck.too_slow])
    def test_rapid_fire_pings(self, count):
        """Rapid-fire pings should all get responses without crashing."""
        with MockMCPClient.over_stdio(SERVER_CMD, timeout=5) as client:
            client.initialize()
            for _ in range(count):
                resp = client.ping()
                assert resp is not None, "Server stopped responding to pings"
            assert client._process.poll() is None
