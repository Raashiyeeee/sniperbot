"""
analytics/latency_tracker.py
────────────────────────────
Latency instrumentation and metrics tracking (Spec v3.1 §10.2).
Measures:
  - Event receive -> parsing -> source verification -> checks -> quote -> submit -> fill
  - Reports p50, p95, and p99 percentiles against the 2-second target.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LatencyBreakdown:
    project_id: str
    event_received_at: float
    ca_extracted_at: Optional[float] = None
    source_verified_at: Optional[float] = None
    checks_completed_at: Optional[float] = None
    quote_received_at: Optional[float] = None
    tx_submitted_at: Optional[float] = None
    tx_confirmed_at: Optional[float] = None

    @property
    def total_submission_ms(self) -> Optional[float]:
        """Latency from event reception to transaction submission."""
        if self.tx_submitted_at:
            return (self.tx_submitted_at - self.event_received_at) * 1000
        return None

    @property
    def total_fill_ms(self) -> Optional[float]:
        """Latency from event reception to on-chain confirmation."""
        if self.tx_confirmed_at:
            return (self.tx_confirmed_at - self.event_received_at) * 1000
        return None


class LatencyTracker:
    """Tracks latency metrics across buy executions."""

    def __init__(self) -> None:
        self.records: List[LatencyBreakdown] = []

    def record(self, breakdown: LatencyBreakdown) -> None:
        self.records.append(breakdown)
        ms = breakdown.total_submission_ms
        if ms is not None:
            meets_target = ms <= 2000.0  # 2-second target
            logger.info(
                "latency_submission_recorded",
                project_id=breakdown.project_id,
                latency_ms=f"{ms:.1f}ms",
                target_met=meets_target,
            )

    def get_summary_stats(self) -> Dict[str, float]:
        """Calculate p50, p95, and p99 latency for transaction submission."""
        submission_times = [
            r.total_submission_ms for r in self.records if r.total_submission_ms is not None
        ]
        if not submission_times:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "target_met_pct": 0.0}

        sorted_times = sorted(submission_times)
        n = len(sorted_times)

        p50 = statistics.median(sorted_times)
        p95 = sorted_times[int(0.95 * n) if n > 1 else 0]
        p99 = sorted_times[int(0.99 * n) if n > 1 else 0]

        target_met_count = sum(1 for t in sorted_times if t <= 2000.0)
        pct = (target_met_count / n) * 100.0

        return {
            "count": n,
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "target_met_pct": round(pct, 2),
        }
