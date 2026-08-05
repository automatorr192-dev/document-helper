import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from tgauth import BadInitData, check

TOKEN = "123456:TESTTOKEN"


def make_init_data(user_id: int = 7, auth_date: int | None = None, token: str = TOKEN) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "Гриша"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_returns_user():
    assert check(make_init_data(user_id=42), TOKEN)["id"] == 42


def test_rejects_foreign_token():
    # Подпись сделана чужим ботом — значит запрос не наш.
    with pytest.raises(BadInitData):
        check(make_init_data(token="999:OTHER"), TOKEN)


def test_rejects_tampered_user():
    # Классическая атака: подменить id пользователя, оставив чужую подпись.
    data = make_init_data(user_id=7)
    with pytest.raises(BadInitData):
        check(data.replace("%22id%22%3A+7", "%22id%22%3A+8"), TOKEN)


def test_rejects_missing_hash():
    with pytest.raises(BadInitData):
        check("auth_date=1&user=%7B%7D", TOKEN)


def test_rejects_empty():
    with pytest.raises(BadInitData):
        check("", TOKEN)


def test_rejects_stale_data():
    old = int(time.time()) - 48 * 3600
    with pytest.raises(BadInitData):
        check(make_init_data(auth_date=old), TOKEN)
