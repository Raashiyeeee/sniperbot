"""telegram_bot/alerts.py — AlertService for Phase 1 Discovery Radar.

Sends Telegram notifications for launch detections, reminders, expiry, and CA
discovery events. No live buying is performed here.
"""

from __future__ import annotations

from typing import Optional

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import (
    Announcement,
    Project,
    TelegramNotification,
)

logger = structlog.get_logger(__name__)


class AlertService:
    """Composes and dispatches Telegram alert messages for the operator.

    All messages are sent to ``settings.telegram_alert_chat_id``. Notification
    records are persisted in the database so the pipeline can track delivery
    state and correlate callbacks with projects.
    """

    def __init__(self, bot: Bot, session: Optional[AsyncSession] = None) -> None:
        """Initialise AlertService.

        Args:
            bot:     A ``python-telegram-bot`` :class:`~telegram.Bot` instance
                     that is already authenticated.
            session: An optional active SQLAlchemy async session. If omitted or
                     closed, fresh sessions are acquired automatically.
        """
        self._bot = bot
        self._session = session
        self._log = logger.bind(service="AlertService")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_launch_detected(
        self,
        project: Project,
        announcement: Announcement,
    ) -> TelegramNotification:
        """Send a 'launch detected' alert with Accept / Reject / Details buttons.

        Composes the standardised launch-notification message and delivers it
        to the operator channel.  An inline keyboard with three buttons is
        attached so the operator can act without leaving Telegram.

        A :class:`~database.models.TelegramNotification` row is created and
        returned so callers can store the ``message_id`` for future edits.

        Args:
            project:      The newly-detected :class:`~database.models.Project`.
            announcement: The triggering :class:`~database.models.Announcement`
                          that carries platform / source-handle metadata.

        Returns:
            The persisted :class:`~database.models.TelegramNotification` row.
        """
        # Determine project display name
        if project.name and project.ticker:
            project_str = f"{project.name} (${project.ticker})"
        elif project.name:
            project_str = project.name
        elif project.ticker:
            project_str = f"${project.ticker}"
        else:
            project_str = "Example Token"

        # Determine announcement line
        raw_text = (announcement.text or project.description or "").strip()
        announcement_summary = (
            getattr(announcement, "announcement_summary", None)
            or (raw_text.split("\n")[0] if raw_text else "Token launch scheduled.")
        )
        if len(announcement_summary) > 200:
            announcement_summary = announcement_summary[:197] + "..."

        # Expected CA
        expected_ca = (
            getattr(announcement, "expected_ca_time_str", None)
            or getattr(project, "expected_ca_time_str", None)
            or "Not specified"
        )

        # Launch time
        launch_time = (
            getattr(announcement, "launch_time_str", None)
            or getattr(project, "launch_time_str", None)
            or "Not specified"
        )

        # X Post link
        post_url = getattr(announcement, "post_url", None)
        if not post_url or post_url == "N/A":
            msg_id = getattr(announcement, "message_id", "")
            author = getattr(announcement, "author_id", "") or project.official_x_handle or ""
            clean_author = author.lstrip("@")
            if clean_author and msg_id:
                post_url = f"https://x.com/{clean_author}/status/{msg_id}"
            elif msg_id:
                post_url = f"https://x.com/i/web/status/{msg_id}"
            else:
                post_url = "Not available"

        # X Profile link
        x_profile = getattr(announcement, "x_profile_url", None)
        if not x_profile:
            author = getattr(announcement, "author_id", "") or project.official_x_handle or ""
            clean_author = author.lstrip("@")
            if clean_author:
                x_profile = f"https://x.com/{clean_author}"
            else:
                x_profile = "Not available"

        # Network (Solana, Robinhood Chain, or Elysium)
        network = (
            getattr(announcement, "network", None)
            or getattr(project, "network", None)
            or (project.chain_id.capitalize() if project.chain_id else None)
            or "Solana"
        )
        net_lower = network.lower().replace("_", " ")
        if "elysium testnet" in net_lower:
            network = "Elysium Testnet"
        elif "elysium" in net_lower:
            network = "Elysium"
        elif "sol" in net_lower:
            network = "Solana"
        elif "robinhood" in net_lower or "rh" in net_lower or "evm" in net_lower:
            network = "Robinhood Chain"

        # Check if project has AI intelligence results
        ai_scores = project.ai_scores_breakdown or {}
        v_flags = project.verification_flags or {}
        rec = project.ai_recommendation or "REVIEW"
        overall = project.ai_score if project.ai_score is not None else (project.score or 0.0)

        if project.ai_scores_breakdown:
            util_s = int(ai_scores.get("product_utility", 0))
            cred_s = int(ai_scores.get("project_credibility", 0))
            comm_s = int(ai_scores.get("community_quality", 0))
            acct_s = int(ai_scores.get("x_account_credibility", 0))
            conf_s = int(ai_scores.get("token_launch_confirmation", 0))

            website_ver = v_flags.get("official_website", "Unknown")
            product_ver = v_flags.get("product_existence", "Unknown")
            x_ver = v_flags.get("official_x_account", "Unknown")
            team_ver = v_flags.get("team_association", "Unknown")
            comm_ver = v_flags.get("community_activity", "Unknown")
            launch_ver = v_flags.get("token_launch_announcement", "Unknown")

            red_flags = project.red_flags or []
            red_flags_str = "\n".join(f"- {rf}" for rf in red_flags) if red_flags else "None detected"
            missing_info = project.missing_information or []
            missing_str = "\n".join(f"- {m}" for m in missing_info) if missing_info else "None"

            category_str = project.category or "Utility"
            website_str = project.website_url or "Not specified"
            tg_str = project.official_telegram or "Not specified"

            text = (
                "🚀 NEW PROJECT DETECTED\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "PROJECT INFORMATION\n\n"
                f"Name: {project_str}\n"
                f"Category: {category_str}\n"
                f"Blockchain: {network}\n\n"
                f"Website: {website_str}\n"
                f"X Account: {x_profile}\n"
                f"Telegram: {tg_str}\n\n"
                f"Original Announcement:\n{post_url}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "AI PROJECT INTELLIGENCE\n\n"
                f"Product Utility: {util_s}/100\n"
                f"Project Credibility: {cred_s}/100\n"
                f"Community Quality: {comm_s}/100\n"
                f"X Account Credibility: {acct_s}/100\n"
                f"Token Launch Confirmation: {conf_s}/100\n\n"
                f"OVERALL QUALITY: {int(overall)}/100\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "VERIFICATION\n\n"
                f"[{website_ver}] Official website\n"
                f"[{product_ver}] Product existence\n"
                f"[{x_ver}] Official X account\n"
                f"[{team_ver}] Team association\n"
                f"[{comm_ver}] Community activity\n"
                f"[{launch_ver}] Token launch announcement\n\n"
                f"RED FLAGS:\n{red_flags_str}\n\n"
                f"MISSING INFORMATION:\n{missing_str}\n\n"
                "CONTRACT ADDRESS: NOT YET AVAILABLE\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"AI RECOMMENDATION: [{rec}]"
            )
        else:
            text = (
                "🚨 New launch announcement detected\n"
                "Filtered\n"
                "\n"
                f"Project: {project_str}\n"
                f"Announcement: {announcement_summary}\n"
                f"Expected CA: {expected_ca}\n"
                f"Launch time: {launch_time}\n"
                f"X Post: {post_url}\n"
                f"X Profile: {x_profile}\n"
                f"Network: {network}"
            )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✓ Accept",
                        callback_data=f"approve:{project.id}",
                    ),
                    InlineKeyboardButton(
                        "✕ Reject",
                        callback_data=f"reject:{project.id}",
                    ),
                    InlineKeyboardButton(
                        "📋 Details",
                        callback_data=f"details:{project.id}",
                    ),
                ]
            ]
        )

        self._log.info(
            "sending_launch_detected",
            project_id=str(project.id),
            chat_id=settings.telegram_alert_chat_id,
        )

        message = await self._bot.send_message(
            chat_id=settings.telegram_alert_chat_id,
            text=text,
            reply_markup=keyboard,
        )

        notification = TelegramNotification(
            project_id=project.id,
            message_id=message.message_id,
            chat_id=str(message.chat_id),
            message_text=text,
            notification_type="launch_detected",
        )

        session = self._session
        should_close = False
        if session is None or not getattr(session, "is_active", True):
            from database.connection import AsyncSessionLocal
            session = AsyncSessionLocal()
            should_close = True

        try:
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
        finally:
            if should_close:
                await session.close()

        self._log.info(
            "launch_detected_sent",
            project_id=str(project.id),
            message_id=message.message_id,
            notification_id=str(notification.id),
        )
        return notification

    async def send_reminder(
        self,
        project: Project,
        hours_elapsed: float,
        hours_until_expiry: float,
    ) -> None:
        """Send a reminder that a project's CA has not yet been seen.

        Args:
            project:            The project still awaiting a CA.
            hours_elapsed:      Hours since the project was first detected.
            hours_until_expiry: Hours remaining before the approval window
                                expires.
        """
        text = (
            f"⏰ REMINDER: {project.name} CA not yet seen. "
            f"{hours_elapsed}h elapsed, {hours_until_expiry}h until expiry."
        )
        self._log.info(
            "sending_reminder",
            project_id=str(project.id),
            hours_elapsed=hours_elapsed,
            hours_until_expiry=hours_until_expiry,
        )
        await self._bot.send_message(
            chat_id=settings.telegram_alert_chat_id,
            text=text,
        )

    async def send_expired(self, project: Project) -> None:
        """Notify the operator that a project has moved to Expired status.

        Args:
            project: The project whose CA-monitoring window has closed.
        """
        text = (
            f"⌛ EXPIRED: {project.name} moved to Expired status. "
            "No CA detected within the timeout window."
        )
        self._log.info("sending_expired", project_id=str(project.id))
        await self._bot.send_message(
            chat_id=settings.telegram_alert_chat_id,
            text=text,
        )

    async def send_ca_detected(
        self,
        project: Project,
        ca_address: str,
        chain_id: str,
    ) -> None:
        """Send a high-priority second notification when a contract address is found.

        Args:
            project:    The project whose CA has just been identified.
            ca_address: The detected contract address.
            chain_id:   The chain on which the contract was deployed.
        """
        text = (
            f"🎯 CA DETECTED — {project.name}\n"
            "\n"
            f"CA:     {ca_address}\n"
            f"Chain:  {chain_id}\n"
            "Source: Verified ✅\n"
            "\n"
            "Running checks..."
        )
        self._log.info(
            "sending_ca_detected",
            project_id=str(project.id),
            ca_address=ca_address,
            chain_id=chain_id,
        )
        await self._bot.send_message(
            chat_id=settings.telegram_alert_chat_id,
            text=text,
        )
