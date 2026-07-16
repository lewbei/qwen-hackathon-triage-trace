from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load(service: str, name: str) -> dict[str, Any] | None:
    path = FIXTURES_DIR / service / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def inspect_metrics(service: str, time_window: str = "1h") -> dict[str, Any]:
    data = _load(service, "metrics") or {"cpu": 0.45, "memory": 0.62, "errors": 0.01}
    return {"service": service, "time_window": time_window, **data}


def list_recent_deployments(service: str) -> dict[str, Any]:
    data = _load(service, "deployments") or {"last_deployments": []}
    return {"service": service, **data}


def read_current_runbook(service: str) -> dict[str, Any]:
    data = _load(service, "runbook") or {"runbook": "No runbook on file."}
    return {"service": service, **data}


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "inspect_metrics",
            "description": "Read metrics for a service over a time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "time_window": {"type": "string", "default": "1h"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_deployments",
            "description": "List recent deployments for a service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_current_runbook",
            "description": "Read the current runbook for a service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                },
                "required": ["service"],
            },
        },
    },
]


def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "inspect_metrics":
        return inspect_metrics(**arguments)
    if name == "list_recent_deployments":
        return list_recent_deployments(**arguments)
    if name == "read_current_runbook":
        return read_current_runbook(**arguments)
    raise ValueError(f"Unknown tool: {name}")
