# MCP Glossary

Quick-reference definitions of terms used throughout this repository.

---

**Client** -- Software that connects to an MCP server and makes requests.
In the MCP architecture, the client sits between the host application and
the server. Example: the MCP client built into Claude Desktop.

**Capability** -- A feature that a server declares support for during
initialization. Capabilities include `tools`, `resources`, `prompts`,
and `logging`. Each can have sub-capabilities like `listChanged`.

**Content Block** -- A typed unit of data in a tool result. Each block has
a `type` field (e.g., `"text"`, `"image"`) and type-specific data fields.

**Elicitation** -- A server-initiated request asking the client (and
ultimately the user) for additional input during a tool execution.

**Host** -- The application that embeds an MCP client. Examples: Claude
Desktop, an IDE plugin, or a custom application. The host manages the
user interface and orchestrates client-server connections.

**Initialize** -- The first message in an MCP session. The client sends
its protocol version and capabilities; the server responds with its own.
No other requests should be processed before initialization completes.

**JSON-RPC** -- JSON Remote Procedure Call, version 2.0. The wire protocol
used by MCP. Messages have `jsonrpc`, `id`, `method`, and `params` fields.
Notifications omit the `id` field.

**listChanged** -- A notification that a server sends when its list of
tools, resources, or prompts changes. Clients that declared support for
this capability should re-fetch the relevant list when they receive it.

**MCP (Model Context Protocol)** -- An open protocol for connecting LLMs
to external tools, data sources, and services. Defines how clients discover
and invoke server capabilities.

**Notification** -- A JSON-RPC message without an `id` field. Notifications
are fire-and-forget: the sender does not expect a response. Example:
`notifications/initialized`.

**Prompt** -- A server-provided template that generates messages for the
model. Unlike tools (which the model calls), prompts are user-triggered
and produce content that becomes part of the conversation.

**Resource** -- A server-provided data source that can be read by the client.
Resources have URIs and can represent files, database records, API responses,
or any other data. Unlike tools, resources are read-only.

**Sampling** -- A server-initiated request asking the client to generate
a model completion. This allows servers to leverage the model's capabilities
as part of tool execution.

**Server** -- Software that exposes tools, resources, and prompts via the
MCP protocol. Servers can be local processes (stdio transport) or remote
services (HTTP/SSE transport).

**Session** -- A single connection between a client and server, from
`initialize` to disconnection. Sessions are stateless by default: servers
should not retain state between sessions.

**Tool** -- A function that a server exposes for the model to call. Tools
have a name, description, and input schema. The model decides when to call
tools based on the conversation context and tool descriptions.

**Transport** -- The communication channel between client and server.
MCP supports three transports:
- **stdio** -- Communication over standard input/output of a subprocess
- **SSE** -- Server-Sent Events over HTTP (server push + client POST)
- **Streamable HTTP** -- Standard HTTP request/response
