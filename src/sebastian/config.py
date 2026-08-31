from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .constants import DEFAULT_MESSAGES_DB, sebastian_home

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "model": "gpt-5.6-sol",
    "reasoning": {"effort": "medium"},
    "messages_db": str(DEFAULT_MESSAGES_DB),
    "poll_seconds": 5,
    "max_response_chars": 1200,
    "max_context_messages": 20,
    "max_triggers_per_conversation_per_minute": 3,
    "global_daily_api_call_limit": 100,
    "state_retention_days": 7,
    "retry": {"initial_seconds": 15, "maximum_seconds": 900, "send_reconcile_seconds": 45},
    "allowlist": {"enabled": False, "conversation_guids": [], "participants": []},
    "images": {
        "enabled": True,
        "max_images": 4,
        "max_image_bytes": 10_485_760,
        "max_total_bytes": 20_971_520,
        "detail": "auto",
    },
    "web_search": {"enabled": True, "search_context_size": "low"},
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_path() -> Path:
    return Path(os.environ.get("SEBASTIAN_CONFIG", str(sebastian_home() / "config.json"))).expanduser()


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_path()
    override: dict[str, Any] = {}
    if target.exists():
        override = json.loads(target.read_text(encoding="utf-8"))
    config = deep_merge(DEFAULTS, override)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    numeric_bounds = {
        "poll_seconds": (1, 300),
        "max_response_chars": (len("— Sebastian, Chris’s AI assistant") + 2, 10000),
        "max_context_messages": (0, 20),
        "max_triggers_per_conversation_per_minute": (1, 60),
        "global_daily_api_call_limit": (1, 10000),
        "state_retention_days": (1, 30),
    }
    for key, (minimum, maximum) in numeric_bounds.items():
        value = config.get(key)
        if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid {key}: expected {minimum}..{maximum}.")
    if not isinstance(config.get("enabled"), bool):
        raise ValueError("enabled must be true or false.")
    reasoning = config.get("reasoning", {})
    if reasoning.get("effort") not in {
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }:
        raise ValueError("reasoning.effort must be none, low, medium, high, xhigh, or max.")
    allowlist = config.get("allowlist", {})
    if not isinstance(allowlist.get("enabled"), bool):
        raise ValueError("allowlist.enabled must be true or false.")
    for key in ("conversation_guids", "participants"):
        if not isinstance(allowlist.get(key), list) or not all(
            isinstance(item, str) and item for item in allowlist[key]
        ):
            raise ValueError(f"allowlist.{key} must be a list of non-empty strings.")
    images = config.get("images", {})
    if not isinstance(images.get("enabled"), bool):
        raise ValueError("images.enabled must be true or false.")
    image_bounds = {
        "max_images": (0, 8),
        "max_image_bytes": (1_024, 20_971_520),
        "max_total_bytes": (1_024, 52_428_800),
    }
    for key, (minimum, maximum) in image_bounds.items():
        value = images.get(key)
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid images.{key}: expected {minimum}..{maximum}.")
    if images.get("detail") not in {"low", "high", "auto"}:
        raise ValueError("images.detail must be low, high, or auto.")


def write_config(config: dict[str, Any], path: Path | None = None) -> None:
    validate_config(config)
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(target)
