#!/usr/bin/env python3
"""Кнопки действий под постом в Telegram-боте Legal Privacy.

Формат callback_data (данные, которые Telegram присылает боту при нажатии
кнопки — ограничение платформы: не больше 64 байт): "<verb>:<DD-MM-YYYY>:<id>",
например "pub_vk:26-07-2026:1" — опубликовать в ВК пост id=1 из файла за 26.07.2026.
По дате однозначно восстанавливается путь к файлу поста, отдельно его хранить не нужно.

Verbs: pub_vk (опубликовать в ВК), pub_tg (опубликовать в Telegram-канал),
img (сгенерировать/перегенерировать картинку через ProxyAPI — повторный клик
после первой генерации запускает новый платный вызов тем же промптом), edit (редактировать текст),
arch (в архив), rej (не размещаем).
"""
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_LP = os.path.dirname(SCRIPT_DIR)  # projects/legal_privacy

MONTHS_EN = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

POST_FILENAME_RE = re.compile(r"^пост_(\d{2}-\d{2}-\d{4})\.json$")

VERB_LABELS = {
    "pub_vk": "📤 ВК",
    "pub_tg": "📤 Telegram-канал",
    "img": "🖼 Сгенерировать картинку",
}
VERB_LABELS_DONE = {
    "pub_vk": "✅ ВК",
    "pub_tg": "✅ Telegram-канал",
    "img": "🔄 Перегенерировать",
}


def post_file_path(date_str, base_dir=None):
    """date_str в формате DD-MM-YYYY -> путь social_posts/YYYY/<month_en>/пост_DD-MM-YYYY.json"""
    dd, mm, yyyy = date_str.split("-")
    month_name = MONTHS_EN[int(mm)]
    root = base_dir or PROJECT_ROOT_LP
    return os.path.join(root, "social_posts", yyyy, month_name, f"пост_{date_str}.json")


def date_from_post_file(post_file):
    basename = os.path.basename(post_file)
    m = POST_FILENAME_RE.match(basename)
    if not m:
        raise ValueError(f"Не удалось извлечь дату DD-MM-YYYY из имени файла: {basename}")
    return m.group(1)


def encode_callback(verb, date_str, post_id):
    data = f"{verb}:{date_str}:{post_id}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data длиннее 64 байт: {data!r}")
    return data


def decode_callback(data):
    parts = data.split(":")
    if len(parts) != 3:
        raise ValueError(f"Некорректный callback_data: {data!r}")
    verb, date_str, post_id_str = parts
    return verb, date_str, int(post_id_str)


def load_posts_file(post_file):
    with open(post_file, encoding="utf-8") as f:
        return json.load(f)


def save_posts_file(post_file, data):
    with open(post_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_post(data, post_id):
    for post in data.get("posts", []):
        if post["id"] == post_id:
            return post
    return None


def load_post(post_file, post_id):
    data = load_posts_file(post_file)
    post = find_post(data, post_id)
    if post is None:
        raise ValueError(f"Пост id={post_id} не найден в {post_file}")
    return post


def format_post_message(post, note=None):
    """Собрать текст сообщения из поста: заголовок, готовый текст, источник, опциональная заметка.

    Единая точка форматирования — используется и при первой отправке черновика
    (send_telegram.py), и при обновлении сообщения после правки (bot_listener.py),
    чтобы вид сообщения не расходился между этими двумя случаями.
    """
    source = post.get("source") or {}
    title = source.get("title", "")
    url = source.get("url", "")
    date = source.get("published_date", "")
    content = (((post.get("platforms") or {}).get("vk") or {}).get("content") or "").strip()

    parts = [f"📰 {title}".strip(), "", content]
    if url:
        src_line = f"Источник: {url}"
        if date:
            src_line += f" ({date})"
        parts += ["", src_line]
    if note:
        parts += ["", note]
    return "\n".join(parts)


def build_post_keyboard(post_file, post):
    """Собрать inline_keyboard для поста — статус публикации берётся из post["published"]."""
    date_str = date_from_post_file(post_file)
    post_id = post["id"]
    published = post.get("published") or {}

    def publish_button(verb, channel_key):
        done = bool((published.get(channel_key) or {}).get("published_at"))
        label = VERB_LABELS_DONE[verb] if done else VERB_LABELS[verb]
        return {"text": label, "callback_data": encode_callback(verb, date_str, post_id)}

    image_done = bool(post.get("image_path"))
    row_image = [
        {
            "text": VERB_LABELS_DONE["img"] if image_done else VERB_LABELS["img"],
            "callback_data": encode_callback("img", date_str, post_id),
        }
    ]
    row_publish = [
        publish_button("pub_vk", "vk"),
        publish_button("pub_tg", "telegram"),
    ]
    row_actions = [
        {"text": "✏️ Редактировать", "callback_data": encode_callback("edit", date_str, post_id)},
        {"text": "🗄 В архив", "callback_data": encode_callback("arch", date_str, post_id)},
        {"text": "🚫 Не размещаем", "callback_data": encode_callback("rej", date_str, post_id)},
    ]
    return {"inline_keyboard": [row_image, row_publish, row_actions]}
