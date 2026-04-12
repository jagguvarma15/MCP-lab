"""
Transport Tests -- measuring latency, reliability, and failure behavior.

These tests profile the performance characteristics of MCP communication
and verify behavior under degraded conditions.

Run: pytest tests/transport/ -v
"""

import pytest
import sys
import os
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from harness import MockMCPClient, TestReporter, Finding, Severity


SERVER_CMD = f"{sys.executable} -m harness.mock_server"


# ---------------------------------------------------------------------------
# Test: Baseline Latency Profiling
# ---------------------------------------------------------------------------

class TestLatencyProfiling:
    """Measure round-trip latency for different MCP operations."""

    def test_initialize_latency(self):
        """Measure how long the initialize handshake takes."""
        timings = []
        for _ in range(10):
            with MockMCPClient.over_stdio(SERVER_CMD) as client:
                resp = client.initialize()
                timings.append(resp.elapsed_ms)

        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        print(f"\nInitialize latency: avg={avg:.1f}ms  p95={p95:.1f}ms")
        # No hard assertion -- this is profiling, not pass/fail

    def test_tool_list_latency(self):
        """Measure tools/list round-trip time."""
        with MockMCPClient.over_stdio(SERVER_CMD) as client:
            client.initialize()
            timings = []
            for _ in range(50):
                resp = client.list_tools()
                timings.append(resp.elapsed_ms)

        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        print(f"\ntools/list latency: avg={avg:.1f}ms  p95={p95:.1f}ms  n={len(timings)}")

    def test_tool_call_latency(self):
        """Measure tool call round-trip time."""
        with MockMCPClient.over_stdio(SERVER_CMD) as client:
            client.initialize()
            timings = []
            for i in range(50):
                resp = client.call_tool("echo", {"message": f"test-{i}"})
                timings.append(resp.elapsed_ms)

        avg = statistics.mean(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        print(f"\ntool call latency: avg={avg:.1f}ms  p95={p95:.1f}ms  n={len(timings)}")


# ---------------------------------------------------------------------------
# Test: Behavior Under Delay
# ---------------------------------------------------------------------------

class TestDelayBehavior:
    """How does the client handle slow servers?"""

    def test_fixed_delay(self):
        """Server with 100ms fixed delay."""
        cmd = f"{sys.executable} -m harness.mock_server --delay 100"
        with MockMCPClient.over_stdio(cmd) as client:
            client.initialize()
            resp = client.call_tool("echo", {"message": "slow"})
            assert resp.elapsed_ms >= 90, (
                f"Expected ~100ms delay, got {resp.elapsed_ms:.1f}ms"
            )

    def test_high_latency_still_works(self):
        """Server with 500ms delay -- verify responses are still correct."""
        cmd = f"{sys.executable} -m harness.mock_server --delay 500"
        with MockMCPClient.over_stdio(cmd) as client:
            client.initialize()
            resp = client.call_tool("echo", {"message": "patience"})
            assert not resp.is_error
            assert resp.elapsed_ms >= 450


# ---------------------------------------------------------------------------
# Test: Error Rate Resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    """How does communication hold up when the server errors intermittently?"""

    def test_partial_errors(self):
        """Server errors on ~30% of requests."""
        cmd = f"{sys.executable} -m harness.mock_server --error-rate 0.3"
        with MockMCPClient.over_stdio(cmd) as client:
            client.initialize()
            successes = 0
            errors = 0
            for i in range(100):
                resp = client.call_tool("echo", {"message": f"test-{i}"})
                if resp and not resp.is_error:
                    successes += 1
                else:
                    errors += 1

            error_rate = errors / (successes + errors)
            print(f"\nError rate: {error_rate:.1%} ({errors}/{successes + errors})")
            # Should be roughly 30% errors
            assert 0.15 < error_rate < 0.50, (
                f"Expected ~30% error rate, got {error_rate:.1%}"
            )


# ---------------------------------------------------------------------------
# Test: Reporting Integration
# ---------------------------------------------------------------------------

class TestReportGeneration:
    """Verify the test reporter works correctly with transport data."""

    def test_latency_report(self):
        """Generate a latency report from real measurements."""
        reporter = TestReporter(suite_name="transport-profile")

        with MockMCPClient.over_stdio(SERVER_CMD) as client:
            client.initialize()
            for i in range(20):
                resp = client.call_tool("echo", {"message": f"bench-{i}"})
                reporter.add_latency("echo_tool", resp.elapsed_ms)

        reporter.add_finding(Finding(
            title="Baseline latency measured",
            description=f"Echo tool: avg {statistics.mean(client.response_times_ms[1:]):.1f}ms",
            severity=Severity.INFO,
            category="transport",
        ))

        summary = reporter.summary()
        assert "echo_tool" in summary
        assert "Baseline latency" in summary
        print(f"\n{summary}")
