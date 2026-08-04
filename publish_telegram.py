#!/usr/bin/env python3
"""Публикация поста (текст, опционально с картинкой) в Telegram-канал Legal Privacy.

В отличие от send_telegram.py (доставка черновика на утверждение в личку Андрею/жене),
этот скрипт публикует финальный, уже одобренный текст в публичный канал (TELEGRAM_CHANNEL_ID).
Без сторонних библиотек, как остальные скрипты проекта.
"""
import argparse
import os
import sys

from telegram_api import TelegramAPI, TelegramError, load_env


def main():
    parser = argparse.ArgumentParser(description="Публикация поста в Telegram-канал Legal Privacy")
    parser.add_argument("--check", action="store_true", help="Только проверить доступ бота к каналу (getChat), ничего не публиковать")
    parser.add_argument("--text-file", help="Файл с текстом поста (plain text, UTF-8)")
    parser.add_argument("--text", help="Текст поста строкой (альтернатива --text-file)")
    parser.add_argument("--image", help="Путь к картинке (опционально, отправится как фото с подписью)")
    parser.add_argument(
        "--env-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    )
    args = parser.parse_args()

    env = load_env(os.path.abspath(args.env_file))
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Ошибка: TELEGRAM_BOT_TOKEN не найден в .env")

    channel_id = env.get("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        sys.exit("Ошибка: TELEGRAM_CHANNEL_ID не найден в .env (например, @legal_privacy152DPO)")

    api = TelegramAPI(token)

    try:
        if args.check:
            chat = api.get_chat(channel_id)
            print(f"Канал доступен: {chat.get('title')} (id={chat.get('id')}, username=@{chat.get('username')})")
            print("Права на публикацию проверяются только реальной отправкой — getChat их не подтверждает.")
            return

        if args.text_file:
            with open(args.text_file, encoding="utf-8") as f:
                text = f.read()
        elif args.text:
            text = args.text
        else:
            sys.exit("Ошибка: нужен --text-file или --text (или --check для проверки доступа)")

        if args.image:
            result = api.send_illustrated_message(channel_id, args.image, text)
        else:
            result = api.send_message(channel_id, text, disable_preview=True)

        print(f"Опубликовано в {channel_id}: message_id={result['message_id']}")
    except TelegramError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
