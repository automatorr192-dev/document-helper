"""Вебхук для облака: Telegram будит спящее приложение, а разбор идёт фоном.

lifespan не запускается — бот подставляется руками, в Telegram тесты не ходят.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENROUTER_API_KEY", "test")

import pytest  # noqa: E402
from aiogram import Bot  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

SECRET = "s3cret"


def update(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Полина"},
            "text": "/start",
        },
    }


@pytest.fixture
def hooked(monkeypatch):
    fed = []

    async def feed(bot, received):
        fed.append(received.update_id)

    monkeypatch.setattr(main, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(main.tg.dp, "feed_update", feed)
    main.app.state.bot = Bot("123456:TESTTOKEN")
    yield TestClient(main.app), fed
    main.app.state.bot = None


def post(client, body, secret=SECRET):
    return client.post("/telegram", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_update_without_secret_is_refused(hooked):
    client, fed = hooked
    assert post(client, update(1), secret="guess").status_code == 403
    assert client.post("/telegram", json=update(1)).status_code == 403
    assert fed == []


def test_update_goes_to_the_bot_once(hooked):
    client, fed = hooked
    assert post(client, update(7)).json() == {"ok": True}
    assert post(client, update(7)).json() == {"ok": True}
    assert post(client, update(8)).status_code == 200
    assert fed == [7, 8]


def test_polling_mode_has_no_webhook(monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    main.app.state.bot = None
    assert post(TestClient(main.app), update(9), secret="").status_code == 403
