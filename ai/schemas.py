"""
ai/schemas.py
─────────────
Pydantic schemas for Gemini structured outputs, verification checks,
and project scoring.
Matches Master Prompt §2, §3, §6, and §13.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCategory(str, Enum):
    GAMING = "Gaming"
    STAKING = "Staking"
    TRADING = "Trading"
    LAUNCHPADS = "Launchpads"
    DEFI = "DeFi"
    UTILITY = "Utility"
    CREATOR_PLATFORMS = "Creator Platforms"
    INFRASTRUCTURE = "Infrastructure"
    OTHER = "Other"


class BlockchainType(str, Enum):
    SOLANA = "Solana"
    ROBINHOOD_CHAIN = "Robinhood Chain"
    UNKNOWN = "Unknown"


class VerificationStatus(str, Enum):
    VERIFIED = "Verified"
    UNVERIFIED = "Unverified"
    UNKNOWN = "Unknown"
    FAILED = "Failed"


class RecommendationLevel(str, Enum):
    HIGH_QUALITY = "HIGH_QUALITY"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class AnnouncementType(str, Enum):
    UPCOMING_TGE = "UPCOMING_TGE"
    OFFICIAL_LAUNCH_DATE = "OFFICIAL_LAUNCH_DATE"
    CA_REVEAL_SCHEDULED = "CA_REVEAL_SCHEDULED"
    CA_SOON = "CA_SOON"
    ORDINARY_UPDATE = "ORDINARY_UPDATE"
    MARKETING = "MARKETING"
    UNKNOWN = "UNKNOWN"


# ── Structured Sub-Analyses ───────────────────────────────────────

class ProjectIdentification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Identified project name")
    ticker: Optional[str] = Field(None, description="Project token ticker if mentioned")
    primary_category: ProjectCategory = Field(
        ProjectCategory.UTILITY,
        description="Primary category of the project"
    )
    categories: List[str] = Field(
        default_factory=list,
        description="List of applicable categories (e.g. Gaming, Staking)"
    )
    blockchain: BlockchainType = Field(
        BlockchainType.SOLANA,
        description="Target blockchain (Solana or Robinhood Chain)"
    )
    website_url: Optional[str] = Field(None, description="Official project website")
    official_x_handle: Optional[str] = Field(None, description="Official X (@username)")
    official_telegram: Optional[str] = Field(None, description="Official Telegram channel/group")
    founder_team: Optional[str] = Field(None, description="Founder or team information if known")
    product_summary: Optional[str] = Field(None, description="Summary of existing product/utility")
    documentation_url: Optional[str] = Field(None, description="Link to documentation / whitepaper")


class ProductAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_real_product: bool = Field(False, description="True if a genuine working product/demo exists")
    has_functional_website: bool = Field(False, description="True if official website is functional")
    has_documentation: bool = Field(False, description="True if documentation/whitepaper exists")
    utility_score: float = Field(0.0, ge=0.0, le=100.0, description="Product utility score (0-100)")
    verified_evidence: List[str] = Field(default_factory=list, description="Verified facts about product")
    unknowns: List[str] = Field(default_factory=list, description="Unverified claims or missing product details")
    red_flags: List[str] = Field(default_factory=list, description="Product red flags or cloned code")


class CredibilityAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    official_source_confirmed: bool = Field(False, description="True if post originates from official project")
    team_identifiable: bool = Field(False, description="True if team/founder is identifiable")
    website_verified: bool = Field(False, description="True if website matches claimed identity")
    ecosystem_presence: bool = Field(False, description="True if integrated into Solana/Robinhood ecosystem")
    credibility_score: float = Field(0.0, ge=0.0, le=100.0, description="Project credibility score (0-100)")
    ecosystem_score: float = Field(0.0, ge=0.0, le=100.0, description="Ecosystem presence score (0-100)")
    evidence: List[str] = Field(default_factory=list, description="Verified credibility points")
    red_flags: List[str] = Field(default_factory=list, description="Credibility concerns or impersonation")


class SocialAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_credibility_score: float = Field(0.0, ge=0.0, le=100.0, description="X account authenticity (0-100)")
    community_quality_score: float = Field(0.0, ge=0.0, le=100.0, description="Community engagement quality (0-100)")
    account_age_assessment: str = Field("UNKNOWN", description="Account age evaluation")
    organic_engagement_detected: bool = Field(False, description="True if replies/engagement look organic")
    bot_activity_suspected: bool = Field(False, description="True if artificial replies/bots detected")
    username_change_risk: bool = Field(False, description="True if suspicious rename pattern found")
    red_flags: List[str] = Field(default_factory=list, description="Social red flags")


class AnnouncementAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    announcement_type: AnnouncementType = Field(
        AnnouncementType.UNKNOWN,
        description="Classification of the announcement"
    )
    is_official_announcement: bool = Field(False, description="Published by confirmed official project account")
    is_authorized_source: bool = Field(False, description="Source authorized to announce token/TGE")
    launch_confirmed: bool = Field(False, description="True if a genuine token launch is announced")
    confirmation_score: float = Field(0.0, ge=0.0, le=100.0, description="Token launch confirmation score (0-100)")
    announcement_url: Optional[str] = Field(None, description="Direct URL to official announcement post")
    launch_date_str: Optional[str] = Field(None, description="Announced launch time or countdown if any")
    expected_ca_time_str: Optional[str] = Field(None, description="When CA will be shared")


class VerificationChecklist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    official_website: VerificationStatus = VerificationStatus.UNKNOWN
    product_existence: VerificationStatus = VerificationStatus.UNKNOWN
    official_x_account: VerificationStatus = VerificationStatus.UNKNOWN
    team_association: VerificationStatus = VerificationStatus.UNKNOWN
    community_activity: VerificationStatus = VerificationStatus.UNKNOWN
    token_launch_announcement: VerificationStatus = VerificationStatus.UNKNOWN


class QualityAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_score: float = Field(0.0, ge=0.0, le=100.0, description="Overall weighted quality score (0-100)")
    sub_scores: Dict[str, float] = Field(default_factory=dict, description="Sub-category score breakdown")
    risk_level: str = Field("MEDIUM", description="LOW, MEDIUM, or HIGH")
    recommendation: RecommendationLevel = Field(
        RecommendationLevel.REVIEW,
        description="Final AI recommendation"
    )
    mandatory_rejection: bool = Field(
        False,
        description="True if mandatory rejection condition is met (impersonation, malicious, unauthorized)"
    )
    mandatory_rejection_reason: Optional[str] = Field(None, description="Reason if mandatory rejection triggered")
    red_flags: List[str] = Field(default_factory=list, description="All detected red flags")
    missing_information: List[str] = Field(default_factory=list, description="Missing or unverified items")
    reasoning_summary: str = Field("", description="Executive summary of the evaluation")


# ── Full Root Intelligence Schema ─────────────────────────────────

class ProjectIntelligenceResult(BaseModel):
    """Complete structured intelligence result returned for each evaluated project."""
    model_config = ConfigDict(extra="ignore")

    project: ProjectIdentification
    product_analysis: ProductAnalysis
    credibility_analysis: CredibilityAnalysis
    social_analysis: SocialAnalysis
    announcement_analysis: AnnouncementAnalysis
    verification: VerificationChecklist
    quality_assessment: QualityAssessment
