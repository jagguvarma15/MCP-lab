"""
Evaluation Tests -- measuring the real cost of MCP integrations.

These tests quantify things that are easy to overlook:
- How many tokens do tool descriptions consume?
- How does schema complexity affect tool call accuracy?
- What's the overhead of MCP vs direct API calls?

Run: pytest tests/evaluation/ -v
"""

import json
import pytest
import sys

from harness import Tool, ToolParam, TestReporter, Finding, Severity


# ---------------------------------------------------------------------------
# Token Estimation (rough, for measurement purposes)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Test: Context Window Cost of Tool Descriptions
# ---------------------------------------------------------------------------

class TestContextCost:
    """
    Every MCP tool's schema gets serialized into the model's context window.
    This directly reduces the space available for conversation.
    How expensive are different tool designs?
    """

    def test_minimal_tool_cost(self):
        """Baseline: simplest possible tool."""
        tool = Tool(name="ping", description="Ping")
        schema = json.dumps(tool.to_schema())
        tokens = estimate_tokens(schema)
        print(f"\nMinimal tool: {len(schema)} chars ~ {tokens} tokens")
        assert tokens < 50

    def test_realistic_tool_cost(self):
        """A realistic tool with 5 parameters."""
        tool = Tool(
            name="search_documents",
            description=(
                "Search through the document repository using full-text search. "
                "Returns matching documents with relevance scores. Supports "
                "filtering by date range, document type, and author."
            ),
            params=[
                ToolParam(name="query", type="string", description="Search query text"),
                ToolParam(name="max_results", type="integer", description="Maximum results to return (1-100)"),
                ToolParam(name="date_from", type="string", description="Start date in ISO 8601 format", required=False),
                ToolParam(name="date_to", type="string", description="End date in ISO 8601 format", required=False),
                ToolParam(name="doc_type", type="string", description="Filter by document type",
                          enum=["pdf", "docx", "txt", "markdown"], required=False),
            ],
        )
        schema = json.dumps(tool.to_schema(), indent=2)
        tokens = estimate_tokens(schema)
        print(f"\nRealistic tool (5 params): {len(schema)} chars ~ {tokens} tokens")

    def test_complex_tool_cost(self):
        """A complex tool with many parameters -- represents enterprise integrations."""
        params = []
        for i in range(20):
            params.append(ToolParam(
                name=f"param_{i}",
                type="string",
                description=f"Parameter {i} for the enterprise workflow integration service endpoint handler",
                required=i < 5,
            ))

        tool = Tool(
            name="enterprise_workflow_executor",
            description=(
                "Execute a complex enterprise workflow across multiple backend systems. "
                "This tool integrates with SAP, Salesforce, and internal microservices "
                "to orchestrate multi-step business processes including approval chains, "
                "data validation, cross-system synchronization, and audit logging. "
                "Supports both synchronous and asynchronous execution modes."
            ),
            params=params,
        )
        schema = json.dumps(tool.to_schema(), indent=2)
        tokens = estimate_tokens(schema)
        print(f"\nComplex tool (20 params): {len(schema)} chars ~ {tokens} tokens")

    def test_multi_server_cost(self):
        """Total cost when connecting 5 servers with 5 tools each."""
        total_tokens = 0
        tool_count = 0

        for server_idx in range(5):
            for tool_idx in range(5):
                tool = Tool(
                    name=f"server{server_idx}_tool{tool_idx}",
                    description=f"Tool {tool_idx} from server {server_idx}. "
                    f"Performs operations related to {['search', 'create', 'update', 'delete', 'list'][tool_idx]}.",
                    params=[
                        ToolParam(name="input", type="string", description="The input to process"),
                        ToolParam(name="options", type="string", description="Processing options", required=False),
                    ],
                )
                schema = json.dumps(tool.to_schema())
                total_tokens += estimate_tokens(schema)
                tool_count += 1

        print(f"\n{tool_count} tools across 5 servers: ~ {total_tokens} tokens")
        print(f"That's roughly {total_tokens / 200_000 * 100:.1f}% of a 200K context window")

    def test_description_verbosity_comparison(self):
        """Compare concise vs verbose descriptions for the same tool."""
        concise = Tool(
            name="send_email",
            description="Send an email.",
            params=[
                ToolParam(name="to", type="string", description="Recipient"),
                ToolParam(name="subject", type="string", description="Subject line"),
                ToolParam(name="body", type="string", description="Email body"),
            ],
        )
        verbose = Tool(
            name="send_email",
            description=(
                "Send an email message to a specified recipient. This tool composes "
                "and sends an email through the configured email service provider. "
                "The email will be sent from the authenticated user's account. "
                "Supports plain text and HTML body content. Delivery is not guaranteed "
                "and may be subject to spam filtering by the recipient's mail server."
            ),
            params=[
                ToolParam(name="to", type="string",
                          description="The email address of the intended recipient. Must be a valid email format (user@domain.tld)."),
                ToolParam(name="subject", type="string",
                          description="The subject line of the email. Keep concise for best deliverability. Max 998 characters per RFC 2822."),
                ToolParam(name="body", type="string",
                          description="The main body content of the email message. Can be plain text or HTML markup. "
                          "For HTML, wrap in <html> tags. Images must be referenced by URL, not embedded."),
            ],
        )

        concise_tokens = estimate_tokens(json.dumps(concise.to_schema()))
        verbose_tokens = estimate_tokens(json.dumps(verbose.to_schema()))
        overhead = verbose_tokens - concise_tokens

        print(f"\nConcise: ~{concise_tokens} tokens")
        print(f"Verbose: ~{verbose_tokens} tokens")
        print(f"Verbosity overhead: +{overhead} tokens ({overhead / concise_tokens * 100:.0f}% more)")


# ---------------------------------------------------------------------------
# Test: Schema Design Patterns
# ---------------------------------------------------------------------------

class TestSchemaPatterns:
    """Compare different approaches to structuring tool schemas."""

    def test_flat_vs_nested_params(self):
        """Flat parameters vs nested object parameter."""
        flat = Tool(
            name="create_event",
            description="Create a calendar event",
            params=[
                ToolParam(name="title", type="string"),
                ToolParam(name="start_date", type="string"),
                ToolParam(name="start_time", type="string"),
                ToolParam(name="end_date", type="string"),
                ToolParam(name="end_time", type="string"),
                ToolParam(name="location", type="string", required=False),
                ToolParam(name="description", type="string", required=False),
            ],
        )

        # Nested approach -- fewer params, structured data
        nested = Tool(
            name="create_event",
            description="Create a calendar event",
            params=[
                ToolParam(name="title", type="string"),
                ToolParam(name="time_range", type="object",
                          description="JSON object with start and end ISO timestamps"),
                ToolParam(name="details", type="object",
                          description="JSON object with optional location and description"),
            ],
        )

        flat_tokens = estimate_tokens(json.dumps(flat.to_schema()))
        nested_tokens = estimate_tokens(json.dumps(nested.to_schema()))

        print(f"\nFlat (7 params): ~{flat_tokens} tokens")
        print(f"Nested (3 params): ~{nested_tokens} tokens")
        print(f"Savings: {flat_tokens - nested_tokens} tokens")

    def test_many_small_tools_vs_few_large_tools(self):
        """Compare 10 focused tools vs 2 multi-purpose tools."""
        # 10 small tools
        small_tools_tokens = 0
        for action in ["create", "read", "update", "delete", "list",
                        "search", "export", "import", "archive", "restore"]:
            tool = Tool(
                name=f"{action}_document",
                description=f"{action.title()} a document",
                params=[ToolParam(name="id", type="string")],
            )
            small_tools_tokens += estimate_tokens(json.dumps(tool.to_schema()))

        # 2 large tools
        large_tools_tokens = 0
        for name, actions in [("document_write", ["create", "update", "delete", "import", "archive"]),
                              ("document_read", ["read", "list", "search", "export", "restore"])]:
            tool = Tool(
                name=name,
                description=f"Perform document operations: {', '.join(actions)}",
                params=[
                    ToolParam(name="action", type="string",
                              description=f"One of: {', '.join(actions)}", enum=actions),
                    ToolParam(name="id", type="string", required=False),
                    ToolParam(name="data", type="string", required=False),
                ],
            )
            large_tools_tokens += estimate_tokens(json.dumps(tool.to_schema()))

        print(f"\n10 small tools: ~{small_tools_tokens} tokens")
        print(f"2 large tools: ~{large_tools_tokens} tokens")
        print(f"Difference: {small_tools_tokens - large_tools_tokens} tokens")
