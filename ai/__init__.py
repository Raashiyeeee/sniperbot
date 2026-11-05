"""
ai package
──────────
Gemini AI project intelligence, classification, verification, and scoring.
"""
from ai.gemini_client import GeminiClient
from ai.project_evaluator import ProjectEvaluator
from ai.schemas import (
    BlockchainType,
    ProjectCategory,
    ProjectIntelligenceResult,
    RecommendationLevel,
    VerificationStatus,
)

__all__ = [
    "GeminiClient",
    "ProjectEvaluator",
    "ProjectCategory",
    "BlockchainType",
    "VerificationStatus",
    "RecommendationLevel",
    "ProjectIntelligenceResult",
]
