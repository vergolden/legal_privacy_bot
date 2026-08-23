#!/usr/bin/env python3
"""Слушатель Telegram-бота Legal Privacy.

Работает постоянно (long polling), обрабатывает два типа входящих событий:

1. Команда /digest от разрешённых chat_id (Андрей и жена) — запускает Claude Code
   в headless-режиме (claude -p), который ищет новости и готовит черновики постов.
   Это единственное место, где решение принимает ИИ — дальше только код.

2. Нажатия кнопок под черновиком (callback_query) — публикация в ВК/Telegram-канал,
   редактирование, архив, отказ. Обрабатывается напрямую в Python, без вызова claude -p:
   это детерминированные действия, агент здесь не нужен (см. дорожную карту,
   projects/legal_privacy/дорожная_карта_bot_pipeline_2026-07-26.md).

Не требует сторонних библиотек — только стандартная библиотека Python,
как и остальные скрипты проекта.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone

from telegram_api import TelegramAPI, TelegramError, load_env
from post_buttons import (
    build_post_keyboard,
    date_from_post_file,
    decode_callback,
    find_post,
    format_post_message,
    load_posts_file,
    post_file_path,
    save_posts_file,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_LP = os.path.dirname(SCRIPT_DIR)  # projects/legal_privacy
CLAUDE_CODE_ROOT = os.path.dirname(os.path.dirname(PROJECT_ROOT_LP))  # Claude_Code
STATE_FILE = os.path.join(SCRIPT_DIR, ".bot_listener_state.json")
EDITING_STATE_FILE = os.path.join(SCRIPT_DIR, ".bot_listener_editing_state.json")
MSK = timezone(timedelta(hours=3))

ORCHESTRATOR_PROMPT = (
    "Этот запуск инициирован из Telegram-бота Legal Privacy (headless, "
    "claude -p, без интерактивной сессии и без человека за терминалом) "
    "командой /digest. Запусти скилл-оркестратор legal-privacy-daily-pipeline "
    "в автономном режиме, без подтверждения плана: найди свежие новости по "
    "152-ФЗ/ПДн/ИБ, подготовь черновик поста, отправь его на утверждение в "
    "Telegram-бот Legal Privacy и зафиксируй результат в журнале публикаций."
)

COMMANDS = {
    "digest": "Найти новости и подготовить черновик поста на утверждение",
    "start": "Проверка связи с ботом",
}

# Headless-режим (claude -p) не подхватывает permissions.allow из .claude/settings.json
# автоматически — без человека за терминалом подтвердить всплывающий permission-запрос
# некому, и вызов зависает/падает. Разрешения нужно продублировать явным флагом
# --allowedTools, теми же паттернами, что и в settings.json.
ALLOWED_TOOLS = [
    "Read",
    "Write",
    "WebFetch",
    "WebSearch",
    "Edit(projects/legal_privacy/news_digests/**)",
    "Edit(projects/legal_privacy/social_posts/**)",
    "Bash(python3 projects/legal_privacy/scripts/send_telegram.py *)",
    "Bash(python3 projects/legal_privacy/scripts/publish_vk.py *)",
    "Agent",
]

CHANNEL_LABELS = {"vk": "ВК", "telegram": "Telegram-канал"}
REVIEW_KEY_TO_ENV = {"andrew": "TELEGRAM_CHAT_ID_ANDREW", "wife": "TELEGRAM_CHAT_ID_WIFE"}


def load_json_state(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def now_iso():
    return datetime.now(MSK).isoformat()


def run_pipeline(api, chat_id, requester):
    api.send_message(
        chat_id,
        f"Инициатор: {requester}. Запускаю поиск новостей — это может занять пару минут, "
        f"пришлю черновик, как только будет готов.",
    )
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Запуск пайплайна (инициатор: {requester})")
    try:
        result = subprocess.run(
            ["claude", "-p", ORCHESTRATOR_PROMPT, "--allowedTools", *ALLOWED_TOOLS],
            cwd=CLAUDE_CODE_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        api.send_message(chat_id, "Пайплайн не уложился в отведённое время (15 минут). Останавливаю, посмотрю логи.")
        return
    except FileNotFoundError:
        api.send_message(chat_id, "Не найден claude CLI в PATH на сервере, где запущен слушатель. Нужно поправить конфигурацию.")
        return

    if result.returncode != 0:
        print("STDERR:", result.stderr[-2000:])
        api.send_message(chat_id, "Пайплайн завершился с ошибкой. Подробности в логе слушателя, посмотрю и поправлю.")
        return

    print("STDOUT:", result.stdout[-2000:])
    api.send_message(chat_id, "Пайплайн отработал. Если черновик не пришёл отдельным сообщением — проверь лог слушателя.")


def resolve_image_path(image_path):
    """image_path в JSON хранится относительно CLAUDE_CODE_ROOT (как у уже
    существующего примера id1 от 15.07) — приводим к абсолютному, чтобы open()
    не зависел от того, из какой директории реально запущен bot_listener.py."""
    if not image_path or os.path.isabs(image_path):
        return image_path
    return os.path.join(CLAUDE_CODE_ROOT, image_path)


def write_tmp_text(text):
    path = os.path.join(PROJECT_ROOT_LP, "social_posts", f"tmp_publish_{int(time.time() * 1000)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def refresh_message_everywhere(api, env, post_file, post):
    """Обновить кнопки под черновиком у обоих получателей (по review.message_ids)."""
    message_ids = ((post.get("review") or {}).get("message_ids")) or {}
    keyboard = build_post_keyboard(post_file, post)
    for key, msg_id in message_ids.items():
        chat_id = env.get(REVIEW_KEY_TO_ENV.get(key, ""))
        if not chat_id or not msg_id:
            continue
        try:
            api.edit_message_reply_markup(chat_id, msg_id, keyboard)
        except TelegramError as e:
            print(f"Не удалось обновить кнопки у {key} (chat_id={chat_id}): {e}")


def retext_message_everywhere(api, env, post_file, post, note=None):
    """Полностью обновить текст+кнопки сообщения у обоих получателей (после правки текста)."""
    message_ids = ((post.get("review") or {}).get("message_ids")) or {}
    text = format_post_message(post, note=note)
    keyboard = build_post_keyboard(post_file, post)
    for key, msg_id in message_ids.items():
        chat_id = env.get(REVIEW_KEY_TO_ENV.get(key, ""))
        if not chat_id or not msg_id:
            continue
        try:
            api.edit_message_text(chat_id, msg_id, text, reply_markup=keyboard, disable_preview=True)
        except TelegramError as e:
            print(f"Не удалось обновить текст у {key} (chat_id={chat_id}): {e}")


def delete_message_everywhere(api, env, post):
    message_ids = ((post.get("review") or {}).get("message_ids")) or {}
    for key, msg_id in message_ids.items():
        chat_id = env.get(REVIEW_KEY_TO_ENV.get(key, ""))
        if not chat_id or not msg_id:
            continue
        try:
            api.delete_message(chat_id, msg_id)
        except TelegramError as e:
            print(f"Не удалось удалить сообщение у {key} (chat_id={chat_id}): {e}")


def handle_publish(api, env, callback_id, requester, posts_data, post, post_file, channel):
    published = post.setdefault("published", {})
    channel_pub = published.setdefault(channel, {"published_at": None})
    if channel_pub.get("published_at"):
        api.answer_callback_query(callback_id, text=f"Уже опубликовано в {CHANNEL_LABELS[channel]}.", show_alert=True)
        return

    text = ((post.get("platforms") or {}).get("vk") or {}).get("content")
    # MVP: единый текст поста для всех каналов (news-to-social пока не готовит
    # отдельные версии под telegram/site) — берём platforms.vk.content.
    if not text:
        api.answer_callback_query(callback_id, text="У поста нет текста для публикации.", show_alert=True)
        return

    image_path = resolve_image_path(post.get("image_path"))
    tmp_text_file = None
    try:
        if channel == "vk":
            group_id = env.get("VK_GROUP_ID")
            if not group_id:
                api.answer_callback_query(callback_id, text="VK_GROUP_ID не задан в .env.", show_alert=True)
                return
            tmp_text_file = write_tmp_text(text)
            cmd = [
                "python3", os.path.join(SCRIPT_DIR, "publish_vk.py"),
                "--group-id", group_id, "--text-file", tmp_text_file,
            ]
            if image_path:
                cmd += ["--image", image_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        else:  # telegram
            cmd = ["python3", os.path.join(SCRIPT_DIR, "publish_telegram.py"), "--text", text]
            if image_path:
                cmd += ["--image", image_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    finally:
        if tmp_text_file and os.path.exists(tmp_text_file):
            os.remove(tmp_text_file)

    if result.returncode != 0:
        print(f"Ошибка публикации ({channel}): {result.stderr[-1500:]}")
        api.answer_callback_query(
            callback_id, text=f"Ошибка публикации в {CHANNEL_LABELS[channel]}, смотрю лог.", show_alert=True
        )
        return

    published_at = now_iso()
    channel_pub["published_at"] = published_at
    if channel == "vk":
        m = re.search(r"post_id=(\d+)", result.stdout)
        channel_pub["vk_post_id"] = int(m.group(1)) if m else None
    else:
        m = re.search(r"message_id=(\d+)", result.stdout)
        channel_pub["message_id"] = int(m.group(1)) if m else None

    approval = post.setdefault("approval", {})
    if approval.get("status") == "pending":
        approval["status"] = "approved"
        approval["reviewed_by"] = requester
        approval["reviewed_at"] = published_at
    targets = approval.setdefault("publish_targets", [])
    if channel not in targets:
        targets.append(channel)

    save_posts_file(post_file, posts_data)

    api.answer_callback_query(callback_id, text=f"Опубликовано в {CHANNEL_LABELS[channel]}.")
    refresh_message_everywhere(api, env, post_file, post)


def handle_edit_request(api, callback_id, chat_id, post_file, post_id, editing_state):
    editing_state[chat_id] = {"post_file": post_file, "post_id": post_id}
    save_json_state(EDITING_STATE_FILE, editing_state)
    api.answer_callback_query(callback_id, text="Жду новый текст следующим сообщением.")
    api.send_message(chat_id, "✏️ Пришлите новый текст поста следующим сообщением в этот чат.")


def apply_edit(api, env, chat_id, requester, entry, new_text):
    post_file = entry["post_file"]
    post_id = entry["post_id"]
    if not os.path.exists(post_file):
        api.send_message(chat_id, "Не нашёл файл поста — правка потеряна, посмотрю лог.")
        return

    posts_data = load_posts_file(post_file)
    post = find_post(posts_data, post_id)
    if post is None:
        api.send_message(chat_id, "Не нашёл этот пост в файле — правка потеряна, посмотрю лог.")
        return

    platforms = post.setdefault("platforms", {})
    vk_block = platforms.setdefault("vk", {})
    vk_block["content"] = new_text.strip()
    save_posts_file(post_file, posts_data)

    note = f"✏️ Отредактировано: {requester}"
    retext_message_everywhere(api, env, post_file, post, note=note)
    api.send_message(chat_id, "Текст обновлён.")


def handle_close(api, env, callback_id, requester, posts_data, post, post_file, verb):
    status = "archived" if verb == "arch" else "rejected"
    approval = post.setdefault("approval", {})
    approval["status"] = status
    approval["reviewed_by"] = requester
    approval["reviewed_at"] = now_iso()
    save_posts_file(post_file, posts_data)

    label = "Отправлено в архив." if verb == "arch" else "Помечено «не размещаем»."
    api.answer_callback_query(callback_id, text=label)
    delete_message_everywhere(api, env, post)


def send_photo_to_reviewers(api, env, post):
    """После генерации картинки — отправить её отдельным сообщением (без подписи
    и кнопок) всем, кому уже отправлен черновик. Текстовое сообщение с кнопками
    не трогаем: картинка и текст теперь всегда отдельные сообщения (см.
    TelegramAPI.send_illustrated_message), поэтому review.message_ids по-прежнему
    указывает на действующее текстовое сообщение — обновлять их не нужно."""
    message_ids = ((post.get("review") or {}).get("message_ids")) or {}
    image_path = resolve_image_path(post.get("image_path"))
    for key in message_ids:
        chat_id = env.get(REVIEW_KEY_TO_ENV.get(key, ""))
        if not chat_id:
            continue
        try:
            api.send_photo(chat_id, image_path)
        except TelegramError as e:
            print(f"Не удалось отправить фото {key} (chat_id={chat_id}): {e}")


def handle_image_request(api, env, callback_id, chat_id, posts_data, post, post_file):
    image_prompt = post.get("image_prompt")
    if not image_prompt:
        api.answer_callback_query(callback_id, text="У поста нет image_prompt для генерации.", show_alert=True)
        return

    # Повторный клик после первой генерации — это осознанная перегенерация тем же
    # промптом (кнопка меняет подпись на "🔄 Перегенерировать", см. post_buttons.py):
    # у моделей генерации есть элемент случайности, картинка выйдет другой. Каждый
    # клик — новый платный вызов ProxyAPI, старое фото в чате не удаляется (нет
    # привязки message_id фото к посту), новое шлётся отдельным сообщением поверх.
    is_regeneration = bool(post.get("image_path"))

    # Генерация может занять больше времени, чем Telegram ждёт ответа на callback_query
    # (после ~30-60 сек запрос считается устаревшим и answerCallbackQuery падает с
    # ошибкой) — отвечаем сразу, а результат отправляем обычным сообщением в чат.
    wait_text = "Перегенерирую картинку, обычно занимает до минуты…" if is_regeneration else "Генерирую картинку, обычно занимает до минуты…"
    api.answer_callback_query(callback_id, text=wait_text)

    date_str = date_from_post_file(post_file)
    output_path = os.path.join(os.path.dirname(post_file), f"пост_{date_str}_id{post['id']}.png")

    try:
        result = subprocess.run(
            [
                "python3", os.path.join(SCRIPT_DIR, "generate_image.py"),
                "--prompt", image_prompt,
                "--output", output_path,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        api.send_message(chat_id, "Генерация картинки не уложилась в 3 минуты, посмотрю лог.")
        return

    if result.returncode != 0:
        print(f"Ошибка генерации картинки: {result.stderr[-1500:]}")
        api.send_message(chat_id, "Ошибка генерации картинки, посмотрю лог.")
        return

    post["image_path"] = os.path.relpath(output_path, CLAUDE_CODE_ROOT)
    save_posts_file(post_file, posts_data)

    send_photo_to_reviewers(api, env, post)
    refresh_message_everywhere(api, env, post_file, post)


def handle_callback(api, env, callback, allowed_chats, editing_state):
    callback_id = callback["id"]
    chat_id = str(callback["message"]["chat"]["id"])
    data = callback.get("data", "")

    if chat_id not in allowed_chats:
        api.answer_callback_query(callback_id, text="Нет доступа.", show_alert=True)
        return

    requester = allowed_chats[chat_id]

    try:
        verb, date_str, post_id = decode_callback(data)
        post_file = post_file_path(date_str)
    except ValueError as e:
        api.answer_callback_query(callback_id, text=f"Не понял кнопку: {e}", show_alert=True)
        return

    if not os.path.exists(post_file):
        api.answer_callback_query(callback_id, text="Файл поста не найден.", show_alert=True)
        return

    posts_data = load_posts_file(post_file)
    post = find_post(posts_data, post_id)
    if post is None:
        api.answer_callback_query(callback_id, text="Пост не найден в файле.", show_alert=True)
        return

    if verb == "pub_vk":
        handle_publish(api, env, callback_id, requester, posts_data, post, post_file, "vk")
    elif verb == "pub_tg":
        handle_publish(api, env, callback_id, requester, posts_data, post, post_file, "telegram")
    elif verb == "img":
        handle_image_request(api, env, callback_id, chat_id, posts_data, post, post_file)
    elif verb == "edit":
        handle_edit_request(api, callback_id, chat_id, post_file, post_id, editing_state)
    elif verb in ("arch", "rej"):
        handle_close(api, env, callback_id, requester, posts_data, post, post_file, verb)
    else:
        api.answer_callback_query(callback_id, text="Неизвестное действие.", show_alert=True)


def main():
    env_path = os.path.join(PROJECT_ROOT_LP, ".env")
    env = load_env(env_path)

    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Ошибка: TELEGRAM_BOT_TOKEN не найден в .env")

    allowed_chats = {}
    if env.get("TELEGRAM_CHAT_ID_ANDREW"):
        allowed_chats[env["TELEGRAM_CHAT_ID_ANDREW"]] = "Андрей"
    if env.get("TELEGRAM_CHAT_ID_WIFE"):
        allowed_chats[env["TELEGRAM_CHAT_ID_WIFE"]] = "Татьяна"

    if not allowed_chats:
        sys.exit("Ошибка: не заданы TELEGRAM_CHAT_ID_ANDREW / TELEGRAM_CHAT_ID_WIFE в .env")

    api = TelegramAPI(token)
    api.set_my_commands(list(COMMANDS.items()))

    state = load_json_state(STATE_FILE, {"offset": 0})
    offset = state.get("offset", 0)
    editing_state = load_json_state(EDITING_STATE_FILE, {})

    print(f"Слушатель запущен. Разрешённые chat_id: {list(allowed_chats.keys())}")
    print("Команда запуска пайплайна: /digest. Кнопки под черновиками обрабатываются автоматически. Ctrl+C для остановки.")

    while True:
        try:
            updates = api.get_updates(offset)
        except (urllib.error.URLError, TelegramError, OSError) as e:
            # OSError покрывает и TimeoutError/socket.timeout — long polling (getUpdates с
            # ожиданием до 30 сек) время от времени упирается в сетевой таймаут на чтении
            # ответа, и urllib в этом случае не всегда заворачивает его в URLError.
            # Это штатная ситуация при долгом опросе, не повод останавливать слушателя.
            print(f"Ошибка опроса Telegram: {e}. Повтор через 5 секунд.")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            save_json_state(STATE_FILE, {"offset": offset})

            # Один плохой update (сетевой сбой на середине обработки, неожиданный формат
            # данных и т.п.) не должен ронять весь процесс — слушатель обязан продолжать
            # работать. Ловим широко и логируем, а не подбираем исключения точечно.
            try:
                callback = update.get("callback_query")
                if callback:
                    handle_callback(api, env, callback, allowed_chats, editing_state)
                    continue

                message = update.get("message")
                if not message:
                    continue

                chat_id = str(message.get("chat", {}).get("id"))
                text = (message.get("text") or "").strip()

                if chat_id not in allowed_chats:
                    print(f"Игнорирую сообщение от постороннего chat_id={chat_id}")
                    continue

                requester = allowed_chats[chat_id]

                # Если для этого чата ждём текст правки (после нажатия "Редактировать") —
                # следующее не-командное сообщение считается новым текстом поста, а не командой.
                if chat_id in editing_state and not text.startswith("/") and text:
                    entry = editing_state.pop(chat_id)
                    save_json_state(EDITING_STATE_FILE, editing_state)
                    apply_edit(api, env, chat_id, requester, entry, text)
                    continue

                if text == "/start":
                    api.send_message(chat_id, "Бот на связи. Команда /digest запускает поиск новостей и подготовку черновика.")
                elif text == "/digest":
                    run_pipeline(api, chat_id, requester)
                elif text.startswith("/"):
                    api.send_message(chat_id, "Неизвестная команда. Доступно: /digest")
            except Exception as e:
                print(f"Ошибка обработки update_id={update.get('update_id')}: {e!r}. Продолжаю работу.")


if __name__ == "__main__":
    main()
