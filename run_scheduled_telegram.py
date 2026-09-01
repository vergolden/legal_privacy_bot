#!/usr/bin/env python3
"""Точка входа для GitHub Actions (cron): публикует в Telegram-канал Legal
Privacy те посты из очереди scheduled_queue/queue.json, у которых сегодня
наступил день публикации.

Запускается ежедневно из .github/workflows/lp-scheduled-telegram.yml в
репозитории legal_privacy_bot. В CI выгружен только этот репозиторий —
доступа к остальному проекту Claude_Code (social_posts/, .env на уровень
выше) нет. Вся нужная информация (текст, картинка) уже скопирована в очередь
локально, при планировании (bot_listener.py, verb "sday").

ВК сюда не попадает — у него собственный нативный отложенный постинг
(publish_date), публикацию делает сама VK без участия этого скрипта.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

from telegram_api import TelegramAPI, TelegramError, load_env
import scheduled_queue

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MSK = timezone(timedelta(hours=3))


def notify_failure(api, env, entry, reason):
    text = (
        f"⚠️ Не удалось опубликовать в Telegram запланированный пост на {entry['day']}:\n"
        f"«{entry['title']}»\n\nПричина: {reason}\n\n"
        f"Посмотрите /plan в боте — пост остался в очереди со статусом «ошибка», "
        f"можно отменить и опубликовать вручную."
    )
    for env_key in ("TELEGRAM_CHAT_ID_ANDREW", "TELEGRAM_CHAT_ID_WIFE"):
        chat_id = env.get(env_key)
        if not chat_id:
            continue
        try:
            api.send_message(chat_id, text)
        except TelegramError as e:
            print(f"Не удалось отправить уведомление об ошибке в {env_key}: {e}")


def main():
    env_path = os.path.join(os.path.dirname(SCRIPT_DIR), ".env")
    env = load_env(env_path)

    token = env.get("TELEGRAM_BOT_TOKEN")
    channel_id = env.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel_id:
        sys.exit("Ошибка: TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_ID не заданы (секреты репозитория)")

    api = TelegramAPI(token)
    today_str = datetime.now(MSK).strftime("%d-%m-%Y")

    queue = scheduled_queue.load_queue()
    due = [
        e
        for e in queue.get("entries", [])
        if not e.get("cancelled") and e["day"] == today_str and e["telegram"]["status"] == "pending"
    ]

    if not due:
        print(f"На {today_str} запланированных публикаций в Telegram нет.")
        return

    for entry in due:
        text = entry["telegram"]["text"]
        image_file = entry["telegram"].get("image_file")
        image_path = os.path.join(scheduled_queue.IMAGES_DIR, image_file) if image_file else None
        try:
            if image_path and os.path.exists(image_path):
                result = api.send_illustrated_message(channel_id, image_path, text)
            else:
                result = api.send_message(channel_id, text, disable_preview=True)
            entry["telegram"]["status"] = "published"
            entry["telegram"]["message_id"] = result.get("message_id")
            entry["telegram"]["published_at"] = datetime.now(MSK).isoformat()
            print(f"Опубликовано: entry_id={entry['entry_id']}, message_id={result.get('message_id')}")
        except TelegramError as e:
            entry["telegram"]["status"] = "failed"
            entry["telegram"]["failed_reason"] = str(e)
            print(f"Ошибка публикации entry_id={entry['entry_id']}: {e}")
            notify_failure(api, env, entry, str(e))

    scheduled_queue.save_queue(queue)


if __name__ == "__main__":
    main()
