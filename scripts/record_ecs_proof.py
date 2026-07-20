#!/usr/bin/env python3
"""Record live ECS proof for all three demo scenarios."""
import http.cookiejar
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://47.251.179.138"
SCENARIOS = [
    {
        "id": "cart-redis-latency",
        "service": "cart-service",
        "symptom": "High checkout failure rate and slow response times",
        "context": "Redis latency spiked and checkout failures exceeded 40 per minute.",
        "expected_action_contains": "Scale the Redis cache and restart the cart workers",
    },
    {
        "id": "notifications-queue-backlog",
        "service": "notification-service",
        "symptom": "Notification queue backlog above 400,000 messages",
        "context": "Queue depth is over 400,000 messages after an upstream outage and error rate is climbing.",
        "expected_action_contains": "Scale the notification workers and requeue failed messages",
    },
    {
        "id": "payments-psp-failure",
        "service": "payment-service",
        "symptom": "Payment timeouts and PSP unavailability",
        "context": "Primary PSP latency p99 is 4200ms, timeouts are 31 per minute, and psp_available is false.",
        "expected_action_contains": "backup PSP",
    },
]


def post_json(path: str, body: dict, cookie_jar: http.cookiejar.CookieJar) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    with opener.open(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(path: str, cookie_jar: http.cookiejar.CookieJar) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    with opener.open(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    results = []
    for i, sc in enumerate(SCENARIOS, 1):
        if i == 3:
            # The public demo permits five writes per minute. The first two
            # scenarios use four writes, so wait for the window instead of
            # spoofing forwarding headers to evade the limiter.
            print("[*] Waiting for the public demo rate-limit window…")
            time.sleep(61)
        cj = http.cookiejar.CookieJar()
        print(f"[*] Scenario {i}/3: {sc['id']}")

        setup = post_json(f"/api/demo/setup/{sc['id']}", {}, cj)
        tenant = setup["alert"]["tenant"]

        # Verify the browser-style memory list returns the seeded tenant memories.
        memories = get_json(f"/api/memories?tenant={tenant}", cj)
        active = [m for m in memories if m["status"] in ("active", "simulated_safe")]
        quarantined = [m for m in memories if m["status"] == "quarantined"]
        superseded = [m for m in memories if m["status"] == "superseded"]

        alert = {
            "tenant": tenant,
            "service": sc["service"],
            "symptom": sc["symptom"],
            "context": sc["context"],
            "severity": "critical",
        }
        run = post_json("/api/agent/runs?mode=memory", alert, cj)
        action = run.get("proposal", {}).get("action", "")
        recalled = run.get("proposal", {}).get("recalled_memory_ids", [])
        action_matches = sc["expected_action_contains"].lower() in action.lower()
        lifecycle_matches = bool(active and quarantined and superseded)
        run_ok = run.get("status") not in {"error", "invalid"}
        scenario_passed = action_matches and lifecycle_matches and bool(recalled) and run_ok

        results.append({
            "scenario_id": sc["id"],
            "tenant": tenant,
            "memory_counts": {
                "active_or_simulated_safe": len(active),
                "quarantined": len(quarantined),
                "superseded": len(superseded),
            },
            "proposed_action": action,
            "recalled_memory_ids": recalled,
            "expected_action_contains": sc["expected_action_contains"],
            "action_matches_expectation": action_matches,
            "lifecycle_matches_expectation": lifecycle_matches,
            "recalled_memory": bool(recalled),
            "scenario_passed": scenario_passed,
            "status": run.get("status"),
        })
        print(f"    action: {action}")
        print(f"    recalled: {recalled}")
        time.sleep(2)

    out = Path(__file__).parent.parent / "docs" / "ECS_PROOF.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "url": BASE,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scenarios": results,
        }, f, indent=2)
    print(f"\n[+] Proof written to {out}")
    for r in results:
        print(f"{r['scenario_id']}: {'PASS' if r['scenario_passed'] else 'REVIEW'} — {r['proposed_action']}")


if __name__ == "__main__":
    main()
