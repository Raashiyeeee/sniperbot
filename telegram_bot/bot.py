"""telegram_bot/bot.py — TelegramBot for Phase 1 Discovery Radar.

Handles operator callbacks (approve / reject / details) from Telegram inline
keyboards.  Only authorised operator IDs (``settings.operator_ids``) may
trigger state-changing actions; all other presses are logged and silently
refused.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Optional

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    BlacklistEntry,
    Project,
    ProjectStatus,
)
from projects.account_manager import AccountManager

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


class TelegramBot:
    """Telegram bot that routes operator callback decisions into the pipeline.

    Inline keyboard callbacks emitted by :class:`~telegram_bot.alerts.AlertService`
    are received here.  Each handler validates operator identity, updates
    project state, edits the Telegram message to reflect the new state,
    and pushes events into the shared asyncio queue so the rest of the pipeline
    can react (e.g. start CA monitoring on approval).

    Args:
        event_queue:       Asyncio queue shared with the rest of the pipeline.
                           Approved-project events are pushed here.
        AsyncSessionLocal: SQLAlchemy async session factory used to obtain a
                           fresh DB session per request.
    """

    def __init__(
        self,
        event_queue: asyncio.Queue,  # type: ignore[type-arg]
        AsyncSessionLocal: Callable[[], AsyncSession],
    ) -> None:
        self._queue = event_queue
        self._AsyncSessionLocal = AsyncSessionLocal
        self._log = logger.bind(service="TelegramBot")

        self._app: Application = (
            ApplicationBuilder()
            .token(settings.telegram_bot_token)
            .build()
        )
        self._register_handlers()

    @property
    def bot(self):
        """Expose underlying telegram.Bot instance."""
        return self._app.bot

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Attach callback-query and command handlers to the Application."""
        self._app.add_handler(
            CallbackQueryHandler(self._dispatch_callback, pattern=r"^(approve|reject|details|noop)(:.*)?$")
        )
        self._app.add_handler(CommandHandler("setaccounts", self._cmd_setaccounts))
        self._app.add_handler(CommandHandler("set_x", self._cmd_set_x))
        self._app.add_handler(CommandHandler("set_tg", self._cmd_set_tg))
        self._app.add_handler(CommandHandler("refresh_admins", self._cmd_refresh_admins))
        self._app.add_handler(CommandHandler("help_accounts", self._cmd_help_accounts))

    # ------------------------------------------------------------------
    # Dispatch & authorisation
    # ------------------------------------------------------------------

    async def _dispatch_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Route an incoming callback query after authorisation check.

        Parses the ``callback_data`` into an action and project ID, verifies
        the caller is an authorised operator, then delegates to the appropriate
        private handler.
        """
        query = update.callback_query
        if query is None or query.data is None:
            return

        operator_id: int = query.from_user.id
        data: str = query.data

        if data == "noop":
            await query.answer("This project decision has already been finalized.", show_alert=False)
            return

        action, _, project_id_str = data.partition(":")

        log = self._log.bind(operator_id=operator_id, action=action, project_id=project_id_str)

        # ── Authorisation ──────────────────────────────────────────────
        if operator_id not in settings.operator_ids:
            log.warning("unauthorized_callback_tap")
            await query.answer("⛔ Unauthorized")
            async with self._AsyncSessionLocal() as session:
                await self._write_audit(
                    session=session,
                    action="unauthorized_button_tap",
                    operator_id=operator_id,
                    project_id=project_id_str,
                    detail=f"Unauthorized tap on action={action}",
                )
                await session.commit()
            return

        # ── Authorised test path ───────────────────────────────────────
        if project_id_str in ("test-uuid", "test"):
            await self._handle_test_callback(action, operator_id, query, update)
            return

        try:
            uuid.UUID(project_id_str)
        except ValueError:
            log.error("invalid_project_id_in_callback")
            await query.answer("⚠️ Invalid project reference.")
            return

        async with self._AsyncSessionLocal() as session:
            project = await session.get(Project, project_id_str)
            if project is None:
                log.error("project_not_found")
                await query.answer("⚠️ Project not found.")
                return

            if action == "approve":
                await self._handle_approve(project, operator_id, session, query)
            elif action == "reject":
                await self._handle_reject(project, operator_id, session, query)
            elif action == "details":
                await self._handle_details(project, operator_id, session, query, update)
            else:
                log.warning("unknown_action")
                await query.answer("⚠️ Unknown action.")

    # ------------------------------------------------------------------
    # Test action handler
    # ------------------------------------------------------------------

    async def _handle_test_callback(
        self,
        action: str,
        operator_id: int,
        query: Any,
        update: Update,
    ) -> None:
        """Handle test alert buttons (e.g. from scripts/test_telegram.py)."""
        if action == "approve":
            approved_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 DETAILS", callback_data="details:test-uuid")]
            ])
            text = (
                "✅ LAUNCH TEST — APPROVED\n\n"
                "Project:       StarToken ($STAR)\n"
                "Chain:         Robinhood Chain (EVM)\n"
                "Source:        X — https://x.com/StarTokenDev\n"
                f"Status:        APPROVED by operator {operator_id}\n\n"
                "🔍 Monitoring for official CA drop...\n"
                "Fast-path buy will trigger automatically upon validation."
            )
            await self._edit_query_message(query, text, approved_markup)
            await query.answer("✅ [TEST] Approved! Pipeline moves to CA monitoring.")

        elif action == "reject":
            disabled_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 REJECTED & BLACKLISTED", callback_data="noop")]
            ])
            text = (
                "❌ LAUNCH TEST — REJECTED & BLACKLISTED\n\n"
                "Project:       StarToken ($STAR)\n"
                "Chain:         Robinhood Chain (EVM)\n"
                "Source:        X — https://x.com/StarTokenDev\n"
                f"Status:        REJECTED & BLACKLISTED by operator {operator_id}\n\n"
                "🚫 This token and its social handles have been blacklisted."
            )
            await self._edit_query_message(query, text, disabled_markup)
            await query.answer("❌ [TEST] Rejected! Project added to blacklist.")

        elif action == "details":
            details = (
                "📋 Test Project Details\n"
                "────────────────────────\n"
                "Name:            StarToken ($STAR)\n"
                "Chain:           Robinhood Chain (EVM)\n"
                "Official Handle: @StarTokenDev\n"
                "Profile:         https://x.com/StarTokenDev\n"
                f"Max Buy Tax:     {settings.max_buy_tax_pct}%\n"
                f"Max Slippage:    {settings.evm_max_slippage_pct}%\n"
                f"Buy Amount:      {settings.elysium_buy_amount_hype} HYPE\n"
                f"Min Liquidity:   ${settings.min_qualifying_volume_usd}\n"
                f"Target Execution: {settings.ca_execution_target_min_ms}-{settings.ca_execution_target_max_ms}ms\n"
                "Selling:         Manual only (TP/SL/auto OFF)\n"
            )
            chat_to_reply = update.effective_chat or (query.message.chat if query.message else None)
            if chat_to_reply:
                await chat_to_reply.send_message(text=details)
            elif query.message:
                await self.bot.send_message(chat_id=query.message.chat_id, text=details)
            await query.answer("📋 Details sent.")

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_approve(
        self,
        project: Project,
        operator_id: int,
        session: AsyncSession,
        query: Any,
    ) -> None:
        """Approve a project, edit Telegram message, and queue for CA monitoring."""
        log = self._log.bind(project_id=str(project.id), operator_id=operator_id)

        # Idempotency guard for repeated clicks
        if project.status == ProjectStatus.APPROVED:
            await query.answer("✓ Project is already approved and in CA monitoring.", show_alert=False)
            return
        if project.status == ProjectStatus.REJECTED:
            await query.answer("✕ Project has already been rejected and blacklisted.", show_alert=False)
            return

        log.info("approving_project")

        project.status = ProjectStatus.APPROVED  # type: ignore[assignment]

        approval = Approval(
            project_id=project.id,
            operator_id=str(operator_id),
            decision=ApprovalDecision.APPROVED,
        )
        session.add(approval)

        await self._write_audit(
            session=session,
            action="project_approved",
            operator_id=operator_id,
            project_id=str(project.id),
            detail=f"Project '{project.name}' approved by operator {operator_id}",
        )

        # Register any already-known official accounts for continuous monitoring
        acct_mgr = AccountManager(session, bot=self.bot)
        try:
            await acct_mgr.bootstrap_from_project(project, str(operator_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("approve_account_bootstrap_failed", error=str(exc))

        await session.commit()

        x_handle = f"@{project.official_x_handle}" if project.official_x_handle else "not set"
        tg_ref = (
            project.official_telegram_chat_id
            or (f"@{project.official_telegram}" if project.official_telegram else "not set")
        )
        # Update Telegram message visibly
        new_text = (
            "✅ LAUNCH APPROVED — MONITORING FOR CA\n\n"
            f"Project:       {project.name or 'Unknown'} ({project.ticker or 'N/A'})\n"
            f"Chain:         {project.chain_id or 'Unknown'}\n"
            f"Status:        APPROVED\n"
            f"Operator:      {operator_id}\n"
            f"Official X:    {x_handle}\n"
            f"Official TG:   {tg_ref}\n\n"
            "🔍 Official channels are now actively monitored for CA drop.\n"
            "Fast-path buy will trigger automatically upon validation.\n\n"
            "Attach / update official accounts:\n"
            f"/set_x {project.id} @OfficialHandle\n"
            f"/set_tg {project.id} @channel_or_-100chatid\n"
            f"/setaccounts {project.id} x:@handle tg:@channel"
        )
        approved_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 DETAILS", callback_data=f"details:{project.id}")]
        ])
        await self._edit_query_message(query, new_text, approved_markup)

        await query.answer("✅ Project approved. Monitoring for CA...")
        await self._queue.put({"event": "approved", "project_id": str(project.id)})
        log.info("project_approved_and_queued")

    async def _handle_reject(
        self,
        project: Project,
        operator_id: int,
        session: AsyncSession,
        query: Any,
    ) -> None:
        """Reject a project, edit Telegram message, and add to blacklist."""
        log = self._log.bind(project_id=str(project.id), operator_id=operator_id)

        # Idempotency guard for repeated clicks
        if project.status == ProjectStatus.REJECTED:
            await query.answer("✕ Project has already been rejected and blacklisted.", show_alert=False)
            return
        if project.status == ProjectStatus.APPROVED:
            await query.answer("✓ Project is already approved.", show_alert=False)
            return

        log.info("rejecting_project")

        project.status = ProjectStatus.REJECTED  # type: ignore[assignment]
        project.is_blacklisted = True

        approval = Approval(
            project_id=project.id,
            operator_id=str(operator_id),
            decision=ApprovalDecision.REJECTED,
        )
        session.add(approval)

        blacklist_entry = BlacklistEntry(
            project_id=project.id,
            reason=f"Rejected by operator {operator_id}",
            created_by=str(operator_id),
        )
        session.add(blacklist_entry)

        await self._write_audit(
            session=session,
            action="project_rejected",
            operator_id=operator_id,
            project_id=str(project.id),
            detail=f"Project '{project.name}' rejected and blacklisted by operator {operator_id}",
        )

        await session.commit()

        # Update Telegram message visibly
        new_text = (
            "❌ LAUNCH REJECTED & BLACKLISTED\n\n"
            f"Project:       {project.name or 'Unknown'} ({project.ticker or 'N/A'})\n"
            f"Chain:         {project.chain_id or 'Unknown'}\n"
            f"Status:        REJECTED & BLACKLISTED\n"
            f"Operator:      {operator_id}\n\n"
            "🚫 This token and its social handles have been blacklisted."
        )
        disabled_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 REJECTED & BLACKLISTED", callback_data="noop")]
        ])
        await self._edit_query_message(query, new_text, disabled_markup)

        await query.answer("❌ Project rejected and blacklisted.")
        log.info("project_rejected_and_blacklisted")

    async def _handle_details(
        self,
        project: Project,
        operator_id: int,
        session: AsyncSession,
        query: Any,
        update: Update,
    ) -> None:
        """Reply with the full project details as a Telegram text message."""
        log = self._log.bind(project_id=str(project.id), operator_id=operator_id)
        log.info("sending_details")

        details = (
            f"📋 Project Intelligence Details\n"
            f"───────────────────────────────\n"
            f"ID:              {project.id}\n"
            f"Name:            {project.name or 'N/A'}\n"
            f"Ticker:          {project.ticker or 'N/A'}\n"
            f"Category:        {project.category or 'Utility'}\n"
            f"Chain:           {project.chain_id or 'Unknown'}\n"
            f"Status:          {project.status}\n"
            f"Overall Quality: {project.ai_score if project.ai_score is not None else (project.score or 'N/A')}/100\n"
            f"Recommendation:  {project.ai_recommendation or 'N/A'}\n"
            f"Website:         {project.website_url or 'N/A'}\n"
            f"Official X:      {project.official_x_handle or 'N/A'}\n"
            f"Official TG:     {project.official_telegram or 'N/A'}\n"
            f"TG Chat ID:      {project.official_telegram_chat_id or 'N/A'}\n"
            f"Expected launch: {project.expected_launch_at or project.launch_time_str or 'Not stated'}\n"
            f"CA Expected:     {project.expected_ca_time_str or 'Not stated'}\n"
            f"Blacklisted:     {project.is_blacklisted}\n"
        )
        if project.ai_scores_breakdown:
            details += (
                f"\n📊 Sub-scores:\n"
                f"- Product Utility: {project.ai_scores_breakdown.get('product_utility', 'N/A')}/100\n"
                f"- Credibility:     {project.ai_scores_breakdown.get('project_credibility', 'N/A')}/100\n"
                f"- Community:       {project.ai_scores_breakdown.get('community_quality', 'N/A')}/100\n"
                f"- X Account:       {project.ai_scores_breakdown.get('x_account_credibility', 'N/A')}/100\n"
                f"- Confirmation:    {project.ai_scores_breakdown.get('token_launch_confirmation', 'N/A')}/100\n"
                f"- Ecosystem:       {project.ai_scores_breakdown.get('ecosystem_presence', 'N/A')}/100\n"
            )
        if project.red_flags:
            details += f"\n🚩 Red Flags: {', '.join(project.red_flags)}\n"

        chat_to_reply = update.effective_chat or (query.message.chat if query.message else None)
        if chat_to_reply is not None:
            await chat_to_reply.send_message(text=details)
        elif query.message:
            await self.bot.send_message(chat_id=query.message.chat_id, text=details)

        await self._write_audit(
            session=session,
            action="project_details_requested",
            operator_id=operator_id,
            project_id=str(project.id),
            detail=f"Operator {operator_id} requested details for project '{project.name}'",
        )
        await session.commit()

        await query.answer("📋 Details sent.")
        log.info("details_sent")

    async def _edit_query_message(
        self,
        query: Any,
        text: str,
        reply_markup: Any = None,
        parse_mode: str | None = None,
    ) -> None:
        """Safely edit the Telegram message attached to a callback query."""
        try:
            if hasattr(query, "edit_message_text"):
                await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
            elif query.message:
                await self.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
        except Exception as exc:
            self._log.warning("failed_to_edit_message", error=str(exc))

    # ------------------------------------------------------------------
    # Account attachment commands
    # ------------------------------------------------------------------

    async def _require_operator(self, update: Update) -> Optional[int]:
        user = update.effective_user
        if user is None or user.id not in settings.operator_ids:
            if update.effective_message:
                await update.effective_message.reply_text("⛔ Unauthorized")
            return None
        return user.id

    async def _cmd_help_accounts(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if await self._require_operator(update) is None:
            return
        text = (
            "Official account commands:\n\n"
            "/set_x <project_id|ticker> @handle\n"
            "/set_tg <project_id|ticker> @channel|-100chatid\n"
            "/setaccounts <project_id|ticker> x:@handle tg:@channel\n"
            "/refresh_admins <project_id|ticker>\n\n"
            "After Accept, attach the official X and Telegram so CA monitoring "
            "and auto-buy only trigger from those authorized sources."
        )
        if update.effective_message:
            await update.effective_message.reply_text(text)

    async def _cmd_set_x(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        operator_id = await self._require_operator(update)
        if operator_id is None:
            return
        args = context.args or []
        if len(args) < 2:
            if update.effective_message:
                await update.effective_message.reply_text("Usage: /set_x <project_id|ticker> @handle")
            return
        await self._apply_account_update(
            update,
            operator_id,
            project_ref=args[0],
            x_handle=args[1],
            telegram_ref=None,
        )

    async def _cmd_set_tg(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        operator_id = await self._require_operator(update)
        if operator_id is None:
            return
        args = context.args or []
        if len(args) < 2:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Usage: /set_tg <project_id|ticker> @channel_or_-100chatid"
                )
            return
        await self._apply_account_update(
            update,
            operator_id,
            project_ref=args[0],
            x_handle=None,
            telegram_ref=args[1],
        )

    async def _cmd_setaccounts(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        operator_id = await self._require_operator(update)
        if operator_id is None:
            return
        args = context.args or []
        if len(args) < 2:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Usage: /setaccounts <project_id|ticker> x:@handle tg:@channel"
                )
            return

        project_ref = args[0]
        x_handle = None
        telegram_ref = None
        for token in args[1:]:
            lower = token.lower()
            if lower.startswith("x:"):
                x_handle = token.split(":", 1)[1]
            elif lower.startswith("tg:"):
                telegram_ref = token.split(":", 1)[1]
            elif token.startswith("@"):
                # Ambiguous bare @ — treat as X if x not set else TG
                if x_handle is None:
                    x_handle = token
                else:
                    telegram_ref = token
            elif token.startswith("-") or token.isdigit():
                telegram_ref = token

        if not x_handle and not telegram_ref:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Provide x:@handle and/or tg:@channel (or chat id)."
                )
            return

        await self._apply_account_update(
            update,
            operator_id,
            project_ref=project_ref,
            x_handle=x_handle,
            telegram_ref=telegram_ref,
        )

    async def _cmd_refresh_admins(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        operator_id = await self._require_operator(update)
        if operator_id is None:
            return
        args = context.args or []
        if len(args) < 1:
            if update.effective_message:
                await update.effective_message.reply_text("Usage: /refresh_admins <project_id|ticker>")
            return

        async with self._AsyncSessionLocal() as session:
            mgr = AccountManager(session, bot=self.bot)
            project = await mgr.resolve_project(args[0])
            if project is None:
                if update.effective_message:
                    await update.effective_message.reply_text("⚠️ Project not found.")
                return
            try:
                admins = await mgr.refresh_telegram_admins(project, str(operator_id))
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                if update.effective_message:
                    await update.effective_message.reply_text(f"⚠️ Failed: {exc}")
                return

        if update.effective_message:
            await update.effective_message.reply_text(
                f"✅ Refreshed {len(admins)} Telegram admin(s) for "
                f"{project.name or project.ticker or project.id}."
            )

    async def _apply_account_update(
        self,
        update: Update,
        operator_id: int,
        project_ref: str,
        x_handle: Optional[str],
        telegram_ref: Optional[str],
    ) -> None:
        async with self._AsyncSessionLocal() as session:
            mgr = AccountManager(session, bot=self.bot)
            project = await mgr.resolve_project(project_ref)
            if project is None:
                if update.effective_message:
                    await update.effective_message.reply_text("⚠️ Project not found.")
                return
            try:
                await mgr.set_accounts(
                    project,
                    operator_id=str(operator_id),
                    x_handle=x_handle,
                    telegram_ref=telegram_ref,
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                if update.effective_message:
                    await update.effective_message.reply_text(f"⚠️ Failed: {exc}")
                return

            x_str = f"@{project.official_x_handle}" if project.official_x_handle else "unchanged"
            tg_str = (
                project.official_telegram_chat_id
                or (f"@{project.official_telegram}" if project.official_telegram else "unchanged")
            )
            if update.effective_message:
                await update.effective_message.reply_text(
                    "✅ Official accounts updated\n"
                    f"Project: {project.name or project.ticker or project.id}\n"
                    f"X: {x_str}\n"
                    f"TG: {tg_str}\n"
                    "Monitoring is active for these sources only."
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _write_audit(
        session: AsyncSession,
        action: str,
        operator_id: int | None,
        project_id: str | None,
        detail: str = "",
    ) -> None:
        """Persist an ``AuditEvent`` record (does NOT commit the session)."""
        event = AuditEvent(
            action=action,
            actor=str(operator_id) if operator_id is not None else "system",
            entity_id=project_id,
            detail=detail,
        )
        session.add(event)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Telegram bot and begin polling for updates."""
        self._log.info("starting_telegram_bot")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self._log.info("telegram_bot_polling")

    async def stop(self) -> None:
        """Gracefully stop polling and shut down the Application."""
        self._log.info("stopping_telegram_bot")
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        self._log.info("telegram_bot_stopped")
