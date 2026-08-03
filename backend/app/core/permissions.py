"""Feature access control — single source of truth for role permissions.

Before this module the same rules lived in three places that had already
drifted apart: ``FEATURE_ROLES`` in ``app.core.deps`` (what the API enforced),
``ROLE_FEATURES`` in ``index.tsx`` (what the sidebar showed), and the plan
table in the docs. Permissions are now stored in the ``role_permissions``
table, enforced here, and rendered by the frontend from what the server says.

Guardrails that cannot be edited away from the admin UI:

* ``admin`` always holds every feature — an admin cannot lock themselves out.
* Features flagged ``admin_only`` (settings, permissions, users) can never be
  granted to another role, whatever the request body says.
* Features flagged ``always_on`` (profile, pricing) stay available to every
  authenticated role, so nobody gets trapped in the app with no way out.

The table is read through a short-lived process-local cache. On Cloud Run each
instance keeps its own copy, so a change made on one instance is picked up by
the others within ``CACHE_TTL`` seconds.
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# Pseudo-role for visitors without an account.
ANONYMOUS_ROLE = "anonymous"

ALL_ROLES = [
    ANONYMOUS_ROLE,
    "registered",
    "paid_basic",
    "paid_pro",
    "paid_enterprise",
    "admin",
]

ROLE_LABELS = {
    ANONYMOUS_ROLE: "Vizitator (neautentificat)",
    "registered": "Free (cont gratuit)",
    "paid_basic": "Basic",
    "paid_pro": "Pro",
    "paid_enterprise": "Enterprise",
    "admin": "Administrator",
}


class Feature:
    """A gateable page or capability.

    Attributes:
        key: Stable identifier used by the API and the frontend router.
        label: Romanian label shown in the admin matrix.
        category: Grouping used by the sidebar and the admin matrix.
        description: One-line explanation shown under the label.
        admin_only: Never grantable to a role other than ``admin``.
        always_on: Always granted to every authenticated role.
        ui_only: No backend endpoint enforces it — it only hides navigation.
    """

    __slots__ = ("key", "label", "category", "description",
                 "admin_only", "always_on", "ui_only")

    def __init__(
        self,
        key: str,
        label: str,
        category: str,
        description: str = "",
        admin_only: bool = False,
        always_on: bool = False,
        ui_only: bool = False,
    ):
        self.key = key
        self.label = label
        self.category = category
        self.description = description
        self.admin_only = admin_only
        self.always_on = always_on
        self.ui_only = ui_only


# Order here drives the order of the admin matrix.
FEATURE_CATALOG: list[Feature] = [
    # --- Workspace ---
    Feature("chat", "Asistent AP", "Workspace",
            "Chat-ul principal cu RAG pe deciziile CNSC"),
    Feature("datalake", "Decizii CNSC", "Workspace",
            "Data Lake — răsfoire și căutare în decizii"),
    Feature("spete", "Spețe ANAP", "Workspace",
            "Cazuistica oficială ANAP", ui_only=True),
    Feature("dashboard", "Dashboard", "Workspace",
            "Statistici globale"),
    Feature("analytics", "Analiză CNSC", "Workspace",
            "Profil complet, predictor rezultat, analiză comparativă"),
    Feature("strategy", "Strategie Contestare", "Workspace",
            "Generator de strategie de contestare"),
    Feature("dosare", "Dosare Digitale", "Workspace",
            "Case management — dosare și documente atașate"),
    Feature("alerts", "Alerte Decizii", "Workspace",
            "Reguli de alertare pentru decizii noi"),

    # --- Instrumente juridice ---
    Feature("multi_document", "Analiză Multi-Document", "Instrumente juridice",
            "Red flags și consistență între 2-5 documente"),
    Feature("compliance", "Verificator Conformitate", "Instrumente juridice",
            "Verificare document vs. legislație"),
    Feature("drafter", "Drafter Contestații", "Instrumente juridice",
            "Generare contestații și alte documente"),
    Feature("redflags", "Red Flags Detector", "Instrumente juridice",
            "Detectare clauze problematice"),
    Feature("clarification", "Clarificări", "Instrumente juridice",
            "Generare solicitări de clarificare"),
    Feature("rag", "Jurisprudență RAG", "Instrumente juridice",
            "Memo-uri RAG pe jurisprudență"),

    # --- Formare ---
    Feature("training", "TrainingAP", "Formare",
            "Generare materiale didactice"),
    Feature("export", "Export materiale", "Formare",
            "Export DOCX / PDF / MD"),

    # --- Colaborare ---
    Feature("comments", "Comentarii pe documente", "Colaborare",
            "Comentarii inline pe documentele generate"),

    # --- Sistem ---
    Feature("settings", "Setări LLM", "Sistem",
            "Provider, model și chei API", admin_only=True),
    Feature("permissions", "Drepturi & Roluri", "Sistem",
            "Această pagină — matricea de permisiuni", admin_only=True),
    Feature("users", "Administrare utilizatori", "Sistem",
            "CRUD conturi utilizatori", admin_only=True),
    Feature("profile", "Profil", "Sistem",
            "Profilul propriu", always_on=True, ui_only=True),
    Feature("pricing", "Planuri & Prețuri", "Sistem",
            "Pagina de planuri", always_on=True, ui_only=True),
]

FEATURES_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURE_CATALOG}
ALL_FEATURE_KEYS: list[str] = [f.key for f in FEATURE_CATALOG]

ADMIN_ONLY_FEATURES: set[str] = {f.key for f in FEATURE_CATALOG if f.admin_only}
ALWAYS_ON_FEATURES: set[str] = {f.key for f in FEATURE_CATALOG if f.always_on}

FEATURE_CATEGORIES: list[str] = list(dict.fromkeys(f.category for f in FEATURE_CATALOG))


# Defaults, used to seed the table and as the fallback when it is unreachable.
# These reconcile the previous backend FEATURE_ROLES with the frontend
# ROLE_FEATURES; where the two disagreed, the stricter backend rule wins.
DEFAULT_ROLE_FEATURES: dict[str, list[str]] = {
    ANONYMOUS_ROLE: ["chat", "pricing"],
    "registered": [
        "chat", "datalake", "spete", "dashboard", "analytics", "rag",
        "profile", "pricing",
    ],
    "paid_basic": [
        "chat", "datalake", "spete", "dashboard", "analytics", "rag",
        "strategy", "compliance", "drafter", "redflags", "clarification",
        "dosare", "alerts", "comments", "profile", "pricing",
    ],
    "paid_pro": [
        "chat", "datalake", "spete", "dashboard", "analytics", "rag",
        "strategy", "compliance", "multi_document", "drafter", "redflags",
        "clarification", "training", "export", "dosare", "alerts", "comments",
        "profile", "pricing",
    ],
    "paid_enterprise": [
        "chat", "datalake", "spete", "dashboard", "analytics", "rag",
        "strategy", "compliance", "multi_document", "drafter", "redflags",
        "clarification", "training", "export", "dosare", "alerts", "comments",
        "profile", "pricing",
    ],
    "admin": list(ALL_FEATURE_KEYS),
}

# Plan name shown in the 403 message when a feature is denied.
_PLAN_HINT_ORDER = [
    ("paid_basic", "Basic"),
    ("paid_pro", "Pro"),
    ("paid_enterprise", "Enterprise"),
]

CACHE_TTL = 60  # seconds

_cache: Optional[dict[str, set[str]]] = None
_cache_time: float = 0.0
_table_missing_logged = False


def normalize_features(rol: str, features: list[str] | set[str]) -> list[str]:
    """Apply the non-negotiable guardrails to a requested feature list.

    Drops unknown keys, forces ``admin`` to hold everything, strips
    ``admin_only`` features from non-admin roles, and re-adds ``always_on``
    features for every authenticated role. Returns catalog order.
    """
    if rol == "admin":
        return list(ALL_FEATURE_KEYS)

    requested = {f for f in features if f in FEATURES_BY_KEY}
    requested -= ADMIN_ONLY_FEATURES

    if rol != ANONYMOUS_ROLE:
        requested |= ALWAYS_ON_FEATURES

    return [k for k in ALL_FEATURE_KEYS if k in requested]


def default_matrix() -> dict[str, list[str]]:
    """Guardrail-normalized copy of the shipped defaults."""
    return {rol: normalize_features(rol, DEFAULT_ROLE_FEATURES.get(rol, []))
            for rol in ALL_ROLES}


def invalidate_cache() -> None:
    """Drop the cached matrix so the next read hits the database."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0.0


async def load_matrix(session: AsyncSession, use_cache: bool = True) -> dict[str, set[str]]:
    """Return ``{role: {feature, ...}}`` for every known role.

    Falls back to ``DEFAULT_ROLE_FEATURES`` when the table has not been created
    yet, so the application keeps working between a deploy and its migration.
    Roles missing from the table also fall back to their defaults.
    """
    global _cache, _cache_time, _table_missing_logged

    now = time.monotonic()
    if use_cache and _cache is not None and (now - _cache_time) < CACHE_TTL:
        return _cache

    # Import here to avoid a circular import at module load time.
    from app.models.decision import RolePermission

    matrix: dict[str, set[str]] = {
        rol: set(normalize_features(rol, DEFAULT_ROLE_FEATURES.get(rol, [])))
        for rol in ALL_ROLES
    }

    try:
        result = await session.execute(select(RolePermission))
        for row in result.scalars().all():
            if row.rol not in matrix:
                # Unknown role in the table — keep it, it may be a new plan.
                matrix[row.rol] = set()
            matrix[row.rol] = set(normalize_features(row.rol, row.features or []))
        _table_missing_logged = False
    except SQLAlchemyError as e:
        # Most likely: relation "role_permissions" does not exist (migration
        # not applied yet). Roll back so the session stays usable.
        await session.rollback()
        if not _table_missing_logged:
            logger.warning(
                "role_permissions_unavailable_using_defaults",
                error=str(e),
                error_type=type(e).__name__,
            )
            _table_missing_logged = True
        # Do not cache a fallback result for long — retry on the next call.
        return matrix

    _cache = matrix
    _cache_time = now
    return matrix


async def get_role_features(session: AsyncSession, rol: Optional[str]) -> set[str]:
    """Effective feature set for a role (``None`` means anonymous)."""
    matrix = await load_matrix(session)
    key = rol or ANONYMOUS_ROLE
    if key in matrix:
        return matrix[key]
    # Unknown role — treat as anonymous rather than granting anything.
    return matrix.get(ANONYMOUS_ROLE, set())


async def has_feature(session: AsyncSession, rol: Optional[str], feature: str) -> bool:
    """True if the role may use the feature."""
    return feature in await get_role_features(session, rol)


async def required_plan_hint(session: AsyncSession, feature: str) -> str:
    """Cheapest paid plan that currently grants the feature, for 403 messages."""
    matrix = await load_matrix(session)
    for rol, label in _PLAN_HINT_ORDER:
        if feature in matrix.get(rol, set()):
            return label
    return "Enterprise"
