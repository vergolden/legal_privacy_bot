#!/usr/bin/env python3
"""Отправка сообщения (текст, опционально с картинкой) в Telegram-бот Legal Privacy.

Используется субагентом telegram-courier для доставки черновиков новостей
и постов на утверждение. Без сторонних библиотек, как остальные скрипты проекта.

Два режима:
- Свободный текст (--text / --text-file) — для сообщений без привязки к посту
  (например, "свежих новостей сегодня не нашлось").
- Пост (--post-file + --post-id) — текст и картинка берутся из самого поста
  (format_post_message), под сообщением автоматически появляются кнопки действий
  (публикация/редактирование/архив/отказ). Так текст сообщения и внутри бота,
  и после правки через кнопку "Редактировать" собирается одной и той же функцией
  и не расходится между собой.
"""
import argparse
import os
import sys

from telegram_api import TelegramAPI, TelegramError, load_env
from post_buttons import build_post_keyboard, format_post_message, load_post


def main():
    parser = argparse.ArgumentParser(description="Отправка сообщения в Telegram-бот Legal Privacy")
    parser.add_argument("--to", required=True, choices=["andrew", "wife", "both"], help="Кому отправить")
    parser.add_argument("--text-file", help="Файл с текстом сообщения (свободный режим, без --post-file)")
    parser.add_argument("--text", help="Текст сообщения строкой (альтернатива --text-file)")
    parser.add_argument("--image", help="Путь к картинке (свободный режим; для --post-file берётся из image_path поста, если не задано явно)")
    parser.add_argument("--post-file", help="Путь к JSON-файлу поста (social-content/v1) — режим 'пост'")
    parser.add_argument("--post-id", type=int, help="id поста внутри --post-file")
    parser.add_argument("--note", help="Короткая заметка, которая добавится в конец сообщения (например, предупреждение о качестве черновика) — только в режиме 'пост'")
    parser.add_argument(
        "--env-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    )
    args = parser.parse_args()

    env = load_env(os.path.abspath(args.env_file))
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Ошибка: TELEGRAM_BOT_TOKEN не найден в .env")

    chat_ids = []
    if args.to in ("andrew", "both") and env.get("TELEGRAM_CHAT_ID_ANDREW"):
        chat_ids.append(("Андрей", env["TELEGRAM_CHAT_ID_ANDREW"]))
    if args.to in ("wife", "both") and env.get("TELEGRAM_CHAT_ID_WIFE"):
        chat_ids.append(("жена", env["TELEGRAM_CHAT_ID_WIFE"]))

    if not chat_ids:
        sys.exit(f"Ошибка: не найден chat_id для --to {args.to} в .env")

    reply_markup = None
    image = args.image

    if args.post_file and args.post_id is not None:
        post = load_post(args.post_file, args.post_id)
        text = format_post_message(post, note=args.note)
        reply_markup = build_post_keyboard(args.post_file, post)
        if not image:
            image = post.get("image_path")
    elif args.post_file or args.post_id is not None:
        sys.exit("Ошибка: --post-file и --post-id нужно указывать вместе")
    elif args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        sys.exit("Ошибка: нужен --text-file/--text, или --post-file вместе с --post-id")

    api = TelegramAPI(token)

    try:
        for name, chat_id in chat_ids:
            if image:
                result = api.send_illustrated_message(chat_id, image, text, reply_markup=reply_markup)
            else:
                result = api.send_message(chat_id, text, reply_markup=reply_markup, disable_preview=True)
            print(f"Отправлено {name} (chat_id={chat_id}): message_id={result['message_id']}")
    except TelegramError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
