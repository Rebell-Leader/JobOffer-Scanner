"""In-process metrics registry — counters + histograms with snapshot dump.

Not a replacement for a real Prometheus / StatsD pipeline; designed for the
"two-Streamlit-containers" deployments where wiring up Prometheus is bigger
than the value, but you still want to know which LLM provider is failing most
and how long the median report-generation takes.

Snapshots can be dumped via ``python -m worker.metrics_dump`` (Phase 18 also
ships that CLI) for ad-hoc inspection, or piped into a log pipeline.

Thread-safe via a single ``RLock``. Histograms keep an exponential decay
window (max 1000 samples per bucket) so memory stays bounded even for the
busiest counter — at the cost of producing approximations rather than exact
quantiles. Good enough for "is the p95 LLM latency above 30s today?"
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Tuple


# How many samples each histogram remembers. Bounded so a hot path doesn't
# eat memory; older samples drop off in FIFO order.
_HISTOGRAM_MAX_SAMPLES = 1000


@dataclass(frozen=True)
class CounterSnapshot:
    name: str
    tags: Tuple[Tuple[str, str], ...]
    value: int


@dataclass(frozen=True)
class HistogramSnapshot:
    name: str
    tags: Tuple[Tuple[str, str], ...]
    count: int
    sum: float
    min: float
    max: float
    p50: float
    p95: float
    p99: float


@dataclass(frozen=True)
class MetricsSnapshot:
    counters: Tuple[CounterSnapshot, ...]
    histograms: Tuple[HistogramSnapshot, ...]


def _tag_key(tags: Optional[Dict[str, str]]) -> Tuple[Tuple[str, str], ...]:
    """Canonicalize a tag dict into a hashable, sort-stable tuple."""
    if not tags:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in tags.items()))


class Registry:
    """Process-local metrics store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = {}
        self._histograms: Dict[
            Tuple[str, Tuple[Tuple[str, str], ...]], Deque[float]
        ] = {}

    # -- Counters ---------------------------------------------------------

    def increment(
        self,
        name: str,
        amount: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        key = (name, _tag_key(tags))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def counter(self, name: str, tags: Optional[Dict[str, str]] = None) -> int:
        key = (name, _tag_key(tags))
        with self._lock:
            return self._counters.get(key, 0)

    # -- Histograms -------------------------------------------------------

    def observe(
        self,
        name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        key = (name, _tag_key(tags))
        with self._lock:
            samples = self._histograms.get(key)
            if samples is None:
                samples = deque(maxlen=_HISTOGRAM_MAX_SAMPLES)
                self._histograms[key] = samples
            samples.append(float(value))

    # -- Snapshot ---------------------------------------------------------

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = tuple(
                CounterSnapshot(name=name, tags=tags, value=value)
                for (name, tags), value in sorted(self._counters.items())
            )
            histograms: List[HistogramSnapshot] = []
            for (name, tags), samples in sorted(self._histograms.items()):
                if not samples:
                    continue
                ordered = sorted(samples)
                count = len(ordered)
                histograms.append(
                    HistogramSnapshot(
                        name=name,
                        tags=tags,
                        count=count,
                        sum=sum(ordered),
                        min=ordered[0],
                        max=ordered[-1],
                        p50=_quantile(ordered, 0.50),
                        p95=_quantile(ordered, 0.95),
                        p99=_quantile(ordered, 0.99),
                    )
                )
            return MetricsSnapshot(counters=counters, histograms=tuple(histograms))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


def _quantile(sorted_values: List[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


# Module-level singleton — same lifecycle as the Python process.
_registry = Registry()


def get_registry() -> Registry:
    return _registry


def reset_for_testing() -> None:
    _registry.reset()


# Convenience aliases.
def increment(name: str, amount: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
    _registry.increment(name, amount, tags)


def observe(name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
    _registry.observe(name, value, tags)


def snapshot() -> MetricsSnapshot:
    return _registry.snapshot()


def render_snapshot_text(snap: Optional[MetricsSnapshot] = None) -> str:
    """Human-readable dump suitable for CLI / admin views."""
    snap = snap or snapshot()
    lines: List[str] = []
    if snap.counters:
        lines.append("# Counters")
        for c in snap.counters:
            tag_part = "{" + ", ".join(f"{k}={v}" for k, v in c.tags) + "}" if c.tags else ""
            lines.append(f"{c.name}{tag_part} = {c.value}")
    if snap.histograms:
        if lines:
            lines.append("")
        lines.append("# Histograms (durations in ms unless tagged otherwise)")
        for h in snap.histograms:
            tag_part = "{" + ", ".join(f"{k}={v}" for k, v in h.tags) + "}" if h.tags else ""
            lines.append(
                f"{h.name}{tag_part} count={h.count} "
                f"min={h.min:.1f} p50={h.p50:.1f} p95={h.p95:.1f} "
                f"p99={h.p99:.1f} max={h.max:.1f}"
            )
    if not lines:
        lines.append("(no metrics recorded yet)")
    return "\n".join(lines)
