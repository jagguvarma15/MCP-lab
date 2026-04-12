"""
Test Reporter -- collect, aggregate, and format results from MCP tests.

Designed to produce both human-readable summaries and machine-readable
JSON for tracking results over time.
"""

import json
import time
import statistics
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Finding:
    """A single observation from a test."""
    title: str
    description: str
    severity: Severity
    category: str  # security, transport, conformance, evaluation, integration
    evidence: dict = field(default_factory=dict)
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
        }


@dataclass
class LatencyProfile:
    """Latency statistics from a set of measurements."""
    measurements_ms: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.measurements_ms)

    @property
    def mean(self) -> float:
        return statistics.mean(self.measurements_ms) if self.measurements_ms else 0

    @property
    def median(self) -> float:
        return statistics.median(self.measurements_ms) if self.measurements_ms else 0

    @property
    def p95(self) -> float:
        if len(self.measurements_ms) < 20:
            return max(self.measurements_ms) if self.measurements_ms else 0
        sorted_ms = sorted(self.measurements_ms)
        idx = int(len(sorted_ms) * 0.95)
        return sorted_ms[idx]

    @property
    def p99(self) -> float:
        if len(self.measurements_ms) < 100:
            return max(self.measurements_ms) if self.measurements_ms else 0
        sorted_ms = sorted(self.measurements_ms)
        idx = int(len(sorted_ms) * 0.99)
        return sorted_ms[idx]

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "mean_ms": round(self.mean, 2),
            "median_ms": round(self.median, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
        }


class TestReporter:
    """Aggregates findings and metrics from test runs."""

    def __init__(self, suite_name: str = "mcp-lab"):
        self.suite_name = suite_name
        self.findings: list[Finding] = []
        self.latency_profiles: dict[str, LatencyProfile] = {}
        self.metadata: dict = {
            "suite": suite_name,
            "started_at": time.time(),
        }

    def add_finding(self, finding: Finding):
        self.findings.append(finding)

    def add_latency(self, label: str, ms: float):
        if label not in self.latency_profiles:
            self.latency_profiles[label] = LatencyProfile()
        self.latency_profiles[label].measurements_ms.append(ms)

    # -- Output formats -----------------------------------------------------

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"{'=' * 60}",
            f"  MCP Lab Report: {self.suite_name}",
            f"{'=' * 60}",
            "",
        ]

        # Findings by severity
        for sev in [Severity.CRITICAL, Severity.WARNING, Severity.INFO]:
            matching = [f for f in self.findings if f.severity == sev]
            if matching:
                lines.append(f"  [{sev.value.upper()}] ({len(matching)} findings)")
                for f in matching:
                    lines.append(f"    - {f.title}")
                    lines.append(f"      {f.description}")
                    if f.recommendation:
                        lines.append(f"      -> {f.recommendation}")
                    lines.append("")

        # Latency profiles
        if self.latency_profiles:
            lines.append("  LATENCY PROFILES")
            lines.append(f"  {'Label':<30} {'Mean':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'n':>6}")
            lines.append(f"  {'-' * 70}")
            for label, profile in self.latency_profiles.items():
                lines.append(
                    f"  {label:<30} {profile.mean:>7.1f}ms {profile.median:>7.1f}ms "
                    f"{profile.p95:>7.1f}ms {profile.p99:>7.1f}ms {profile.count:>6}"
                )
            lines.append("")

        lines.append(f"{'=' * 60}")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Machine-readable JSON report."""
        report = {
            **self.metadata,
            "completed_at": time.time(),
            "findings": [f.to_dict() for f in self.findings],
            "latency": {k: v.to_dict() for k, v in self.latency_profiles.items()},
            "summary": {
                "total_findings": len(self.findings),
                "critical": len([f for f in self.findings if f.severity == Severity.CRITICAL]),
                "warnings": len([f for f in self.findings if f.severity == Severity.WARNING]),
                "info": len([f for f in self.findings if f.severity == Severity.INFO]),
            },
        }
        return json.dumps(report, indent=2)

    def save(self, path: str):
        Path(path).write_text(self.to_json())
