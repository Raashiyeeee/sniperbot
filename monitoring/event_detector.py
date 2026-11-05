"""
monitoring/event_detector.py
Detects whether an incoming social-media message is a token-launch event
and classifies its type.
"""
from __future__ import annotations

import re
from typing import Optional

import structlog

from database.models import EventType

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex pattern groups (all compiled once at import time)
# ---------------------------------------------------------------------------

_FLAGS = re.IGNORECASE

# launch_intent — team signals upcoming or scheduled launch
_LAUNCH_INTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bCA\s+soon\b", _FLAGS),
    re.compile(r"\blaunching\s+soon\b", _FLAGS),
    re.compile(r"\btoken\s+launch\s+(?:at|scheduled|tomorrow)\b", _FLAGS),
    re.compile(r"\btoken\s+launch\s+scheduled\s+for\b", _FLAGS),
    re.compile(r"\bCA\s+(?:will\s+be\s+revealed|revealed|shared|dropping)\s+in\b", _FLAGS),
    re.compile(r"\blaunch\s+tomorrow(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)?\b", _FLAGS),
    re.compile(r"\bcontract\s+(?:address\s+)?dropping\s+soon\b", _FLAGS),
    re.compile(r"\bcontract\s+(?:address\s+)?dropping\b", _FLAGS),
    re.compile(r"\blaunching\s+tomorrow\b", _FLAGS),
    re.compile(r"\blaunching\s+today\b", _FLAGS),
    re.compile(r"\blaunch\s+scheduled\b", _FLAGS),
    re.compile(r"\blaunch\s+time\b", _FLAGS),
    re.compile(r"\bmint\s+address\s+(?:coming|soon|dropping)\b", _FLAGS),
    re.compile(r"\bfair\s+launch\b", _FLAGS),
    re.compile(r"\bstealth\s+launch\b", _FLAGS),
    re.compile(r"\btoken\s+launches\b", _FLAGS),
    re.compile(r"\blaunching\s+at\b", _FLAGS),
    re.compile(r"\bgoing\s+live(?:\s+at|\s+today|\s+tomorrow)?\b", _FLAGS),
    re.compile(r"\bCA\s+will\s+be\s+shared\b", _FLAGS),
    re.compile(r"\bCA\s+(?:at|dropping\s+at)\b", _FLAGS),
]

# ca_announcement — a raw contract / mint address has been posted
_EVM_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
# Base-58 alphabet, 32-44 chars; negative look-ahead/behind to skip URLs
_SOL_ADDRESS_RE = re.compile(
    r"(?<![/\w])([1-9A-HJ-NP-Za-km-z]{32,44})(?![/\w])"
)

# launch_confirm — token is now actively trading
_LAUNCH_CONFIRM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"trading\s+(?:is\s+)?(?:now\s+)?live", _FLAGS),
    re.compile(r"trading\s+enabled", _FLAGS),
    re.compile(r"pool\s+live", _FLAGS),
    re.compile(r"liquidity\s+added", _FLAGS),
    re.compile(r"now\s+trading", _FLAGS),
    re.compile(r"just\s+launched", _FLAGS),
]

# update — schedule or details have changed
_UPDATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"launch\s+time\s+changed", _FLAGS),
    re.compile(r"\bnew\s+time\b", _FLAGS),
    re.compile(r"rescheduled", _FLAGS),
    re.compile(r"\bdelay(ed)?\b", _FLAGS),
    re.compile(r"corrected\s+CA", _FLAGS),
    re.compile(r"chain\s+changed", _FLAGS),
    re.compile(r"\bmigration\b", _FLAGS),
]

# cancellation — project is dead / pulling the plug
_CANCELLATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcancell?ed\b", _FLAGS),
    re.compile(r"not\s+launching", _FLAGS),
    re.compile(r"\brug\b", _FLAGS),
    re.compile(r"shutting\s+down", _FLAGS),
    re.compile(r"project\s+ended", _FLAGS),
]


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return True if *any* pattern matches the given text."""
    return any(p.search(text) for p in patterns)


def _has_ca(text: str) -> bool:
    """Return True if the text contains an EVM or Solana address."""
    if _EVM_ADDRESS_RE.search(text):
        return True
    # Reject addresses that are part of a URL path segment
    for match in _SOL_ADDRESS_RE.finditer(text):
        # Simple heuristic: not preceded by 'http' within 80 chars
        start = max(0, match.start() - 80)
        surrounding = text[start : match.start()]
        if "http" not in surrounding:
            return True
    return False


class EventDetector:
    """Classify incoming messages as token-launch event types.

    Parameters
    ----------
    None — stateless; all pattern tables are module-level constants.

    Usage
    -----
    ::

        detector = EventDetector()
        event_type = detector.detect(message)
    """

    def detect(self, message: dict) -> Optional[EventType]:
        """Analyse a raw message dict and return an :class:`EventType` or *None*.

        The classification priority is:

        1. ``cancellation``   — team confirms project is dead
        2. ``launch_confirm`` — trading / liquidity is live right now
        3. ``update``         — schedule or CA correction
        4. ``ca_announcement``— a contract/mint address is present
        5. ``launch_intent``  — upcoming launch signalled
        6. ``ambiguous``      — weak signal but not clearly any of the above
        7. ``None``           — no launch-related content detected

        Parameters
        ----------
        message:
            Raw message dict with at minimum a ``"text"`` key.

        Returns
        -------
        :class:`~database.models.EventType` or ``None``
        """
        log = logger.bind(
            platform=message.get("platform"),
            message_id=message.get("message_id") or message.get("chat_id"),
        )

        text: str = message.get("text", "") or ""
        if not text.strip():
            log.debug("event_detector.skip", reason="empty_text")
            return None

        # --- Priority 1: cancellation ---
        if _matches_any(text, _CANCELLATION_PATTERNS):
            log.info("event_detector.classified", event_type=EventType.CANCELLATION)
            return EventType.CANCELLATION

        # --- Priority 2: launch_confirm ---
        if _matches_any(text, _LAUNCH_CONFIRM_PATTERNS):
            log.info("event_detector.classified", event_type=EventType.LAUNCH_CONFIRM)
            return EventType.LAUNCH_CONFIRM

        # --- Priority 3: update ---
        if _matches_any(text, _UPDATE_PATTERNS):
            log.info("event_detector.classified", event_type=EventType.UPDATE)
            return EventType.UPDATE

        # --- Priority 4: ca_announcement ---
        if _has_ca(text):
            log.info("event_detector.classified", event_type=EventType.CA_ANNOUNCEMENT)
            return EventType.CA_ANNOUNCEMENT

        # --- Priority 5: launch_intent ---
        if _matches_any(text, _LAUNCH_INTENT_PATTERNS):
            log.info("event_detector.classified", event_type=EventType.LAUNCH_INTENT)
            return EventType.LAUNCH_INTENT

        # --- Priority 6: ambiguous (weak keyword overlap) ---
        weak_keywords = re.compile(
            r"\b(token|launch|mint|presale|airdrop|tokenomics|whitelist|fairlaunch"
            r"|degen|gem|moonshot|100x|ape|buy now|chart)\b",
            _FLAGS,
        )
        if weak_keywords.search(text):
            log.info("event_detector.classified", event_type=EventType.AMBIGUOUS)
            return EventType.AMBIGUOUS

        log.debug("event_detector.no_match")
        return None
