"""Descoperire dinamică a modelelor Claude (Models API) + alegere înlocuitor.

Ca la Gemini: lista din Setări, limitele de tokeni și înlocuirea unui model
retras vin din API-ul Anthropic, nu din cod, ca modelele noi să apară automat.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import anthropic

from app.core.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600
FAILURE_TTL = 300


@dataclass(frozen=True)
class ClaudeModelInfo:
    id: str
    input_tokens: int
    output_tokens: int


_cache: dict[str, tuple[float, list[ClaudeModelInfo]]] = {}
_failures: dict[str, float] = {}
_info: dict[str, ClaudeModelInfo] = {}
_replacements: dict[str, str] = {}


async def list_claude_models(
    client: anthropic.AsyncAnthropic,
    cache_key: str = "default",
    force: bool = False,
) -> list[ClaudeModelInfo]:
    """Modelele Claude disponibile pentru cheia clientului (cache 1h).

    Listă goală dacă API-ul nu răspunde — apelantul folosește lista statică.
    """
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and not force and now - cached[0] < CACHE_TTL:
        return cached[1]
    if not force and now - _failures.get(cache_key, -FAILURE_TTL) < FAILURE_TTL:
        return cached[1] if cached else []

    try:
        models: list[ClaudeModelInfo] = []
        async for m in client.models.list(limit=100):
            if not m.id.startswith("claude-"):
                continue
            models.append(ClaudeModelInfo(
                id=m.id,
                input_tokens=getattr(m, "max_input_tokens", None) or 0,
                output_tokens=getattr(m, "max_tokens", None) or 0,
            ))
        if models:
            _cache[cache_key] = (now, models)
            _info.update({m.id: m for m in models})
            _failures.pop(cache_key, None)
            logger.info("claude_models_fetched", count=len(models))
        return models
    except Exception as e:  # noqa: BLE001
        _failures[cache_key] = now
        logger.warning("claude_models_fetch_failed", error=str(e))
        return cached[1] if cached else []


def get_model_info(model_id: str) -> ClaudeModelInfo | None:
    return _info.get(model_id)


def model_family(model_id: str) -> str:
    """claude-opus-4-6 -> "opus", claude-fable-5-2 -> "fable"."""
    m = re.match(r"claude-([a-z]+)", model_id)
    return m.group(1) if m else ""


def model_version(model_id: str) -> tuple[int, ...]:
    """claude-opus-4-6 -> (4, 6), claude-opus-5 -> (5,), date suffix ignored."""
    parts = re.findall(r"-(\d{1,2})(?=-|$)", model_id)
    return tuple(int(p) for p in parts) or (0,)


def pick_replacement(missing: str, available: list[str]) -> str | None:
    """Cel mai nou model din aceeași familie (opus→opus); altfel cel mai nou model."""
    candidates = [m for m in available if m != missing and m.startswith("claude-")]
    if not candidates:
        return None
    same = [m for m in candidates if model_family(m) == model_family(missing)]
    return max(same or candidates, key=model_version)


def get_known_replacement(model_id: str) -> str | None:
    return _replacements.get(model_id)


async def resolve_replacement(
    missing: str, client: anthropic.AsyncAnthropic, cache_key: str = "default"
) -> str | None:
    if missing in _replacements:
        return _replacements[missing]
    available = [m.id for m in await list_claude_models(client, cache_key, force=True)]
    replacement = pick_replacement(missing, available)
    if replacement:
        _replacements[missing] = replacement
        logger.warning("claude_model_replaced", missing=missing, replacement=replacement)
    return replacement
