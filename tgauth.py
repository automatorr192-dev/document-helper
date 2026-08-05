"""Проверка initData из Telegram Mini App.

Мини-апп открыт по обычной https-ссылке, значит POST на /api/analyze может отправить
кто угодно — и жечь ключ OpenRouter. Единственное доказательство, что запрос пришёл
из Telegram, — подпись в initData, которую браузер подделать не может: она считается
на боттокене, а он есть только у нас и у Telegram.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class BadInitData(Exception):
    pass


def check(init_data: str, bot_token: str, max_age: int = 24 * 3600) -> dict:
    """Возвращает данные пользователя или падает. Никогда не доверять полю user без этого."""
    if not init_data:
        raise BadInitData("пусто")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    given = fields.pop("hash", None)
    if not given:
        raise BadInitData("нет подписи")

    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    # Сравнение с постоянным временем: обычное == позволяет подобрать подпись по таймингу.
    if not hmac.compare_digest(expected, given):
        raise BadInitData("подпись не сходится")

    auth_date = int(fields.get("auth_date", 0))
    if not auth_date or time.time() - auth_date > max_age:
        raise BadInitData("данные протухли")

    return json.loads(fields.get("user", "{}"))
