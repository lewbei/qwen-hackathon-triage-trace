from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_fixture(service: str, name: str) -> dict[str, Any] | None:
    base = Path(__file__).parent.parent / "fixtures" / service
    file = base / f"{name}.json"
    if file.exists():
        return json.loads(file.read_text())
    return None


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _contains(text: str, *keywords: str) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


def _health_score(metrics: dict[str, Any]) -> float:
    """Higher is better. Penalize errors, resource pressure, and queue depth."""
    errors = float(metrics.get("errors", 0.0))
    cpu = float(metrics.get("cpu", 0.0))
    memory = float(metrics.get("memory", 0.0))
    queue_depth = float(metrics.get("queue_depth", 0.0))
    return 1.0 - errors - 0.3 * cpu - 0.2 * memory - 0.0002 * queue_depth


def simulate_action(service: str, action: str, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Simulate the effect of a proposed remediation action on service metrics.

    The simulation is keyword-driven and deterministic. It returns the expected
    post-action metrics, a health score delta, and a human-readable verdict.
    """
    if metrics is None:
        fixture = _load_fixture(service, "metrics")
        metrics = fixture or {"cpu": 0.5, "memory": 0.5, "errors": 0.05}

    post = dict(metrics)
    action_lower = action.lower()
    reasoning_parts: list[str] = []
    improved: bool | None = None

    # Normalize numeric fields.
    queue_depth = float(post.get("queue_depth", 0.0))
    errors = float(post.get("errors", 0.0))
    cpu = float(post.get("cpu", 0.0))
    memory = float(post.get("memory", 0.0))

    # High-confidence harmful keywords override everything.
    if _contains(action_lower, "delete", "refund", "drop", "wipe"):
        post["errors"] = _clamp(errors + 0.3)
        post["queue_depth"] = max(0.0, queue_depth - queue_depth)  # data loss "clears" queue
        reasoning_parts.append("action involves destructive data loss, which severely worsens reliability")
        improved = False

    # notification-service / queue backlog patterns.
    elif service == "notification-service" or queue_depth > 0:
        if _contains(action_lower, "scale", "requeue", "worker", "horizontal"):
            post["queue_depth"] = max(0.0, queue_depth * 0.2)
            post["cpu"] = _clamp(cpu + 0.15)
            post["memory"] = _clamp(memory + 0.05)
            reasoning_parts.append("scaling workers and requeueing drains the backlog without dropping messages")
            improved = True
        elif _contains(action_lower, "restart"):
            post["queue_depth"] = queue_depth * 1.3
            post["errors"] = _clamp(errors + 0.05)
            reasoning_parts.append("restarting pods during a backlog spike temporarily reduces throughput and worsens the queue")
            improved = False
        else:
            post["queue_depth"] = queue_depth * 0.9
            reasoning_parts.append("action has limited effect on queue backlog")
            improved = queue_depth * 0.9 < queue_depth

    # cart-service / payment-service checkout patterns.
    elif service in ("cart-service", "payment-service"):
        if ("scale redis" in action_lower or "scale cache" in action_lower
                or "restart cart workers" in action_lower or "restart payment workers" in action_lower
                or "scale workers" in action_lower):
            post["errors"] = _clamp(errors * 0.2)
            post["cpu"] = _clamp(cpu + 0.1)
            reasoning_parts.append("scaling Redis/workers addresses checkout latency and drops error rate")
            improved = True
        elif "restart" in action_lower and "database" in action_lower:
            post["errors"] = _clamp(errors + 0.2)
            post["cpu"] = _clamp(cpu + 0.2)
            reasoning_parts.append("restarting the database during checkout failures causes more downtime")
            improved = False
        elif "restart" in action_lower and ("worker" in action_lower or "workers" in action_lower):
            post["errors"] = _clamp(errors * 0.3)
            post["cpu"] = _clamp(cpu + 0.1)
            reasoning_parts.append("restarting healthy workers addresses transient pod issues")
            improved = True
        elif "restart" in action_lower:
            post["errors"] = _clamp(errors * 0.9)
            post["cpu"] = _clamp(cpu + 0.05)
            reasoning_parts.append("service restart may temporarily relieve pressure but does not fix root cause")
            improved = True
        else:
            reasoning_parts.append("action has unclear effect on checkout failures")
            improved = False

    else:
        # Generic fallback: conservative, assumes no improvement unless explicitly scaling.
        if _contains(action_lower, "scale", "requeue", "add"):
            post["errors"] = _clamp(errors * 0.8)
            post["cpu"] = _clamp(cpu + 0.05)
            reasoning_parts.append("scaling-like action modestly reduces errors")
            improved = True
        elif _contains(action_lower, "restart"):
            post["errors"] = _clamp(errors + 0.05)
            reasoning_parts.append("restart risks transient downtime")
            improved = False
        else:
            reasoning_parts.append("cannot determine improvement from action keywords")
            improved = False

    before_score = _health_score(metrics)
    after_score = _health_score(post)
    delta = after_score - before_score

    # If we didn't explicitly set improved, derive it from score delta.
    if improved is None:
        improved = delta > 0

    return {
        "service": service,
        "action": action,
        "before_metrics": metrics,
        "after_metrics": post,
        "before_score": round(before_score, 4),
        "after_score": round(after_score, 4),
        "delta": round(delta, 4),
        "improved": improved,
        "reasoning": "; ".join(reasoning_parts) or "simulated outcome unknown",
    }
