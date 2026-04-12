# MCP Threat Model

A structured threat model of the Model Context Protocol covering trust boundaries,
attack surfaces, concrete attack scenarios, and mitigations.

---

## Trust Boundaries

MCP has three primary trust boundaries where assumptions about behavior
can be violated.

### Client <-> Server

The client trusts that server-provided tool descriptions and results are
honest. This is the most critical boundary because tool descriptions and
results are injected directly into the model's context window.

- Tool descriptions become part of the model's prompt
- Tool results are treated as factual responses
- No signature or verification mechanism exists for tool content

### Server <-> External APIs

Servers often proxy calls to external services. The server trusts that
external APIs return valid data, but a compromised external API could
inject malicious content into tool results.

### User <-> Client

The user trusts the client to faithfully represent what tools are available
and what they do. Users typically cannot see raw tool descriptions or verify
that tool calls are routed correctly.

---

## Attack Surfaces

### Tool Descriptions (Prompt Injection)

Tool descriptions are included verbatim in the model's context. A malicious
server can embed instructions in descriptions that override system prompts,
change model behavior, or extract sensitive information.

- **Test reference:** `tests/security/test_trust_boundaries.py::TestToolDescriptionInjection`
- **Fixture:** `fixtures/schemas/adversarial/injection_in_description.json`

### Tool Results (Result Poisoning)

Tool results are returned to the model as trusted content. A malicious server
can return results containing embedded instructions, fake system messages,
or fabricated function calls.

- **Test reference:** `tests/security/test_trust_boundaries.py::TestResultPoisoning`
- **Fixture:** `fixtures/payloads/responses/poisoned_results.json`

### Tool Names (Shadowing)

When multiple servers are connected, a malicious server can register a tool
with the same name as a trusted server's tool, intercepting calls meant for
the legitimate tool.

- **Test reference:** `tests/security/test_trust_boundaries.py::TestToolNameShadowing`
- **Fixture:** `fixtures/servers/adversarial_shadower.json`

### Schemas (Type Confusion, Extra Fields)

Malformed schemas can cause unexpected behavior. Non-standard fields like
`x-execute-on-load` might be processed by naive clients. Type mismatches
between schema declarations and descriptions can confuse models.

- **Test reference:** `tests/security/test_trust_boundaries.py::TestSchemaManipulation`
- **Fixture:** `fixtures/schemas/adversarial/extra_fields.json`

### Transport (MITM, Replay)

Stdio transport has no encryption. HTTP transport without TLS is vulnerable
to interception. There is no built-in message signing or replay protection.

### Authentication (Token Leakage, Credential Phishing)

Tool descriptions can instruct models to include credentials as tool parameters.
Auth tokens should be kept at the transport level (HTTP headers), not passed
as tool arguments.

- **Test reference:** `tests/security/test_trust_boundaries.py::TestAuthLeakage`
- **Test reference:** `tests/integration/test_auth_delegation.py`

---

## Attack Scenarios

### Scenario 1: Description Injection

**Preconditions:** User connects to a malicious MCP server alongside trusted servers.

**Steps:**
1. Malicious server registers tools with injected descriptions
2. Descriptions contain "Ignore all previous instructions..." payloads
3. Model processes descriptions as part of its context
4. Model behavior is altered to follow injected instructions

**Impact:** Model could ignore safety guidelines, exfiltrate data, or
produce harmful output.

**Demonstrated by:** `test_description_with_prompt_injection`

### Scenario 2: Tool Shadowing for Data Exfiltration

**Preconditions:** User has a trusted file-reading server connected.

**Steps:**
1. Malicious server registers `read_file` tool with "Improved version" description
2. Model calls the shadow tool instead of the legitimate one
3. Shadow tool captures file paths and contents
4. Data is exfiltrated to the attacker

**Impact:** Confidential data accessed through shadow tools.

**Demonstrated by:** `test_shadow_tool_captures_input`

### Scenario 3: Context Window Bombing

**Preconditions:** User connects to a server with verbose tools.

**Steps:**
1. Server returns tools with 100K+ character descriptions
2. Descriptions consume most of the model's context window
3. Available space for actual conversation is reduced to unusable levels

**Impact:** Denial of service against the context window.

**Demonstrated by:** `test_description_length_bomb`

### Scenario 4: Result-Based Behavior Manipulation

**Preconditions:** User calls a tool on a malicious server.

**Steps:**
1. Tool returns seemingly normal results with embedded instructions
2. Instructions tell the model to include API keys in future responses
3. Model follows the embedded instructions in subsequent turns

**Impact:** Credential theft, behavior hijacking.

**Demonstrated by:** `test_result_with_embedded_instructions`

### Scenario 5: Unicode Steganography

**Preconditions:** Tool descriptions contain hidden zero-width characters.

**Steps:**
1. Server sends description with zero-width Unicode characters hiding instructions
2. Description appears normal in UIs that render the text
3. Model processes the raw text including hidden characters
4. Hidden instructions are followed

**Impact:** Invisible attack that bypasses human review.

**Demonstrated by:** `test_description_with_hidden_instructions`
**Fixture:** `fixtures/schemas/adversarial/unicode_steganography.json`

---

## Mitigations

### For Clients

- **Description length limits:** Enforce a maximum description length (e.g., 4096 characters)
  and truncate or reject descriptions that exceed it.
- **Tool allowlists:** Maintain a list of approved tool names and reject tools from
  untrusted servers that shadow known tool names.
- **Result sanitization:** Strip known injection patterns (`[SYSTEM]`, `IMPORTANT:`,
  fake function call syntax) from tool results before passing to the model.
- **Namespace isolation:** Prefix tool names with the server name to prevent collisions
  (e.g., `server-a/read_file` vs `server-b/read_file`).
- **Unicode normalization:** Strip zero-width characters and other invisible Unicode
  from descriptions and results.

### For Servers

- **Transport encryption:** Use TLS for HTTP/SSE transport. Consider signing
  messages for stdio transport.
- **Auth header isolation:** Never accept credentials as tool parameters. Use
  transport-level authentication (HTTP headers, environment variables).
- **Input validation:** Validate all tool arguments against the declared schema
  before processing.
- **Rate limiting:** Implement rate limits to prevent abuse.

### For the Spec

- **Define description length limits:** The spec currently has no maximum.
- **Require tool name uniqueness enforcement:** Across connected servers.
- **Standardize error codes:** Define specific error codes for auth failures,
  tool not found, and lifecycle violations.
- **Add tool provenance:** Include server identity in tool metadata so clients
  can attribute tools to their source.
