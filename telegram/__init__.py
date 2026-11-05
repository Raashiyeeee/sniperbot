"""
telegram package shim backed by httpx for lightweight production and dev use.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import httpx


class User:
    def __init__(
        self,
        id: int,
        is_bot: bool = False,
        first_name: str = "",
        username: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.id = id
        self.is_bot = is_bot
        self.first_name = first_name
        self.username = username

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "is_bot": self.is_bot,
            "first_name": self.first_name,
            "username": self.username,
        }


class InlineKeyboardButton:
    def __init__(
        self,
        text: str,
        callback_data: Optional[str] = None,
        url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.text = text
        self.callback_data = callback_data
        self.url = url

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"text": self.text}
        if self.callback_data is not None:
            d["callback_data"] = self.callback_data
        if self.url is not None:
            d["url"] = self.url
        return d


class InlineKeyboardMarkup:
    def __init__(
        self,
        inline_keyboard: List[List[InlineKeyboardButton]],
        **kwargs: Any,
    ) -> None:
        self.inline_keyboard = inline_keyboard

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [b.to_dict() if hasattr(b, "to_dict") else b for b in row]
                for row in self.inline_keyboard
            ]
        }


class Chat:
    def __init__(
        self,
        id: int,
        type: str = "group",
        title: Optional[str] = None,
        bot_token: str = "",
        **kwargs: Any,
    ) -> None:
        self.id = id
        self.type = type
        self.title = title
        self._bot_token = bot_token

    async def send_message(
        self,
        text: str,
        reply_markup: Any = None,
        parse_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> Message:
        bot = Bot(token=self._bot_token)
        return await bot.send_message(
            chat_id=self.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            **kwargs,
        )


class Message:
    def __init__(
        self,
        message_id: int = 1,
        chat_id: int = 1,
        text: str = "",
        from_user: Optional[User] = None,
        chat: Optional[Chat] = None,
        date: Any = None,
        forward_origin: Any = None,
        caption: Optional[str] = None,
        bot_token: str = "",
        raw: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.from_user = from_user
        self.chat = chat or Chat(id=chat_id, bot_token=bot_token)
        self.date = date
        self.forward_origin = forward_origin
        self.caption = caption
        self._bot_token = bot_token
        self._raw = raw or {}

    async def edit_text(
        self,
        text: str,
        reply_markup: Any = None,
        parse_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        bot = Bot(token=self._bot_token)
        return await bot.edit_message_text(
            chat_id=self.chat_id,
            message_id=self.message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        if self._raw:
            return self._raw
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "text": self.text,
        }


class CallbackQuery:
    def __init__(
        self,
        id: str = "",
        data: str = "",
        from_user: Optional[User] = None,
        message: Optional[Message] = None,
        bot_token: str = "",
    ) -> None:
        self.id = id
        self.data = data
        self.from_user = from_user or User(id=0)
        self.message = message
        self._bot_token = bot_token

    async def answer(
        self,
        text: Optional[str] = None,
        show_alert: bool = False,
        **kwargs: Any,
    ) -> None:
        """Acknowledge the button press to Telegram."""
        if not self._bot_token or not self.id:
            return
        url = f"https://api.telegram.org/bot{self._bot_token}/answerCallbackQuery"
        payload: Dict[str, Any] = {"callback_query_id": self.id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json=payload)
        except Exception:
            pass

    async def edit_message_text(
        self,
        text: str,
        reply_markup: Any = None,
        parse_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Convenience method to edit the message the button was attached to."""
        if not self.message:
            return False
        bot = Bot(token=self._bot_token)
        return await bot.edit_message_text(
            chat_id=self.message.chat_id,
            message_id=self.message.message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            **kwargs,
        )


class Update:
    ALL_TYPES: Any = [
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "inline_query",
        "chosen_inline_result",
        "callback_query",
        "shipping_query",
        "pre_checkout_query",
        "poll",
        "poll_answer",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ]

    def __init__(
        self,
        update_id: int = 1,
        message: Optional[Message] = None,
        channel_post: Optional[Message] = None,
        callback_query: Optional[CallbackQuery] = None,
        **kwargs: Any,
    ) -> None:
        self.update_id = update_id
        self.message = message
        self.channel_post = channel_post
        self.callback_query = callback_query

    @property
    def effective_chat(self) -> Optional[Chat]:
        if self.message and self.message.chat:
            return self.message.chat
        if self.channel_post and self.channel_post.chat:
            return self.channel_post.chat
        if self.callback_query and self.callback_query.message and self.callback_query.message.chat:
            return self.callback_query.message.chat
        return None

    @property
    def effective_user(self) -> Optional[User]:
        if self.callback_query and self.callback_query.from_user:
            return self.callback_query.from_user
        if self.message and self.message.from_user:
            return self.message.from_user
        if self.channel_post and self.channel_post.from_user:
            return self.channel_post.from_user
        return None


class Bot:
    def __init__(self, token: str = "", **kwargs: Any) -> None:
        self.token = token

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Any = None,
        parse_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> Message:
        if not self.token:
            return Message(message_id=1, chat_id=chat_id, text=text)

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            if hasattr(reply_markup, "to_dict"):
                payload["reply_markup"] = reply_markup.to_dict()
            elif isinstance(reply_markup, dict):
                payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    res_data = resp.json().get("result", {})
                    msg_id = res_data.get("message_id", 1)
                    chat_dict = res_data.get("chat", {})
                    chat = Chat(id=chat_dict.get("id", chat_id), title=chat_dict.get("title"), bot_token=self.token)
                    return Message(
                        message_id=msg_id,
                        chat_id=chat_id,
                        text=text,
                        chat=chat,
                        bot_token=self.token,
                        raw=res_data,
                    )
        except Exception:
            pass

        return Message(message_id=1, chat_id=chat_id, text=text, bot_token=self.token)

    async def edit_message_text(
        self,
        chat_id: Any,
        message_id: int,
        text: str,
        reply_markup: Any = None,
        parse_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Edit an existing message on Telegram."""
        if not self.token:
            return False

        url = f"https://api.telegram.org/bot{self.token}/editMessageText"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            if hasattr(reply_markup, "to_dict"):
                payload["reply_markup"] = reply_markup.to_dict()
            elif isinstance(reply_markup, dict):
                payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception:
            return False
