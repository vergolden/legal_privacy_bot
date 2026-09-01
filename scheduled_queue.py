#!/usr/bin/env python3
"""Очередь отложенных публикаций в Telegram-канал Legal Privacy.

Формируется локально в bot_listener.py (verb "sday" — Татьяна/Андрей выбрали
день недели для поста), затем читается в облаке GitHub Actions скриптом
run_scheduled_telegram.py, у которого НЕТ доступа к остальному проекту
Claude_Code (в CI выгружен только этот репозиторий, legal_privacy_bot) —
поэтому текст поста и картинка копируются сюда целиком в момент планирования,
а не хранятся ссылкой на social_posts/.

ВК в эту очередь не попадает: у него есть собственный нативный отложенный
постинг (wall.post с publish_date) — публикацию делает сама ВК, без участия
этого кода в день X. Здесь только то, что канал Telegram Bot API откладывать
не умеет и что должен "нажать" наш триггер (GitHub Actions cron).
"""
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(SCRIPT_DIR, "scheduled_queue")
QUEUE_FILE = os.path.join(QUEUE_DIR, "queue.json")
IMAGES_DIR = os.path.join(QUEUE_DIR, "images")


def _ensure_dirs():
    os.makedirs(IMAGES_DIR, exist_ok=True)


def load_queue():
    _ensure_dirs()
    if not os.path.exists(QUEUE_FILE):
        return {"next_id": 1, "entries": []}
    with open(QUEUE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_queue(queue):
    _ensure_dirs()
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
        f.write("\n")


def active_entries(queue):
    """Незаотменённые записи, отсортированные по дню публикации."""
    entries = [e for e in queue.get("entries", []) if not e.get("cancelled")]
    return sorted(entries, key=lambda e: e["day"][6:] + e["day"][3:5] + e["day"][0:2])


def taken_days(queue):
    """Дни (DD-MM-YYYY), на которые уже что-то запланировано — день общий для
    обоих каналов (см. project decision), поэтому проверка не разделяется по каналу."""
    return {e["day"] for e in queue.get("entries", []) if not e.get("cancelled")}


def find_active_entry_for_post(queue, post_file_rel, post_id):
    for e in queue.get("entries", []):
        if not e.get("cancelled") and e["post_file"] == post_file_rel and e["post_id"] == post_id:
            return e
    return None


def get_entry(queue, entry_id):
    for e in queue.get("entries", []):
        if e["entry_id"] == entry_id:
            return e
    return None


def add_entry(
    queue,
    *,
    post_file,
    post_id,
    title,
    day,
    time_str,
    vk_post_id,
    vk_group_id,
    telegram_text,
    image_file,
    scheduled_by,
    scheduled_at,
):
    entry_id = queue.get("next_id", 1)
    queue["next_id"] = entry_id + 1
    entry = {
        "entry_id": entry_id,
        "post_file": post_file,
        "post_id": post_id,
        "title": title,
        "day": day,
        "time": time_str,
        "cancelled": False,
        "vk": {"status": "scheduled", "vk_post_id": vk_post_id, "group_id": vk_group_id},
        "telegram": {
            "status": "pending",
            "text": telegram_text,
            "image_file": image_file,
            "message_id": None,
            "failed_reason": None,
            "published_at": None,
        },
        "scheduled_by": scheduled_by,
        "scheduled_at": scheduled_at,
    }
    queue.setdefault("entries", []).append(entry)
    return entry
