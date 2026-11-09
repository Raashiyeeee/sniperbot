"""
tests/test_announcement_parser.py
─────────────────────────────────
Unit tests for AnnouncementParser — extracting tickers, CAs, chains, launch times.
"""
from __future__ import annotations

import pytest

from monitoring.announcement_parser import AnnouncementParser


@pytest.fixture
def parser() -> AnnouncementParser:
    return AnnouncementParser()


class TestAnnouncementParser:
    def test_extract_ticker_and_chain(self, parser):
        msg = {
            "platform": "x",
            "message_id": "123456",
            "post_url": "https://x.com/status/123456",
            "text": "Introducing $SNIPE on Robinhood Chain! Fair launch today.",
            "author_id": "SniperDev",
            "published_at": "2026-11-04T12:00:00Z",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "launch_intent")
        assert parsed.ticker == "SNIPE"
        assert parsed.chain_id == "robinhood"
        assert parsed.platform == "x"

    def test_extract_evm_ca(self, parser):
        msg = {
            "platform": "telegram",
            "message_id": "9988",
            "text": "Official CA: 0x1234567890abcdef1234567890abcdef12345678 on Robinhood",
            "author_id": "admin_joe",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "ca_announcement")
        assert parsed.ca_address == "0x1234567890abcdef1234567890abcdef12345678"
        assert parsed.chain_id == "robinhood"

    def test_extract_solana_ca(self, parser):
        msg = {
            "platform": "telegram",
            "message_id": "9989",
            "text": "Mint address: 4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R on Solana",
            "author_id": "sol_admin",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "ca_announcement")
        assert parsed.ca_address == "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
        assert parsed.chain_id == "solana"

    def test_ca_not_extracted_from_url_path(self, parser):
        msg = {
            "platform": "x",
            "message_id": "9990",
            "text": "Check out our docs at https://github.com/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/info",
            "author_id": "dev",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "ambiguous")
        # Should not extract a Solana CA that is just a path segment in a URL
        assert parsed.ca_address is None

    def test_graceful_on_empty_text(self, parser):
        msg = {
            "platform": "x",
            "message_id": "0000",
            "text": "",
            "received_at": "2026-11-04T12:00:00Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "ambiguous")
        assert parsed.ticker is None
        assert parsed.ca_address is None
        assert parsed.chain_id is None

    def test_v1_3_spec_ca_soon_not_specified(self, parser):
        msg = {
            "platform": "x",
            "message_id": "11223344",
            "text": "Huge announcement for $SOL project! CA soon! Stay tuned.",
            "author_id": "crypto_alpha",
            "received_at": "2026-11-04T12:00:00Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "launch_intent")
        assert parsed.expected_ca_time_str == "Not specified"
        assert parsed.launch_time_str == "Not specified"
        assert parsed.network == "Solana"
        assert parsed.post_url == "https://x.com/crypto_alpha/status/11223344"
        assert parsed.x_profile_url == "https://x.com/crypto_alpha"

    def test_v1_3_spec_relative_ca_and_launch_time(self, parser):
        msg = {
            "platform": "x",
            "message_id": "99887766",
            "text": "Token launch scheduled for tomorrow. CA will be revealed in 2 hours. Launch tomorrow at 12 PM on Solana.",
            "author_id": "gem_project",
            "received_at": "2026-11-04T12:00:00Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "launch_intent")
        assert parsed.expected_ca_time_str == "In approximately 2 hours"
        assert "12" in parsed.launch_time_str and "PM" in parsed.launch_time_str
        assert parsed.network == "Solana"
        assert parsed.post_url == "https://x.com/gem_project/status/99887766"
        assert parsed.x_profile_url == "https://x.com/gem_project"

    def test_extract_elysium_mainnet(self, parser):
        msg = {
            "platform": "x",
            "message_id": "ely1",
            "text": "Launching $HYPEGEM on Elysium! CA soon. Official CA: 0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
            "author_id": "elysium_dev",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "ca_announcement")
        assert parsed.ticker == "HYPEGEM"
        assert parsed.chain_id == "elysium"
        assert parsed.network == "Elysium"
        assert parsed.ca_address == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"

    def test_extract_elysium_testnet(self, parser):
        msg = {
            "platform": "telegram",
            "message_id": "ely2",
            "text": "$TEST launching on Elysium Testnet. Stay tuned!",
            "author_id": "testnet_admin",
            "received_at": "2026-11-04T12:00:05Z",
            "is_forwarded": False,
        }
        parsed = parser.parse(msg, "launch_intent")
        assert parsed.ticker == "TEST"
        assert parsed.chain_id == "elysium_testnet"
        assert parsed.network == "Elysium Testnet"

    def test_different_times_and_projects(self, parser):
        # Project A: 5 PM UTC
        msg_a = {
            "platform": "x",
            "message_id": "1",
            "text": "Official launch of $ALPHA! Token launch at 5 PM UTC on Solana.",
            "author_id": "alpha_project",
            "received_at": "2026-11-04T12:00:00Z",
        }
        parsed_a = parser.parse(msg_a, "launch_intent")
        assert parsed_a.launch_time_str == "5:00 PM UTC"

        # Project B: 7:30 PM EST and CA in 45 minutes
        msg_b = {
            "platform": "x",
            "message_id": "2",
            "text": "Launch tomorrow at 7:30 PM EST on Robinhood Chain. Contract address dropping in 45 mins!",
            "author_id": "beta_token",
            "received_at": "2026-11-04T12:00:00Z",
        }
        parsed_b = parser.parse(msg_b, "launch_intent")
        assert "Tomorrow at 7:30 PM EST" in parsed_b.launch_time_str
        assert parsed_b.expected_ca_time_str == "In approximately 45 mins"

        # Project C: Relative launch time "in 3 hours"
        msg_c = {
            "platform": "x",
            "message_id": "3",
            "text": "Get ready! Token launching in 3 hours on Solana. CA soon!",
            "author_id": "gamma_token",
            "received_at": "2026-11-04T12:00:00Z",
        }
        parsed_c = parser.parse(msg_c, "launch_intent")
        assert parsed_c.launch_time_str == "In approximately 3 hours"
        assert parsed_c.expected_ca_time_str == "Not specified"

