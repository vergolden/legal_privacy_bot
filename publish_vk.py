#!/usr/bin/env python3
"""Публикация поста на стену сообщества VK (текст + опционально одна картинка).

Текст без картинки — можно ключом сообщества (Community Access Token, VK_COMMUNITY_TOKEN):
Управление сообществом → Работа с API → Ключи доступа → Создать ключ (права: wall, photos).

Загрузка фото (photos.getWallUploadServer/saveWallPhoto) с Community Token не работает —
это подтверждённое ограничение VK API (error 27, "method is unavailable with group auth").
Для поста с картинкой нужен пользовательский токен (VK_USER_TOKEN) от аккаунта-админа
группы, полученный через OAuth (scope=wall,groups,photos,offline).
"""
import argparse
import json
import mimetypes
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

VK_API_VERSION = "5.199"
VK_API_BASE = "https://api.vk.com/method"


def https_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


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


def vk_call(method, token, **params):
    params = {**params, "access_token": token, "v": VK_API_VERSION}
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(f"{VK_API_BASE}/{method}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=https_context()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP-ошибка VK API [{method}] ({e.code}): {e.read().decode('utf-8', errors='replace')}")
    if "error" in result:
        error = result["error"]
        sys.exit(f"Ошибка VK API [{method}]: {error.get('error_msg')} (code {error.get('error_code')})")
    return result["response"]


def upload_photo(upload_url, image_path):
    boundary = uuid.uuid4().hex
    filename = os.path.basename(image_path)
    mime_type = mimetypes.guess_type(filename)[0] or "image/png"

    with open(image_path, "rb") as f:
        file_data = f.read()

    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode()
    body += f"Content-Type: {mime_type}\r\n\r\n".encode()
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        upload_url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120, context=https_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Публикация поста в группу VK")
    parser.add_argument("--group-id", required=True, help="Числовой ID группы (без минуса)")
    parser.add_argument("--text-file", help="Файл с текстом поста (plain text, UTF-8) — обязателен, если не указан --delete")
    parser.add_argument("--image", help="Путь к картинке для прикрепления (опционально)")
    parser.add_argument(
        "--publish-date",
        type=int,
        help="Unixtime — отложить публикацию до этого момента (нативный отложенный постинг VK)",
    )
    parser.add_argument(
        "--delete",
        type=int,
        metavar="POST_ID",
        help="Удалить/отменить ранее запланированный пост с этим post_id (вместо публикации)",
    )
    parser.add_argument(
        "--env-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать, что будет отправлено, без реального вызова VK API",
    )
    args = parser.parse_args()

    if args.delete is None and not args.text_file:
        parser.error("--text-file обязателен, если не указан --delete")

    env = load_env(os.path.abspath(args.env_file))
    user_token = env.get("VK_USER_TOKEN")
    community_token = env.get("VK_COMMUNITY_TOKEN")
    group_id = args.group_id

    if args.delete is not None:
        if args.dry_run:
            print(f"[DRY RUN] Удаление: owner_id=-{group_id}, post_id={args.delete}")
            return
        post_token = user_token or community_token
        if not post_token:
            sys.exit("Ошибка: не найден ни VK_USER_TOKEN, ни VK_COMMUNITY_TOKEN в .env")
        vk_call("wall.delete", post_token, owner_id=f"-{group_id}", post_id=args.delete)
        print(f"Удалено: post_id={args.delete}")
        return

    with open(args.text_file, encoding="utf-8") as f:
        text = f.read()

    if args.dry_run:
        print("[DRY RUN] Группа:", group_id)
        print("[DRY RUN] Картинка:", args.image or "(нет)")
        print("[DRY RUN] Текст поста:\n", text)
        return

    attachment = None
    if args.image:
        if not user_token:
            sys.exit(
                "Ошибка: для публикации с картинкой нужен VK_USER_TOKEN в .env "
                "(Community Token не умеет загружать фото — ограничение VK API)."
            )
        upload_server = vk_call("photos.getWallUploadServer", user_token, group_id=group_id)
        upload_result = upload_photo(upload_server["upload_url"], args.image)
        saved = vk_call(
            "photos.saveWallPhoto",
            user_token,
            group_id=group_id,
            photo=upload_result["photo"],
            server=upload_result["server"],
            hash=upload_result["hash"],
        )
        photo = saved[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"

    # Публикация текста возможна и Community Token, и User Token — предпочитаем
    # тот же токен, которым грузили фото, иначе берём community.
    post_token = user_token or community_token
    if not post_token:
        sys.exit("Ошибка: не найден ни VK_USER_TOKEN, ни VK_COMMUNITY_TOKEN в .env")

    post_params = {
        "owner_id": f"-{group_id}",
        "from_group": 1,
        "message": text,
    }
    if attachment:
        post_params["attachments"] = attachment
    if args.publish_date:
        post_params["publish_date"] = args.publish_date

    result = vk_call("wall.post", post_token, **post_params)
    post_id = result["post_id"]
    if args.publish_date:
        print(f"Запланировано: post_id={post_id}")
    else:
        print(f"Опубликовано: post_id={post_id}")
    print(f"Ссылка: https://vk.com/wall-{group_id}_{post_id}")


if __name__ == "__main__":
    main()
