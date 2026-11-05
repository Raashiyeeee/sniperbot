"""
tests/test_event_detector.py
─────────────────────────────
Unit tests for the EventDetector — Phase 1.
Run with: pytest tests/
"""
from __future__ import annotations

import pytest

from monitoring.event_detector import EventDetector


@pytest.fixture
def detector() -> EventDetector:
    return EventDetector()


class TestLaunchIntent:
    def test_launching_today(self, detector):
        msg = {"text": "Our token is launching today at 3pm UTC!", "platform": "x"}
        assert detector.detect(msg) == "launch_intent"

    def test_launching_tomorrow(self, detector):
        msg = {"text": "We're launching tomorrow, stay tuned 🚀", "platform": "x"}
        assert detector.detect(msg) == "launch_intent"

    def test_ca_soon(self, detector):
        msg = {"text": "CA soon, get ready!", "platform": "telegram"}
        assert detector.detect(msg) == "launch_intent"

    def test_fair_launch(self, detector):
        msg = {"text": "Fair launch in 2 hours on Robinhood Chain", "platform": "x"}
        assert detector.detect(msg) == "launch_intent"

    def test_stealth_launch(self, detector):
        msg = {"text": "Stealth launch happening now", "platform": "telegram"}
        assert detector.detect(msg) == "launch_intent"


class TestCADetection:
    def test_evm_address(self, detector):
        msg = {"text": "CA: 0xAbCdEf1234567890AbCdEf1234567890AbCdEf12", "platform": "x"}
        assert detector.detect(msg) == "ca_announcement"

    def test_solana_address(self, detector):
        msg = {"text": "Mint: So11111111111111111111111111111111111112", "platform": "telegram"}
        assert detector.detect(msg) == "ca_announcement"


class TestLaunchConfirm:
    def test_trading_live(self, detector):
        msg = {"text": "Trading is now LIVE! Ape in 🦍", "platform": "x"}
        assert detector.detect(msg) == "launch_confirm"

    def test_liquidity_added(self, detector):
        msg = {"text": "Liquidity added ✅ Let's go!", "platform": "telegram"}
        assert detector.detect(msg) == "launch_confirm"


class TestCancellation:
    def test_cancelled(self, detector):
        msg = {"text": "Project has been cancelled. Sorry everyone.", "platform": "x"}
        assert detector.detect(msg) == "cancellation"


class TestNoEvent:
    def test_random_text(self, detector):
        msg = {"text": "Just sharing some thoughts on the market today.", "platform": "x"}
        assert detector.detect(msg) is None

    def test_empty_text(self, detector):
        msg = {"text": "", "platform": "x"}
        assert detector.detect(msg) is None


class TestBlacklistNotBypassed:
    """Phase 1 safety: ensure rejected/blacklisted identities don't sneak through."""
    # Full blacklist bypass tests live in tests/test_registry.py (integration)
    pass

