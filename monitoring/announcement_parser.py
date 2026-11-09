"""
monitoring/announcement_parser.py
Parses a raw social-media message into a structured :class:`ParsedAnnouncement`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import structlog
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex constants
# ---------------------------------------------------------------------------

# $TICKER — uppercase letters only, 2-8 chars
_TICKER_RE = re.compile(r"\$([A-Z]{2,8})\b")

# EVM contract address
_EVM_ADDRESS_RE = re.compile(r"\b(0x[0-9a-fA-F]{40})\b")

# Solana mint address — base-58 alphabet, 32-44 chars, not inside a URL path
_SOL_ADDRESS_RE = re.compile(r"(?<![/\w])([1-9A-HJ-NP-Za-km-z]{32,44})(?![/\w])")

# Chain name mentions
_CHAIN_ROBINHOOD_RE = re.compile(r"\brobinhood(?:\s+chain)?\b", re.IGNORECASE)
_CHAIN_SOLANA_RE = re.compile(r"\b(solana|sol)\b", re.IGNORECASE)
_CHAIN_ELYSIUM_TESTNET_RE = re.compile(r"\belysium\s+testnet\b", re.IGNORECASE)
_CHAIN_ELYSIUM_RE = re.compile(r"\belysium\b", re.IGNORECASE)
_CHAIN_EVM_RE = re.compile(r"\bevm\b", re.IGNORECASE)

# "ProjectName token" — single capitalised word before "token"
_PROJECT_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{1,30})\s+token\b", re.IGNORECASE)

# Time expressions: "at HH:MM", "at X:XX UTC/EST", "tomorrow at HH:MM"
_TIME_RE = re.compile(
    r"(?:tomorrow\s+)?at\s+(\d{1,2}:\d{2}(?:\s*[ap]m)?(?:\s*[A-Z]{2,5})?)",
    re.IGNORECASE,
)

# CA source hints
_X_SOURCE_RE = re.compile(r"\b(twitter|x\.com|on\s+x)\b", re.IGNORECASE)
_TG_SOURCE_RE = re.compile(r"\b(telegram|tg|t\.me)\b", re.IGNORECASE)

# URLs — used to exclude Solana addresses that are part of URL paths
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# ParsedAnnouncement model
# ---------------------------------------------------------------------------


@dataclass
class ParsedAnnouncement:
    """Structured representation of a token-launch announcement.

    All extracted fields default to *None* — the parser never raises on
    incomplete data.
    """

    # Source metadata
    platform: str
    message_id: str
    post_url: Optional[str]
    text: str
    author_id: Optional[str]
    event_type: str
    published_at: Optional[datetime]
    received_at: datetime
    is_forwarded: bool
    chat_id: Optional[str] = None

    # Extracted fields
    project_name: Optional[str] = None
    ticker: Optional[str] = None
    chain_id: Optional[str] = None
    ca_address: Optional[str] = None
    expected_launch_time: Optional[datetime] = None
    expected_ca_source: Optional[str] = None
    description: Optional[str] = None

    # Spec v1.3 fields
    expected_ca_time_str: str = "Not specified"
    launch_time_str: str = "Not specified"
    announcement_summary: str = ""
    x_profile_url: Optional[str] = None
    network: str = "Not specified"

    # Original raw payload
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _strip_urls(text: str) -> str:
    """Remove all URLs from *text* before address extraction."""
    return _URL_RE.sub(" ", text)


def _extract_ticker(text: str) -> Optional[str]:
    """Return the first ``$TICKER`` found, uppercased."""
    match = _TICKER_RE.search(text)
    return match.group(1).upper() if match else None


def _extract_evm_address(text: str) -> Optional[str]:
    """Return the first EVM contract address found."""
    match = _EVM_ADDRESS_RE.search(text)
    return match.group(1) if match else None


def _extract_sol_address(text: str) -> Optional[str]:
    """Return the first Solana mint address found (after stripping URLs)."""
    clean = _strip_urls(text)
    for match in _SOL_ADDRESS_RE.finditer(clean):
        candidate = match.group(1)
        # Solana addresses are >= 32 chars; double-check length
        if 32 <= len(candidate) <= 44:
            return candidate
    return None


def _extract_ca(text: str) -> Optional[str]:
    """Prefer EVM address; fall back to Solana."""
    return _extract_evm_address(text) or _extract_sol_address(text)


def _detect_chain(text: str) -> Optional[str]:
    """Return a supported chain_id or ``None``."""
    # More specific names first (testnet before mainnet)
    if _CHAIN_ELYSIUM_TESTNET_RE.search(text):
        return "elysium_testnet"
    if _CHAIN_ELYSIUM_RE.search(text):
        return "elysium"
    if _CHAIN_ROBINHOOD_RE.search(text):
        return "robinhood"
    if _CHAIN_SOLANA_RE.search(text):
        return "solana"
    # EVM without explicit chain name — cannot resolve definitively
    if _CHAIN_EVM_RE.search(text):
        return "robinhood"  # Default EVM L2 in this project
    return None


def _infer_chain_from_address(text: str, current: Optional[str]) -> Optional[str]:
    """If chain is still unknown, guess from address format in text."""
    if current:
        return current
    if _EVM_ADDRESS_RE.search(text):
        return "robinhood"
    if _extract_sol_address(text):
        return "solana"
    return None


def _extract_project_name(text: str, ticker: Optional[str]) -> Optional[str]:
    """Try to extract a human-readable project name."""
    # Prefer the pattern "SomeName token"
    match = _PROJECT_NAME_RE.search(text)
    if match:
        return match.group(1)
    # Fall back to the ticker itself
    return ticker


def _parse_launch_time(text: str) -> Optional[datetime]:
    """Attempt to parse a launch time from *text* using dateutil.

    Returns a timezone-aware datetime (UTC) or None.
    """
    try:
        match = _TIME_RE.search(text)
        if not match:
            return None
        time_str = match.group(0)
        now_utc = datetime.now(tz=timezone.utc)
        # If "tomorrow" is in the overall match include it for dateutil
        parsed = dateutil_parser.parse(
            time_str,
            default=now_utc.replace(hour=0, minute=0, second=0, microsecond=0),
            fuzzy=True,
        )
        # If result is naive, assume UTC
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # If parsed time already passed today and "tomorrow" was mentioned, add 1 day
        if "tomorrow" in time_str.lower() and parsed <= now_utc:
            parsed += relativedelta(days=1)
        return parsed
    except Exception:  # noqa: BLE001
        return None


def _detect_ca_source(text: str) -> Optional[str]:
    """Detect where the CA will be shared (x, telegram, or both)."""
    on_x = bool(_X_SOURCE_RE.search(text))
    on_tg = bool(_TG_SOURCE_RE.search(text))
    if on_x and on_tg:
        return "both"
    if on_x:
        return "x"
    if on_tg:
        return "telegram"
    return None


def _first_meaningful_sentence(text: str) -> Optional[str]:
    """Return the first non-empty sentence (up to 200 chars)."""
    for sentence in re.split(r"[.!?\n]", text):
        stripped = sentence.strip()
        if len(stripped) >= 15:  # ignore very short fragments
            return stripped[:200]
    return None


def _format_time_component(time_str: str) -> str:
    """Normalize '5 PM UTC' -> '5:00 PM UTC', while preserving '5:30 PM UTC' or '17:00 UTC'."""
    m = re.match(r"^(\d{1,2})\s*([ap]m)(.*)$", time_str.strip(), re.I)
    if m:
        hr, ampm, rest = m.groups()
        rest_str = rest.strip()
        if rest_str:
            return f"{hr}:00 {ampm.upper()} {rest_str}"
        return f"{hr}:00 {ampm.upper()}"
    return time_str.strip()


def _extract_expected_ca_time(text: str) -> str:
    """Extract expected CA publication time dynamically for any project.
    Rule: If a post says only 'CA soon' without specifying a time, return 'Not specified'.
    Never invent an expected CA time.
    """
    # 1. Relative duration associated with CA / contract
    # e.g. 'CA in 2 hours', 'CA will be revealed in 2 hours', 'contract dropping in 30 mins', 'CA in ~45m'
    m_rel = re.search(
        r"\b(?:CA|contract(?:\s+address)?|mint(?:\s+address)?)\s+(?:(?:will\s+be\s+)?(?:revealed|shared|posted|dropping|dropped|released)?\s*)?(?:in\s+)?(?:approximately\s+|~\s*)?(\d+\s*(?:hours?|hrs?|minutes?|mins?|h|m))\b",
        text,
        re.I,
    )
    if m_rel:
        val = m_rel.group(1).strip()
        if val.endswith("h") and not val.endswith("sh"):
            val = val[:-1] + " hours"
        elif val.endswith("m") and not val.endswith("am") and not val.endswith("pm"):
            val = val[:-1] + " minutes"
        return f"In approximately {val}"

    # 2. 'in approximately X hours/mins' preceding CA or contract specifically
    m_pre = re.search(
        r"(?<!launching\s)(?<!launch\s)(?<!live\s)\bin\s+(?:approximately\s+|~\s*)?(\d+\s*(?:hours?|hrs?|minutes?|mins?))\b[^\n.!?]*(?:CA|contract|mint)",
        text,
        re.I,
    )
    if m_pre:
        return f"In approximately {m_pre.group(1).strip()}"

    # 3. Absolute time: 'CA at 5 PM UTC', 'Contract address will be posted at 6:00 PM UTC', 'CA dropping at 4 PM'
    m_at = re.search(
        r"\b(?:CA|contract(?:\s+address)?|mint(?:\s+address)?)\s+(?:(?:will\s+be\s+)?(?:revealed|shared|posted|dropping|dropped|released)?\s*)?(?:at|dropping\s+at)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]m)?(?:\s*[A-Z]{2,5})?)\b",
        text,
        re.I,
    )
    if m_at:
        return f"At {_format_time_component(m_at.group(1))}"

    return "Not specified"


def _extract_launch_time_str(text: str) -> str:
    """Extract launch time as a human-readable string dynamically for any project.
    Rule: Never invent a launch time. If unspecified, return 'Not specified'.
    """
    # 1. Relative duration: 'launching in 3 hours', 'launch in 45 minutes', 'going live in 1 hour'
    m_rel = re.search(
        r"\b(?:launch(?:ing)?|fair\s+launch|stealth\s+launch|going\s+live|trading\s+(?:starts?|begins?))\s+(?:in\s+)(?:approximately\s+|~\s*)?(\d+\s*(?:hours?|hrs?|minutes?|mins?))\b",
        text,
        re.I,
    )
    if m_rel:
        return f"In approximately {m_rel.group(1).strip()}"

    # 2. Specific time expressions
    # 'tomorrow at 12 PM', 'launching tomorrow at 3 PM UTC', 'fair launch scheduled for tomorrow at 3 PM CET'
    m_tom_time = re.search(
        r"(?:tomorrow\s+at|scheduled\s+for\s+tomorrow\s+at)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]m)?(?:\s*[A-Z]{2,5})?)\b",
        text,
        re.I,
    )
    if m_tom_time:
        return f"Tomorrow at {_format_time_component(m_tom_time.group(1))}"

    # 'today at 9:30 PM', 'going live today at 9:30 PM'
    m_today_time = re.search(
        r"(?:today\s+at|going\s+live\s+today\s+at)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]m)?(?:\s*[A-Z]{2,5})?)\b",
        text,
        re.I,
    )
    if m_today_time:
        return f"Today at {_format_time_component(m_today_time.group(1))}"

    # General launch at / going live at / trading starts at / listing at
    m_time = re.search(
        r"\b(?:(?:token\s+|fair\s+|stealth\s+)?launch(?:ing)?|going\s+live|trading\s+(?:starts?|begins?)|listing|pool\s+opens?)\s+(?:scheduled\s+for\s+)?(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]m)?(?:\s*[A-Z]{2,5})?)\b",
        text,
        re.I,
    )
    if m_time:
        t_val = _format_time_component(m_time.group(1))
        if re.search(r"([ap]m|[0-9]{1,2}:[0-9]{2}|UTC|EST|PST|GMT|CET)", t_val, re.I):
            if "tomorrow" in text.lower():
                return f"Tomorrow at {t_val}"
            if "today" in text.lower():
                return f"Today at {t_val}"
            return t_val

    # Day-only keywords
    if re.search(r"\b(?:token\s+)?launch(?:ing)?\s+tomorrow\b", text, re.I):
        return "Tomorrow"
    if re.search(r"\b(?:token\s+)?launch(?:ing)?\s+today\b", text, re.I):
        return "Today"

    return "Not specified"


def _extract_announcement_summary(text: str) -> str:
    """Extract clean launch announcement statement."""
    launch_intent_re = re.compile(
        r"(?:token\s+launch|launching|fair\s+launch|stealth\s+launch|ca\s+soon|contract\s+dropping|going\s+live)",
        re.I,
    )
    for line in text.split("\n"):
        line = line.strip()
        if launch_intent_re.search(line) and len(line) >= 10:
            clean = re.sub(r"https?://\S+", "", line).strip()
            if clean:
                return clean[:200]

    for sentence in re.split(r"[.!?\n]", text):
        s = sentence.strip()
        if launch_intent_re.search(s) and len(s) >= 10:
            clean = re.sub(r"https?://\S+", "", s).strip()
            if clean:
                return clean[:200]

    first = text.strip().split("\n")[0].strip() if text else ""
    return re.sub(r"https?://\S+", "", first)[:200] or "Token launch scheduled."


def _format_network_name(chain_id: Optional[str], text: str) -> str:
    """Return a human-readable network name or 'Not specified'."""
    cid = (chain_id or "").lower()
    t_lower = text.lower()
    if cid == "elysium_testnet" or "elysium testnet" in t_lower:
        return "Elysium Testnet"
    if cid == "elysium" or "elysium" in t_lower:
        return "Elysium"
    if cid == "solana" or "solana" in t_lower or "$sol" in t_lower or "pump.fun" in t_lower or "raydium" in t_lower:
        return "Solana"
    if cid == "robinhood" or "robinhood" in t_lower or "rh chain" in t_lower or "evm" in t_lower or "0x" in text:
        return "Robinhood Chain"
    return "Not specified"


def _build_post_url(platform: str, message: dict) -> Optional[str]:
    """Construct exact URL to the announcement post."""
    author = str(message.get("author_id", "")).lstrip("@")
    msg_id = str(message.get("message_id", ""))
    if platform == "x" and author and msg_id:
        return f"https://x.com/{author}/status/{msg_id}"
    if message.get("post_url"):
        return message["post_url"]
    try:
        if platform == "x" and msg_id:
            return f"https://x.com/i/web/status/{msg_id}"
        if platform == "telegram":
            chat_id = str(message.get("chat_id", "")).lstrip("-100")
            if chat_id and msg_id:
                return f"https://t.me/c/{chat_id}/{msg_id}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _build_profile_url(platform: str, author_id: Optional[str]) -> Optional[str]:
    """Construct direct URL to the project's profile."""
    if not author_id:
        return None
    clean = author_id.lstrip("@")
    if platform == "x":
        return f"https://x.com/{clean}"
    if platform == "telegram":
        return f"https://t.me/{clean}"
    return None


def _coerce_datetime(value: object) -> Optional[datetime]:
    """Safely coerce a value to a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = dateutil_parser.parse(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# AnnouncementParser
# ---------------------------------------------------------------------------


class AnnouncementParser:
    """Convert a raw message dict into a :class:`ParsedAnnouncement`.

    This class is stateless and safe to share across coroutines.

    Usage
    -----
    ::

        parser = AnnouncementParser()
        parsed = parser.parse(message, event_type)
    """

    def parse(self, message: dict, event_type: str) -> ParsedAnnouncement:
        """Parse *message* and return a :class:`ParsedAnnouncement`.

        Never raises — all field extraction is wrapped defensively.
        Unknown or missing fields are stored as ``None``.

        Parameters
        ----------
        message:
            Raw message dict from an X or Telegram connector.
        event_type:
            String label produced by :class:`~monitoring.event_detector.EventDetector`.

        Returns
        -------
        :class:`ParsedAnnouncement`
        """
        log = logger.bind(
            platform=message.get("platform"),
            event_type=event_type,
            message_id=message.get("message_id") or message.get("chat_id"),
        )

        try:
            platform: str = message.get("platform") or "unknown"
            message_id: str = str(
                message.get("message_id") or message.get("chat_id") or ""
            )
            text: str = message.get("text") or ""
            author_id: Optional[str] = str(a) if (a := message.get("author_id") or message.get("sender_id")) else None
            chat_id: Optional[str] = str(c) if (c := message.get("chat_id")) is not None else None
            published_at = _coerce_datetime(message.get("published_at"))
            received_at = _coerce_datetime(message.get("received_at")) or datetime.now(tz=timezone.utc)
            is_forwarded: bool = bool(message.get("is_forwarded", False))
            raw: dict = message.get("raw") or {}

            post_url = _build_post_url(platform, message)

            ticker = _extract_ticker(text)
            ca_address = _extract_ca(text)
            chain_id = _detect_chain(text)
            chain_id = _infer_chain_from_address(text, chain_id)
            project_name = _extract_project_name(text, ticker)
            expected_launch_time = _parse_launch_time(text)
            expected_ca_source = _detect_ca_source(text)
            description = _first_meaningful_sentence(text)

            # Spec v1.3 fields
            expected_ca_time_str = _extract_expected_ca_time(text)
            launch_time_str = _extract_launch_time_str(text)
            announcement_summary = _extract_announcement_summary(text)
            network = _format_network_name(chain_id, text)
            x_profile_url = _build_profile_url(platform, author_id)

            log.info(
                "announcement_parser.parsed",
                ticker=ticker,
                chain_id=chain_id,
                has_ca=ca_address is not None,
                has_launch_time=expected_launch_time is not None,
            )

            return ParsedAnnouncement(
                platform=platform,
                message_id=message_id,
                post_url=post_url,
                text=text,
                author_id=author_id,
                event_type=event_type,
                published_at=published_at,
                received_at=received_at,
                is_forwarded=is_forwarded,
                chat_id=chat_id,
                project_name=project_name,
                ticker=ticker,
                chain_id=chain_id,
                ca_address=ca_address,
                expected_launch_time=expected_launch_time,
                expected_ca_source=expected_ca_source,
                description=description,
                expected_ca_time_str=expected_ca_time_str,
                launch_time_str=launch_time_str,
                announcement_summary=announcement_summary,
                x_profile_url=x_profile_url,
                network=network,
                raw=raw,
            )

        except Exception as exc:  # noqa: BLE001
            log.exception("announcement_parser.error", error=str(exc))
            # Return a minimal but valid object so the pipeline can continue
            return ParsedAnnouncement(
                platform=message.get("platform") or "unknown",
                message_id=str(message.get("message_id") or message.get("chat_id") or ""),
                post_url=None,
                text=message.get("text") or "",
                author_id=None,
                event_type=event_type,
                published_at=None,
                received_at=datetime.now(tz=timezone.utc),
                is_forwarded=False,
                raw=message.get("raw") or {},
            )
