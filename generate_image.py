#!/usr/bin/env python3
"""Генерация картинки к посту через ProxyAPI.ru (модели семейства GPT Image)."""
import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

PROXYAPI_URL = "https://api.proxyapi.ru/openai/v1/images/generations"


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


def main():
    parser = argparse.ArgumentParser(description="Генерация картинки через ProxyAPI.ru")
    parser.add_argument("--prompt", required=True, help="Промпт для картинки (image_prompt из JSON поста)")
    parser.add_argument("--output", required=True, help="Путь для сохранения PNG")
    parser.add_argument("--model", default="gpt-image-1-mini", help="Модель ProxyAPI (по умолчанию — самая дешёвая, для черновиков)")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument(
        "--env-file",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    )
    args = parser.parse_args()

    env = load_env(os.path.abspath(args.env_file))
    api_key = env.get("PROXYAPI_API_KEY")
    if not api_key:
        sys.exit("Ошибка: PROXYAPI_API_KEY не найден ни в переменных окружения, ни в .env")

    payload = json.dumps(
        {
            "model": args.model,
            "prompt": args.prompt,
            "size": args.size,
            "n": 1,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        PROXYAPI_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120, context=https_context()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Ошибка ProxyAPI ({e.code}): {e.read().decode('utf-8', errors='replace')}")

    b64 = body["data"][0]["b64_json"]
    image_bytes = base64.b64decode(b64)

    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(image_bytes)

    print(f"Сохранено: {output_path}")
    usage = body.get("usage")
    if usage:
        print(f"Использование токенов: {usage}")


if __name__ == "__main__":
    main()
