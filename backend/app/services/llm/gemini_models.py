"""Descoperire dinamică a modelelor Gemini disponibile + alegere înlocuitor.

Google retrage periodic modelele (în special cele `-preview`), iar un model
retras întoarce `404 NOT_FOUND` la `generateContent`. Ca să nu mai depindem de
o listă hardcodată, citim lista reală prin ListModels (cu cheia configurată)
și, când modelul configurat dispare, alegem automat cel mai apropiat model
disponibil din aceeași familie (pro / flash / flash-lite).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from google import genai

from app.core.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600  # 1 oră

# Modele care nu sunt pentru generare de text (sau sunt variante speciale).
_EXCLUDED_KEYWORDS = (
    "embedding", "tts", "image", "live", "audio", "native",
    "robotics", "computer-use", "aqa", "learnlm", "veo", "imagen",
)


@dataclass(frozen=True)
class GeminiModelInfo:
    id: str
    input_tokens: int
    output_tokens: int


_cache: dict[str, tuple[float, list[GeminiModelInfo]]] = {}
# model retras -> înlocuitor ales (evită un 404 la fiecare instanță nouă)
_replacements: dict[str, str] = {}


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _is_text_model(model_id: str) -> bool:
    return model_id.startswith("gemini-") and not any(
        k in model_id for k in _EXCLUDED_KEYWORDS
    )


async def list_gemini_models(api_key: str, force: bool = False) -> list[GeminiModelInfo]:
    """Lista modelelor Gemini de text disponibile pentru cheia dată (cache 1h).

    Întoarce listă goală dacă API-ul nu răspunde — apelantul folosește atunci
    lista statică.
    """
    key = _key_hash(api_key)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and not force and now - cached[0] < CACHE_TTL:
        return cached[1]

    try:
        client = genai.Client(api_key=api_key)
        models: list[GeminiModelInfo] = []
        async for m in await client.aio.models.list(config={"page_size": 200}):
            name = (m.name or "").removeprefix("models/")
            actions = m.supported_actions or []
            if "generateContent" not in actions or not _is_text_model(name):
                continue
            models.append(GeminiModelInfo(
                id=name,
                input_tokens=m.input_token_limit or 0,
                output_tokens=m.output_token_limit or 0,
            ))
        if models:
            _cache[key] = (now, models)
            logger.info("gemini_models_fetched", count=len(models))
        return models
    except Exception as e:  # noqa: BLE001 — rețea/cheie invalidă: fallback la lista statică
        logger.warning("gemini_models_fetch_failed", error=str(e))
        return cached[1] if cached else []


def _tier(model_id: str) -> str:
    if "flash-lite" in model_id:
        return "flash-lite"
    if "flash" in model_id:
        return "flash"
    if "pro" in model_id:
        return "pro"
    return "other"


def _version(model_id: str) -> tuple[int, ...]:
    m = re.match(r"gemini-(\d+(?:\.\d+)*)", model_id)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def _rank(model_id: str) -> tuple:
    """Cheie de sortare: versiune mare, stabil înaintea preview/exp, fără sufixe."""
    unstable = any(k in model_id for k in ("preview", "exp"))
    alias = model_id.endswith("-latest")
    variant = "customtools" in model_id
    return (_version(model_id), not unstable, not variant, not alias)


def pick_replacement(missing: str, available: list[str]) -> str | None:
    """Alege cel mai bun model disponibil pentru un model retras.

    Preferă aceeași familie (pro/flash/flash-lite), apoi pro, apoi flash;
    în cadrul familiei: un model stabil de aceeași generație sau mai nouă,
    altfel versiunea cea mai nouă.
    """
    candidates = [m for m in available if m != missing and _is_text_model(m)]
    if not candidates:
        return None
    base_version = _version(missing)

    def key(m: str) -> tuple:
        # Un model stabil de aceeași generație sau mai nouă bate un preview
        # (preview-urile sunt cele retrase periodic); altfel, cel mai nou.
        stable = not any(k in m for k in ("preview", "exp"))
        return (stable and _version(m) >= base_version, _rank(m))

    for tier in (_tier(missing), "pro", "flash", "flash-lite", "other"):
        same = [m for m in candidates if _tier(m) == tier]
        if same:
            return max(same, key=key)
    return None


def get_known_replacement(model_id: str) -> str | None:
    return _replacements.get(model_id)


async def resolve_replacement(missing: str, api_key: str) -> str | None:
    """Găsește (și memorează) un înlocuitor pentru un model care dă 404."""
    if missing in _replacements:
        return _replacements[missing]
    available = [m.id for m in await list_gemini_models(api_key, force=True)]
    replacement = pick_replacement(missing, available)
    if replacement:
        _replacements[missing] = replacement
        logger.warning(
            "gemini_model_replaced",
            missing=missing,
            replacement=replacement,
        )
    return replacement


def is_model_not_found(error: Exception) -> bool:
    """True dacă eroarea e 404 pentru modelul cerut (model retras/inexistent)."""
    code = getattr(error, "code", None)
    msg = str(error)
    return code == 404 or ("NOT_FOUND" in msg and "models/" in msg)
