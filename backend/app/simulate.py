from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.action_rules import _phrase_matches, evaluate_action


def _load_fixture(service: str, name: str) -> dict[str, Any] | None:
    base = Path(__file__).parent.parent / "fixtures" / service
    file = base / f"{name}.json"
    if file.exists():
        return json.loads(file.read_text())
    return None


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _service_rule_id(service: str) -> str:
    return {
        "cart-service": "cart_redis_recovery",
        "notification-service": "notification_backlog_recovery",
        "payment-service": "payment_psp_failover",
    }.get(service, f"{service}_default")


def _apply_benefit(post: dict[str, Any], service: str, matched_operations: list[str]) -> str:
    """Apply a service-aware positive metric transform driven by the matched rule operations."""
    action_lower = " ".join(matched_operations).lower()
    queue_depth = float(post.get("queue_depth", 0.0))
    errors = float(post.get("errors", 0.0))
    cpu = float(post.get("cpu", 0.0))
    memory = float(post.get("memory", 0.0))

    if service == "notification-service" or (queue_depth > 0 and "requeue" in action_lower):
        if "requeue" in action_lower or "scale" in action_lower:
            post["queue_depth"] = max(0.0, queue_depth * 0.2)
            post["cpu"] = _clamp(cpu + 0.15)
            post["memory"] = _clamp(memory + 0.05)
            post["errors"] = _clamp(errors * 0.5)
            return "scaling workers and requeueing drains the backlog without dropping messages"

    if service in ("cart-service", "payment-service"):
        if "scale redis" in action_lower or "scale" in action_lower:
            post["errors"] = _clamp(errors * 0.2)
            post["cpu"] = _clamp(cpu + 0.1)
            return "scaling Redis/workers addresses checkout latency and drops error rate"
        if "restart" in action_lower and "worker" in action_lower:
            post["errors"] = _clamp(errors * 0.3)
            post["cpu"] = _clamp(cpu + 0.1)
            return "restarting healthy workers addresses transient pod issues"
        if "verify" in action_lower or "failover" in action_lower or "switch" in action_lower:
            post["errors"] = _clamp(errors * 0.2)
            post["latency_p99"] = max(0, float(post.get("latency_p99", 0)) * 0.5)
            return "verifying connectivity and failing over to the backup PSP restores payment flow"
        if "rollback" in action_lower:
            post["cpu"] = _clamp(cpu * 0.6)
            post["errors"] = _clamp(errors * 0.3)
            return "rolling back to the stable version resolves the deployment-induced regression"

    # Generic positive: scaling-like actions are assumed modestly beneficial.
    if any(op in action_lower for op in ("scale", "requeue", "add", "increase")):
        post["errors"] = _clamp(errors * 0.8)
        post["cpu"] = _clamp(cpu + 0.05)
        return "scaling-like action modestly reduces errors"

    post["errors"] = _clamp(errors * 0.9)
    return "action appears safe but has limited modeled effect on metrics"


def _apply_harm(post: dict[str, Any], forbidden_matches: list[str]) -> str:
    """Harmful sub-actions override otherwise positive compound actions."""
    errors = float(post.get("errors", 0.0))
    queue_depth = float(post.get("queue_depth", 0.0))
    action_lower = " ".join(forbidden_matches).lower()

    if "database" in action_lower and "restart" in action_lower:
        post["errors"] = _clamp(errors + 0.2)
        post["queue_depth"] = queue_depth * 1.5
        return "restarting the database during an incident causes more downtime"

    if any(term in action_lower for term in ["delete", "refund", "drop", "wipe"]):
        post["errors"] = _clamp(errors + 0.3)
        post["queue_depth"] = max(0.0, queue_depth - queue_depth)
        return "action involves destructive data loss and severely worsens reliability"

    post["errors"] = _clamp(errors + 0.1)
    return "action contains a forbidden operation and is predicted to worsen reliability"


def _health_score(metrics: dict[str, Any]) -> float:
    """Higher is better. Penalize errors, resource pressure, and queue depth."""
    errors = float(metrics.get("errors", 0.0))
    cpu = float(metrics.get("cpu", 0.0))
    memory = float(metrics.get("memory", 0.0))
    queue_depth = float(metrics.get("queue_depth", 0.0))
    return 1.0 - errors - 0.3 * cpu - 0.2 * memory - 0.0002 * queue_depth


def simulate_action(service: str, action: str, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Predict the effect of a proposed remediation action on service metrics.

    The simulator is a predictive screen, not execution validation. It uses the
    shared action-rule evaluator to determine whether the action is safe, then
    applies a service-specific metric transform. Harmful sub-actions override
    otherwise positive phrases.
    """
    if metrics is None:
        fixture = _load_fixture(service, "metrics")
        metrics = fixture or {"cpu": 0.5, "memory": 0.5, "errors": 0.05}

    post = dict(metrics)
    action_lower = action.lower()
    reasoning_parts: list[str] = []
    improved: bool | None = None

    # Broad destructive keywords are always forbidden, even if the action rule does not list them.
    broad_forbidden = ["delete", "refund", "drop", "wipe"]
    if any(_phrase_matches(action, term) for term in broad_forbidden):
        result = evaluate_action(action, {"required": [], "forbidden": broad_forbidden, "allowed_supplemental": []})
        reasoning_parts.append(_apply_harm(post, result["forbidden_matches"]))
        improved = False
    else:
        rule_id = _service_rule_id(service)
        result = evaluate_action(action, rule_id)

        if result["forbidden_matches"]:
            reasoning_parts.append(_apply_harm(post, result["forbidden_matches"]))
            improved = False
        elif result["safe"]:
            reasoning_parts.append(_apply_benefit(post, service, result["matched_operations"]))
            improved = True
        else:
            # An unrecognized or incomplete action is treated as not improving.
            reasoning_parts.append(f"action did not match the expected recovery pattern: {', '.join(result['reason_codes'])}")
            improved = False

    before_score = _health_score(metrics)
    after_score = _health_score(post)
    delta = after_score - before_score

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
