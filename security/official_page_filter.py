"""
security/official_page_filter.py
─────────────────────────────────
Filters out bystander shills, influencers, and non-official accounts (Spec v1.3+).
Ensures discovery alerts are only triggered for genuine official project pages.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Phrases used by followers, callers, and bystanders talking ABOUT a drop
_BYSTANDER_PHRASES_RE = re.compile(
    r"\b(?:turning\s+(?:on\s+)?notifications|notifications\s+on|turned\s+notifications\s+on|"
    r"waiting\s+(?:for|on)|can\'?t\s+wait|who\s+(?:is|else)|ready\s+for|"
    r"when\s+ca|drop\s+the\s+ca|send\s+ca|give\s+us\s+ca|watching\s+this|what\s+time\s+is\s+the\s+drop)\b",
    re.IGNORECASE,
)

# Handles or display names indicating shill/caller channels rather than official token pages
_SHILL_PATTERNS = (
    "shill",
    "calls",
    "gemcall",
    "alphacall",
    "signals",
    "crypto_gems",
    "whale_alert",
    "moonshots",
)

# Bio keywords indicative of personal KOL/promoter accounts
_INFLUENCER_BIO_KEYWORDS = (
    "kol",
    "crypto caller",
    "promoter",
    "shiller",
    "influencer",
    "calls channel",
    "gem hunter",
)


class OfficialPageFilter:
    """Evaluates whether an X post originates from an official project account."""

    def __init__(self) -> None:
        self._log = logger.bind(component="OfficialPageFilter")

    def evaluate(
        self,
        text: str,
        author_handle: Optional[str] = None,
        author_name: Optional[str] = None,
        author_bio: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if a post is from an official project page.

        Returns
        -------
        Tuple[is_official, reason]
            is_official: True if post is likely from an official project page.
            reason: Explanation if rejected, otherwise None.
        """
        # 1. Reject bystander / follower phrasing
        if _BYSTANDER_PHRASES_RE.search(text):
            msg = "Rejected: Post contains follower/bystander phrasing (e.g. 'turning notifications on')"
            self._log.debug("bystander_phrase_rejected", text=text[:80])
            return False, msg

        # 2. Check handle / display name for shill patterns
        handle_lower = (author_handle or "").lower()
        name_lower = (author_name or "").lower()

        for pattern in _SHILL_PATTERNS:
            if pattern in handle_lower or pattern in name_lower:
                msg = f"Rejected: Handle/Name contains shill indicator '{pattern}'"
                self._log.debug("shill_handle_rejected", handle=author_handle, pattern=pattern)
                return False, msg

        # 3. Check author profile bio for influencer / caller keywords
        bio_lower = (author_bio or "").lower()
        for kw in _INFLUENCER_BIO_KEYWORDS:
            if kw in bio_lower:
                msg = f"Rejected: Profile bio indicates personal influencer/caller ('{kw}')"
                self._log.debug("influencer_bio_rejected", handle=author_handle, keyword=kw)
                return False, msg

        return True, None

