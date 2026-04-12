# MCP Spec Gaps

Things the MCP specification does not address or is ambiguous about,
discovered during testing. Each gap includes the observed behavior,
the risk it creates, and a suggested resolution.

---

## 1. No Description Length Limit

**Gap:** The spec does not define a maximum length for tool descriptions
or parameter descriptions.

**Observed:** A server can send 100K+ character descriptions. These are
included in the model's context window, effectively consuming the space
available for conversation.

**Risk:** Context window denial-of-service. A single malicious server
can render the entire conversation unusable by consuming all available
context with verbose descriptions.

**Test:** `tests/security/test_trust_boundaries.py::TestToolDescriptionInjection::test_description_length_bomb`

**Suggested Resolution:** Define a recommended maximum description length
(e.g., 4096 characters) and a required maximum (e.g., 65536 characters).
Clients SHOULD truncate descriptions that exceed the recommended limit.

---

## 2. No Tool Name Uniqueness Enforcement Across Servers

**Gap:** The spec does not address what happens when two connected servers
expose tools with the same name.

**Observed:** Both tools appear in the combined list. The client has no
standard way to disambiguate them. First-registered-wins, last-registered-wins,
or undefined behavior depending on the client implementation.

**Risk:** Tool shadowing attacks. A malicious server registers `read_file`
to intercept calls meant for a trusted file system server.

**Test:** `tests/integration/test_multi_server.py::TestToolNamespaceCollision`
**Test:** `tests/security/test_trust_boundaries.py::TestToolNameShadowing`

**Suggested Resolution:** Require clients to namespace tools by server
(e.g., `server-name/tool-name`). The spec should define a tool identifier
format that includes the server's identity.

---

## 3. tools/list Allowed Before initialize

**Gap:** The spec says servers SHOULD reject requests before initialization,
but does not REQUIRE it.

**Observed:** Some servers respond to `tools/list` before `initialize` is
called. Others ignore the request silently. Behavior is inconsistent.

**Test:** `tests/conformance/test_protocol.py::TestLifecycle::test_tools_list_before_initialize`

**Suggested Resolution:** Change SHOULD to MUST. Pre-initialization requests
MUST receive a `-32002` error (Server not initialized).

---

## 4. No Standard Error Code for "Tool Not Found"

**Gap:** The spec does not define a specific error code for when a client
calls a tool that does not exist on the server.

**Observed:** Different servers handle this differently:
- Some return JSON-RPC `-32601` (Method not found)
- Some return a successful result with an error message in the content
- Some return a custom error code

**Test:** `tests/conformance/test_protocol.py::TestErrorHandling::test_unknown_tool_call`

**Suggested Resolution:** Define a standard error code (e.g., `-32001`)
for "Tool not found" that servers MUST use when a `tools/call` request
references a nonexistent tool.

---

## 5. No Guidance on Multi-Server Tool List Merging

**Gap:** The spec describes single client-server connections but does not
address how clients should handle multiple simultaneous server connections.

**Observed:** Clients must make their own decisions about:
- How to merge tool lists from multiple servers
- How to resolve name collisions
- How to route tool calls to the correct server
- How to handle one server crashing while others are alive

**Test:** `tests/integration/test_multi_server.py` (entire file)

**Suggested Resolution:** Add a section on multi-server scenarios with
recommendations for namespace isolation, collision handling, and graceful
degradation.

---

## 6. Protocol Version Negotiation Underspecified

**Gap:** The spec does not clearly define what should happen when the client
and server support incompatible protocol versions.

**Observed:** When a client sends an unrecognized protocol version, servers
typically respond with their own version. But it is unclear whether:
- The connection should proceed with the server's version
- The client should disconnect if versions are incompatible
- There should be a version negotiation handshake

**Test:** `tests/conformance/test_protocol.py::TestLifecycle::test_protocol_version_negotiation`

**Suggested Resolution:** Define a version negotiation protocol. If the server
cannot support the client's requested version, it MUST return a specific error
code with the list of versions it does support.

---

## 7. No Notification Delivery Guarantees

**Gap:** The spec does not address whether notifications (like `listChanged`)
are guaranteed to be delivered or what happens if they are lost.

**Observed:** Over stdio, notifications are best-effort (pipe may buffer
or drop). Over SSE, notifications during a disconnection are lost.

**Suggested Resolution:** Define notification semantics: at-most-once,
at-least-once, or exactly-once. If at-most-once, recommend that clients
periodically re-fetch capabilities rather than relying solely on notifications.

---

## 8. No Authentication Standard

**Gap:** The spec does not define a standard authentication mechanism.
Servers may implement their own auth patterns.

**Observed:** Some servers expect auth in:
- HTTP headers (reasonable for HTTP transport)
- Environment variables (reasonable for stdio)
- Tool parameters (dangerous -- see threat model)

**Test:** `tests/integration/test_auth_delegation.py`

**Suggested Resolution:** Recommend that authentication be handled at the
transport level (HTTP Authorization headers, environment variables for stdio)
and explicitly state that credentials SHOULD NOT be passed as tool parameters.

---

## 9. No Maximum Batch Size

**Gap:** JSON-RPC 2.0 supports batch requests, but the MCP spec does not
define limits on batch size.

**Observed:** A client could send thousands of requests in a single batch.
Servers with no batch size limit may run out of memory or become unresponsive.

**Test:** `fixtures/payloads/requests/rapid_fire.json` (100 sequential requests)

**Suggested Resolution:** Define a recommended maximum batch size (e.g., 100)
and require servers to return an error for batches that exceed it.

---

## 10. Content Type Extensibility Unclear

**Gap:** Tool results contain `content` arrays with typed blocks (e.g.,
`"type": "text"`). The spec does not clearly define what types are valid
or how unknown types should be handled.

**Observed:** Servers can return content blocks with arbitrary types like
`"executable"` or `"binary"`. Client behavior is undefined.

**Test:** `fixtures/payloads/responses/wrong_content_type.json`

**Suggested Resolution:** Define a registry of valid content types. Clients
MUST ignore content blocks with unrecognized types and SHOULD log a warning.
