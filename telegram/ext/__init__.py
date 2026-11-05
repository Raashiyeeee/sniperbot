"""
telegram.ext fallback shim backed by real async long-polling over httpx.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, List, Optional
import httpx


class ContextTypes:
    class DEFAULT_TYPE:
        error: Optional[Exception] = None

        def __init__(self, error: Optional[Exception] = None) -> None:
            self.error = error


class _Filters:
    ALL = object()
    TEXT = object()


filters = _Filters()


class CallbackQueryHandler:
    def __init__(self, callback: Callable[..., Any], pattern: Optional[str] = None) -> None:
        self.callback = callback
        self.pattern = re.compile(pattern) if pattern else None


class MessageHandler:
    def __init__(self, filters: Any, callback: Callable[..., Any]) -> None:
        self.filters = filters
        self.callback = callback


class _Updater:
    def __init__(self, app: Any) -> None:
        self._app = app
        self._running = False
        self._task: Optional[asyncio.Task[Any]] = None
        self._offset = 0

    async def start_polling(
        self,
        drop_pending_updates: bool = False,
        allowed_updates: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(drop_pending_updates))

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self, drop_pending_updates: bool) -> None:
        token = self._app.bot.token
        if not token:
            return

        async with httpx.AsyncClient(timeout=35.0) as client:
            if drop_pending_updates:
                try:
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates?offset=-1")
                    res = resp.json().get("result", [])
                    if res:
                        self._offset = res[-1]["update_id"] + 1
                except Exception:
                    pass

            while self._running:
                try:
                    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={self._offset}&timeout=15"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        for u in updates:
                            self._offset = u["update_id"] + 1
                            await self._dispatch_raw_update(u)
                    elif resp.status_code == 409:
                        # Conflict: another instance is running
                        await asyncio.sleep(5.0)
                    else:
                        await asyncio.sleep(2.0)
                except httpx.RequestError:
                    await asyncio.sleep(2.0)
                except Exception:
                    await asyncio.sleep(1.0)

    async def _dispatch_raw_update(self, raw_u: dict[str, Any]) -> None:
        from telegram import CallbackQuery, Chat, Message, Update, User

        token = self._app.bot.token

        if "callback_query" in raw_u:
            cb_data = raw_u["callback_query"]
            from_dict = cb_data.get("from", {})
            user = User(
                id=from_dict.get("id", 0),
                is_bot=from_dict.get("is_bot", False),
                first_name=from_dict.get("first_name", ""),
                username=from_dict.get("username"),
            )
            msg_dict = cb_data.get("message", {})
            chat_dict = msg_dict.get("chat", {})
            chat = Chat(
                id=chat_dict.get("id", 1),
                type=chat_dict.get("type", "group"),
                title=chat_dict.get("title"),
                bot_token=token,
            )
            msg = Message(
                message_id=msg_dict.get("message_id", 1),
                chat_id=chat.id,
                text=msg_dict.get("text", ""),
                chat=chat,
                bot_token=token,
                raw=msg_dict,
            )
            cq = CallbackQuery(
                id=cb_data.get("id", ""),
                data=cb_data.get("data", ""),
                from_user=user,
                message=msg,
                bot_token=token,
            )
            update = Update(update_id=raw_u["update_id"], callback_query=cq)

            context = ContextTypes.DEFAULT_TYPE()
            for h in self._app.handlers:
                if isinstance(h, CallbackQueryHandler):
                    if h.pattern is None or h.pattern.match(cq.data):
                        asyncio.create_task(h.callback(update, context))

        elif "message" in raw_u or "channel_post" in raw_u:
            is_post = "channel_post" in raw_u
            msg_data = raw_u["channel_post"] if is_post else raw_u["message"]
            from_dict = msg_data.get("from", {})
            user = User(
                id=from_dict.get("id", 0),
                is_bot=from_dict.get("is_bot", False),
                first_name=from_dict.get("first_name", ""),
                username=from_dict.get("username"),
            )
            chat_dict = msg_data.get("chat", {})
            chat = Chat(
                id=chat_dict.get("id", 1),
                type=chat_dict.get("type", "channel" if is_post else "group"),
                title=chat_dict.get("title"),
                bot_token=token,
            )
            msg = Message(
                message_id=msg_data.get("message_id", 1),
                chat_id=chat.id,
                text=msg_data.get("text") or msg_data.get("caption") or "",
                from_user=user if from_dict else None,
                chat=chat,
                caption=msg_data.get("caption"),
                bot_token=token,
                raw=msg_data,
            )
            update = Update(
                update_id=raw_u["update_id"],
                message=msg if not is_post else None,
                channel_post=msg if is_post else None,
            )
            context = ContextTypes.DEFAULT_TYPE()
            for h in self._app.handlers:
                if isinstance(h, MessageHandler):
                    asyncio.create_task(h.callback(update, context))


class ApplicationBuilder:
    def __init__(self) -> None:
        self._token: str = ""

    def token(self, token: str) -> ApplicationBuilder:
        self._token = token
        return self

    def build(self) -> Application:
        from telegram import Bot
        return Application(bot=Bot(self._token))


class Application:
    @classmethod
    def builder(cls) -> ApplicationBuilder:
        """Create a new ApplicationBuilder instance."""
        return ApplicationBuilder()

    def __init__(self, bot: Any = None) -> None:
        from telegram import Bot
        self.bot = bot or Bot()
        self.updater = _Updater(self)
        self.handlers: List[Any] = []
        self.error_handlers: List[Callable[..., Any]] = []

    def add_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: Callable[..., Any]) -> None:
        self.error_handlers.append(handler)

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self.updater.stop()

    async def shutdown(self) -> None:
        await self.updater.stop()
