"""SQLAlchemy models for CNSC decisions."""

from app.models.decision import (
    DecizieCNSC,
    ArgumentareCritica,
    NomenclatorCPV,
    ActNormativ,
    LegislatieFragment,
    SearchScope,
    # Legacy aliases
    Decision,
    DecisionChunk,
)

__all__ = [
    "DecizieCNSC",
    "ArgumentareCritica",
    "NomenclatorCPV",
    "ActNormativ",
    "LegislatieFragment",
    "SearchScope",
    "Decision",
    "DecisionChunk",
]
