# Conformance Report Template

Use this template to document conformance test results when running the
MCP Lab test suite against a real server implementation.

---

## Server Under Test

| Field | Value |
|---|---|
| Server name | (e.g., @modelcontextprotocol/server-filesystem) |
| Server version | (e.g., 1.2.0) |
| Transport | (stdio / SSE / HTTP) |
| Tested on | (date) |
| Tester | (name or handle) |
| MCP Lab version | (git commit or tag) |

---

## Test Results

### JSON-RPC 2.0 Compliance

| Test | Result | Notes |
|---|---|---|
| Response has jsonrpc: "2.0" | PASS / FAIL | |
| Response echoes request ID | PASS / FAIL | |
| No extra fields in response | PASS / FAIL | |
| Error response format (code + message) | PASS / FAIL | |
| Notification gets no response | PASS / FAIL | |
| Malformed JSON returns -32700 | PASS / FAIL | |

### MCP Lifecycle

| Test | Result | Notes |
|---|---|---|
| Initialize returns serverInfo | PASS / FAIL | |
| Initialize returns capabilities | PASS / FAIL | |
| Protocol version negotiation | PASS / FAIL | |
| tools/list before initialize | PASS / FAIL | Expected: reject |
| Ping response | PASS / FAIL | |

### Error Handling

| Test | Result | Notes |
|---|---|---|
| Unknown method returns -32601 | PASS / FAIL | |
| Unknown tool call returns error | PASS / FAIL | |

### Protocol Violations (Server Resilience)

| Test | Result | Notes |
|---|---|---|
| Handles missing jsonrpc field | PASS / FAIL | |
| Handles wrong jsonrpc version | PASS / FAIL | |
| Handles missing method field | PASS / FAIL | |
| Handles null request ID | PASS / FAIL | |

### Security

| Test | Result | Notes |
|---|---|---|
| Description length handling | PASS / FAIL | Max observed: ___ chars |
| No suspicious parameter names | PASS / FAIL | |
| No extra schema fields | PASS / FAIL | |

### Transport

| Test | Result | Notes |
|---|---|---|
| Initialize latency | ___ms avg | |
| tools/list latency | ___ms avg | |
| tools/call latency | ___ms avg | |
| Behavior under 100ms delay | PASS / FAIL | |
| Behavior under 500ms delay | PASS / FAIL | |

---

## Notable Deviations from Spec

List any behaviors that differ from the MCP specification:

1. (Description of deviation)
2. (Description of deviation)

---

## Tools Exposed

| Tool Name | Parameters | Description Length |
|---|---|---|
| | | |
| | | |

---

## Recommendations

Based on test results, recommended changes for the server author:

1. (Recommendation)
2. (Recommendation)

---

## Raw Output

Attach or link to the full test output:

```
(paste pytest output here)
```

JSON report file: (link or path to JSON report)
