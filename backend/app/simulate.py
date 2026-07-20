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
            post["queue_depth"] = max(0.0, queue_depth * 0.05)
            post["cpu"] = _clamp(cpu + 0.15)
            post["memory"] = _clamp(memory + 0.05)
            post["errors"] = _clamp(errors * 0.5)
            return "scaling workers and requeueing drains the backlog without dropping messages"

    if service in ("cart-service", "payment-service"):
        if service == "cart-service" and ("scale redis" in action_lower or "scale" in action_lower):
            post["errors"] = _clamp(errors * 0.2)
            post["cpu"] = _clamp(cpu + 0.1)
            post["checkout_failures"] = max(0, float(post.get("checkout_failures", 0)) * 0.1)
            post["latency_p99"] = max(0, float(post.get("latency_p99", 0)) * 0.5)
            return "scaling Redis and restarting workers addresses checkout latency and drops error rate"
        if service == "cart-service" and "restart" in action_lower and "worker" in action_lower:
            post["errors"] = _clamp(errors * 0.3)
            post["cpu"] = _clamp(cpu + 0.1)
            post["checkout_failures"] = max(0, float(post.get("checkout_failures", 0)) * 0.2)
            post["latency_p99"] = max(0, float(post.get("latency_p99", 0)) * 0.7)
            return "restarting healthy workers addresses transient pod issues"
        if service == "payment-service" and ("verify" in action_lower or "failover" in action_lower or "switch" in action_lower):
            post["errors"] = _clamp(errors * 0.2)
            post["psp_latency_p99"] = max(0, float(post.get("psp_latency_p99", 0)) * 0.2)
            post["payment_timeouts"] = max(0, float(post.get("payment_timeouts", 0)) * 0.2)
            post["psp_available"] = True
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


def _metric_penalty(metrics: dict[str, Any], key: str, threshold: float, weight: float) -> float:
    """Return a weighted [0,1] penalty for ``value/threshold``, capped at 1."""
    value = float(metrics.get(key, 0.0))
    if threshold <= 0 or value <= 0:
        return 0.0
    return min(value / threshold, 1.0) * weight


def _health_score(metrics: dict[str, Any]) -> float:
    """Higher is better. Normalize each metric against a service threshold and clamp to [0, 1]."""
    score = 1.0
    score -= _metric_penalty(metrics, "errors", 0.1, 0.25)
    score -= _metric_penalty(metrics, "cpu", 0.8, 0.15)
    score -= _metric_penalty(metrics, "memory", 0.75, 0.1)
    score -= _metric_penalty(metrics, "queue_depth", 100000, 0.2)
    score -= _metric_penalty(metrics, "psp_latency_p99", 2000, 0.1)
    score -= _metric_penalty(metrics, "payment_timeouts", 20, 0.1)
    score -= _metric_penalty(metrics, "checkout_failures", 30, 0.1)
    score -= _metric_penalty(metrics, "latency_p99", 1500, 0.1)
    if not metrics.get("psp_available", True):
        score -= 0.2
    return _clamp(score)


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
    broad_forbidden_matches = [term for term in broad_forbidden if _phrase_matches(action, term)]
    if broad_forbidden_matches:
        result = evaluate_action(action, {"required": [], "forbidden": broad_forbidden, "allowed_supplemental": []})
        reasoning_parts.append(_apply_harm(post, result["forbidden_matches"]))
        improved = False
    else:
        rule_id = _service_rule_id(service)
        result = evaluate_action(action, rule_id)

        if result["forbidden_matches"]:
            reasoning_parts.append(_apply_harm(post, result["forbidden_matches"]))
            improved = False
        elif result["passed"]:
            # Only a complete, order-correct match to the expected recovery
            # pattern is predicted to improve the incident. Matched operations
            # drive the service-aware metric transform.
            reasoning_parts.append(_apply_benefit(post, service, result["matched_operations"]))
            improved = True
        else:
            # Partial or off-pattern actions are not promoted to simulated_safe.
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
