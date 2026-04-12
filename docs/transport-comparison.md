# Transport Comparison: stdio vs SSE vs Streamable HTTP

Analysis of the three MCP transport mechanisms based on latency profiles,
reconnection behavior, and failure modes from the transport test suite.

---

## Overview

| Property | stdio | SSE | Streamable HTTP |
|---|---|---|---|
| Connection model | Subprocess pipes | Long-lived HTTP + event stream | HTTP request/response |
| Latency | Lowest (~1-5ms) | Medium (~10-50ms) | Medium (~10-50ms) |
| Reconnection | Restart process | Reconnect event stream | Stateless (each request independent) |
| Backpressure | OS pipe buffer | HTTP/TCP flow control | Request queuing |
| Encryption | None (local only) | TLS available | TLS available |
| Remote support | No | Yes | Yes |
| Bidirectional | Yes (stdin/stdout) | Server-push only (+ POST for client) | Request/response only |

---

## Latency Profiles

From `tests/transport/test_latency.py`:

### stdio (Subprocess)

- Initialize handshake: ~5-20ms (includes process startup on first call)
- tools/list: ~1-5ms
- tools/call (echo): ~1-5ms
- Overhead source: JSON serialization, pipe I/O, readline blocking

### HTTP (POST /mcp)

- Initialize handshake: ~10-30ms
- tools/list: ~5-15ms
- tools/call: ~5-15ms
- Overhead source: HTTP framing, TCP connection (keep-alive helps)

### SSE (Event Stream)

- Connection setup: ~20-50ms (HTTP upgrade + event stream negotiation)
- Notifications: ~1-5ms (already connected)
- Client-to-server: Same as HTTP POST (SSE is server-push only)

---

## Reconnection Behavior

### stdio

When the subprocess dies, the client must:
1. Detect the broken pipe (read returns empty)
2. Start a new subprocess
3. Re-initialize (full handshake)
4. Re-list tools (capabilities may have changed)

No partial reconnection is possible. All state is lost.

### SSE

When the event stream drops:
1. Client detects the connection reset
2. Reconnects to the SSE endpoint
3. May need to re-initialize depending on server implementation
4. Server may send missed events if it supports event IDs

`EventSource` API in browsers handles automatic reconnection.

### Streamable HTTP

Each request is independent:
1. Failed request can be retried immediately
2. No connection state to recover
3. Server must re-initialize if it tracks sessions
4. Most resilient to network instability

---

## Failure Modes

### stdio Failures

- **Subprocess crash:** Pipe breaks, all pending reads return empty.
  Client must restart the process entirely.
- **Blocked pipe:** If server writes faster than client reads,
  OS buffer fills and server blocks. No timeout mechanism.
- **Interleaved output:** If server writes to stdout from multiple
  threads, JSON messages can be corrupted.

### SSE Failures

- **Connection timeout:** Long-lived connections may be killed by
  proxies, load balancers, or firewalls.
- **Event ordering:** Events may arrive out of order if the network
  reorders packets (rare but possible with proxies).
- **Missing events:** If the client disconnects and reconnects,
  events during the gap are lost unless the server implements
  event replay.

### HTTP Failures

- **Request timeout:** Individual requests can time out independently.
  Other requests are unaffected.
- **Connection pooling:** HTTP/1.1 keep-alive connections may be
  closed by the server or intermediaries.
- **Rate limiting:** Servers may throttle requests, returning 429
  status codes.

---

## When to Choose Which Transport

| Use Case | Recommended | Why |
|---|---|---|
| Local development | stdio | Lowest latency, simplest setup |
| CI/testing | stdio | No network dependencies |
| Remote server, request/response | Streamable HTTP | Stateless, resilient |
| Remote server, push notifications | SSE | Server can push events |
| Browser-based client | SSE or HTTP | stdio not available |
| High-reliability production | HTTP with retries | Each request independent |
| Low-latency local tools | stdio | Sub-millisecond overhead |

---

## Known Quirks

1. **stdio line buffering:** Python's `sys.stdout` line buffering can
   cause delays. Always call `flush()` after writing.

2. **SSE content type:** Must be `text/event-stream`. Some proxies
   may modify or strip this header.

3. **HTTP POST body size:** No spec limit on request body size.
   Large tool arguments or results can cause issues with some
   HTTP servers or proxies.

4. **stdio stderr:** Server stderr goes to the parent process's
   stderr. Debug output can interfere with test harnesses that
   capture stderr.

5. **Concurrent requests over stdio:** JSON-RPC supports batching,
   but many MCP servers process requests sequentially over stdio.
   Sending multiple requests before reading responses can cause
   deadlocks if the pipe buffer fills.
