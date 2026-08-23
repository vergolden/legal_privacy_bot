#!/usr/bin/env python3
"""Общий клиент Telegram Bot API для скриптов Legal Privacy.

Раньше load_env/https_context/отправка запроса были продублированы в
send_telegram.py, publish_telegram.py и bot_listener.py по отдельности —
собраны здесь в одном месте. Без сторонних библиотек, как и остальные
скрипты проекта (только стандартная библиотека Python).
"""
import json
import mimetypes
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024  # жёсткий лимит Telegram Bot API на подпись к фото


def load_env(env_path):
    env = dict(os.environ)
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def https_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class TelegramError(RuntimeError):
    """Ошибка вызова Telegram Bot API (HTTP-уровня или ok:false в ответе)."""


class TelegramAPI:
    """Тонкая обёртка над Telegram Bot API: один токен — набор методов."""

    def __init__(self, token):
        self.token = token
        self.base = f"{TELEGRAM_API_BASE}/bot{token}"

    def _call_form(self, method, data_bytes, content_type, timeout=60):
        req = urllib.request.Request(
            f"{self.base}/{method}",
            data=data_bytes,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=https_context()) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise TelegramError(f"HTTP-ошибка Telegram API [{method}] ({e.code}): {body}") from None
        if not result.get("ok"):
            raise TelegramError(f"Ошибка Telegram API [{method}]: {result}")
        return result["result"]

    def call(self, method, timeout=60, **params):
        data = urllib.parse.urlencode(params).encode("utf-8")
        return self._call_form(method, data, "application/x-www-form-urlencoded", timeout=timeout)

    def get_chat(self, chat_id):
        return self.call("getChat", chat_id=chat_id)

    def get_updates(self, offset, poll_timeout=30):
        params = {
            "offset": offset,
            "timeout": poll_timeout,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        data = urllib.parse.urlencode(params).encode("utf-8")
        return self._call_form(
            "getUpdates", data, "application/x-www-form-urlencoded", timeout=poll_timeout + 10
        )

    def send_message(self, chat_id, text, reply_markup=None, disable_preview=False):
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup)
        if disable_preview:
            params["link_preview_options"] = json.dumps({"is_disabled": True})
        return self.call("sendMessage", **params)

    def send_photo(self, chat_id, image_path, caption=None, reply_markup=None):
        boundary = uuid.uuid4().hex
        filename = os.path.basename(image_path)
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"

        with open(image_path, "rb") as f:
            file_data = f.read()

        body = bytearray()

        def add_field(name, value):
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        add_field("chat_id", chat_id)
        if caption:
            add_field("caption", caption)
        if reply_markup is not None:
            add_field("reply_markup", json.dumps(reply_markup))

        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode())
        body.extend(file_data)
        body.extend(f"\r\n--{boundary}--\r\n".encode())

        return self._call_form("sendPhoto", bytes(body), f"multipart/form-data; boundary={boundary}")

    def send_illustrated_message(self, chat_id, image_path, text, reply_markup=None):
        """Публикует пост с картинкой, если он помещается в лимит Telegram
        (TELEGRAM_PHOTO_CAPTION_LIMIT символов) — тогда фото и текст идут одним
        сообщением (подпись к фото), визуально выглядят как единое целое.

        Если текст длиннее лимита, картинка не отправляется вообще (раньше в
        этом случае фото и текст уходили двумя отдельными сообщениями — это
        читалось как два несвязанных поста и не нравилось внешне). Возвращает
        результат основного сообщения (на него ссылаются id для правки/удаления)."""
        if len(text) <= TELEGRAM_PHOTO_CAPTION_LIMIT:
            return self.send_photo(chat_id, image_path, caption=text, reply_markup=reply_markup)
        return self.send_message(chat_id, text, reply_markup=reply_markup, disable_preview=True)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None, disable_preview=False):
        # Работает только для текстовых сообщений. Если сообщение отправлено как
        # фото (sendPhoto), Telegram отклонит editMessageText — нужен editMessageCaption
        # (не реализован здесь: в MVP черновики отправляются без картинки, см. SKILL.md).
        params = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup)
        if disable_preview:
            params["link_preview_options"] = json.dumps({"is_disabled": True})
        return self.call("editMessageText", **params)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        return self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=json.dumps(reply_markup),
        )

    def delete_message(self, chat_id, message_id):
        return self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        params = {
            "callback_query_id": callback_query_id,
            "show_alert": "true" if show_alert else "false",
        }
        if text:
            params["text"] = text
        return self.call("answerCallbackQuery", **params)

    def set_my_commands(self, commands):
        payload = [{"command": cmd, "description": desc} for cmd, desc in commands]
        return self.call("setMyCommands", commands=json.dumps(payload))
