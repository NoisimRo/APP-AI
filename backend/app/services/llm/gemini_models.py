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
FAILURE_TTL = 300  # după un eșec ListModels, nu reîncerca la fiecare cerere

# Modele vechi despre care știm sigur că NU au thinking. Orice model necunoscut
# (generații viitoare) e tratat ca model cu thinking — varianta sigură.
_LEGACY_NON_THINKING_PREFIXES = ("gemini-1.", "gemini-2.0")

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
    thinking: bool | None = None


_cache: dict[str, tuple[float, list[GeminiModelInfo]]] = {}
_failures: dict[str, float] = {}
# metadate per model, indiferent de cheie (limite, thinking)
_info: dict[str, GeminiModelInfo] = {}
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
    if not force and now - _failures.get(key, -FAILURE_TTL) < FAILURE_TTL:
        return cached[1] if cached else []

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
                thinking=getattr(m, "thinking", None),
            ))
        if models:
            _cache[key] = (now, models)
            _info.update({m.id: m for m in models})
            _failures.pop(key, None)
            logger.info("gemini_models_fetched", count=len(models))
        return models
    except Exception as e:  # noqa: BLE001 — rețea/cheie invalidă: fallback la lista statică
        _failures[key] = now
        logger.warning("gemini_models_fetch_failed", error=str(e))
        return cached[1] if cached else []


def get_model_info(model_id: str) -> GeminiModelInfo | None:
    """Metadatele raportate de Google pentru model (dacă lista a fost încărcată)."""
    return _info.get(model_id)


def is_thinking_model(model_id: str) -> bool:
    """Thinking după metadatele Google; necunoscut => True (sigur pt. modele noi)."""
    info = _info.get(model_id)
    if info and info.thinking is not None:
        return info.thinking
    return not model_id.startswith(_LEGACY_NON_THINKING_PREFIXES)


def _family(model_id: str) -> str:
    """Familia modelului, independent de generație și de sufixe.

    gemini-3.1-pro-preview -> "pro", gemini-2.5-flash-lite -> "flash-lite",
    gemini-4-argon -> "argon", gemini-3.1-pro-preview-customtools -> "pro-customtools".
    Așa funcționează și pentru denumiri noi, fără modificări de cod.
    """
    fam = re.sub(r"^gemini-\d+(?:\.\d+)*-?", "", model_id)
    fam = re.sub(r"-(preview|exp)\b", "", fam)     # stadiul nu schimbă familia
    fam = re.sub(r"(-\d+)+$", "", fam)             # sufixe dată/versiune: -09-2025, -001
    fam = re.sub(r"-latest$", "", fam)
    return fam.strip("-") or "base"


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

    Preferă aceeași familie (pro, flash, argon, ...), apoi pro, apoi flash;
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

    # 1) aceeași familie (pro→pro, argon→argon); 2) aceeași generație sau mai
    # nouă; 3) familiile clasice; 4) orice model de text.
    same = [m for m in candidates if _family(m) == _family(missing)]
    if same:
        return max(same, key=key)
    # Familie dispărută complet: rămâi pe aceeași generație sau mai nouă.
    newer = [m for m in candidates if _version(m) >= base_version]
    if newer:
        return max(newer, key=key)
    for family in ("pro", "flash", "flash-lite"):
        same = [m for m in candidates if _family(m) == family]
        if same:
            return max(same, key=key)
    return max(candidates, key=key)


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
