"""SQLAlchemy models for CNSC decisions."""

from app.models.decision import (
    DecizieCNSC,
    SectiuneDecizie,
    ArgumentareCritica,
    CitatVerbatim,
    ReferintaArticol,
    NomenclatorCPV,
    # Legacy aliases
    Decision,
    DecisionChunk,
)

__all__ = [
    "DecizieCNSC",
    "SectiuneDecizie",
    "ArgumentareCritica",
    "CitatVerbatim",
    "ReferintaArticol",
    "NomenclatorCPV",
    "Decision",
    "DecisionChunk",
]
