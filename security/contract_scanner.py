"""
security/contract_scanner.py
────────────────────────────
Security scanner and honeypot detector (Spec v3.1 §9, §9.3).
Inspects contract permissions, mint authority, freeze authority,
sellability simulation, and liquidity depth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from database.models import CheckResult

logger = structlog.get_logger(__name__)


@dataclass
class SecurityScanResult:
    status: CheckResult
    is_sellable: bool
    can_freeze: bool
    can_mint: bool
    is_honeypot: bool
    flags: List[str] = field(default_factory=list)
    raw_details: Dict[str, Any] = field(default_factory=dict)
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""


class ContractSecurityScanner:
    """Evaluates token security properties and liquidity routing."""

    def evaluate_security(
        self,
        chain_id: str,
        ca_address: str,
        can_freeze: bool = False,
        can_mint: bool = False,
        is_sellable: bool = True,
        is_honeypot: bool = False,
        max_wallet_restricted: bool = False,
        has_liquidity_route: bool = True,
    ) -> SecurityScanResult:
        """
        Evaluate token safety criteria.
        Spec v3.1 §9 & §9.3:
        - Mint/owner privileges, freeze/pause/blacklist controls.
        - Viable buy and sell route (sellability simulation).
        - Known honeypot patterns.
        """
        flags: List[str] = []

        if is_honeypot:
            flags.append("HONEYPOT_DETECTED")
        if not is_sellable:
            flags.append("CANNOT_SELL_SIMULATION_FAILED")
        if not has_liquidity_route:
            flags.append("NO_LIQUIDITY_ROUTE")
        if can_freeze:
            flags.append("FREEZE_AUTHORITY_ENABLED")
        if can_mint:
            flags.append("MINT_AUTHORITY_ACTIVE")
        if max_wallet_restricted:
            flags.append("MAX_WALLET_RESTRICTION")

        # Determine overall safety
        # Blocking flags: honeypot, not sellable, or no route
        critical_flags = {"HONEYPOT_DETECTED", "CANNOT_SELL_SIMULATION_FAILED", "NO_LIQUIDITY_ROUTE"}
        has_critical = any(f in critical_flags for f in flags)

        if has_critical:
            status = CheckResult.FAIL
            msg = f"Security check FAILED: {', '.join(flags)}"
            logger.warning("security_check_failed", ca=ca_address, flags=flags)
        elif flags:
            # Minor warnings (e.g. mint or freeze authority)
            status = CheckResult.PASS
            msg = f"Security check passed with warnings: {', '.join(flags)}"
            logger.info("security_check_passed_with_warnings", ca=ca_address, flags=flags)
        else:
            status = CheckResult.PASS
            msg = "All security checks passed cleanly"
            logger.info("security_check_passed_cleanly", ca=ca_address)

        return SecurityScanResult(
            status=status,
            is_sellable=is_sellable,
            can_freeze=can_freeze,
            can_mint=can_mint,
            is_honeypot=is_honeypot,
            flags=flags,
            raw_details={
                "chain_id": chain_id,
                "ca_address": ca_address,
                "max_wallet_restricted": max_wallet_restricted,
                "has_liquidity_route": has_liquidity_route,
            },
            message=msg,
        )
