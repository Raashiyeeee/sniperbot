"""
monitoring/ca_extractor.py
──────────────────────────
Extracts, sanitizes, and normalizes Contract Addresses (EVM) and Mint Addresses (Solana).
Spec v3.1 §8.
"""
from __future__ import annotations

import re
from typing import List, Optional

from config.chains import is_valid_evm_address, is_valid_solana_address

# Regex for EVM address (0x + 40 hex chars)
_EVM_PATTERN = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")

# Regex for Solana base58 mint address (32-44 base58 characters)
# Excludes typical URL paths and hex strings
_SOL_PATTERN = re.compile(r"(?<![/\w])([1-9A-HJ-NP-Za-km-z]{32,44})(?![/\w])")

# Common words that might look like base58 strings but are not mint addresses
_SOL_STOPWORDS = {
    "solana", "telegram", "announcement", "launching", "tomorrow",
    "contract", "address", "dexscreener", "pumpfun", "raydium",
}


class CAExtractor:
    """Extracts candidate contract/mint addresses from message text."""

    @staticmethod
    def extract_evm_candidates(text: str) -> List[str]:
        """Find all valid EVM addresses in text."""
        if not text:
            return []
        matches = _EVM_PATTERN.findall(text)
        # Deduplicate while preserving order
        seen = set()
        candidates = []
        for m in matches:
            norm = m.lower()
            if norm not in seen and is_valid_evm_address(norm):
                seen.add(norm)
                candidates.append(m)  # keep original casing or lowercase
        return candidates

    @staticmethod
    def extract_solana_candidates(text: str) -> List[str]:
        """Find all valid Solana mint addresses in text."""
        if not text:
            return []
        # Strip URLs to avoid extracting path segments
        clean_text = re.sub(r"https?://\S+", " ", text)
        matches = _SOL_PATTERN.findall(clean_text)
        seen = set()
        candidates = []
        for m in matches:
            if m.lower() in _SOL_STOPWORDS:
                continue
            if m not in seen and is_valid_solana_address(m):
                seen.add(m)
                candidates.append(m)
        return candidates

    @classmethod
    def extract_for_chain(cls, text: str, chain_id: Optional[str]) -> List[str]:
        """Extract addresses according to expected chain, or both if chain is unknown."""
        if chain_id == "robinhood":
            return cls.extract_evm_candidates(text)
        elif chain_id == "solana":
            return cls.extract_solana_candidates(text)
        else:
            # Try EVM first, then Solana
            evm = cls.extract_evm_candidates(text)
            if evm:
                return evm
            return cls.extract_solana_candidates(text)
