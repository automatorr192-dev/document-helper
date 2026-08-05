import pytest
from aiogram.fsm.storage.base import StorageKey

from storage import FileStorage

KEY = StorageKey(bot_id=1, chat_id=2, user_id=2)


@pytest.mark.asyncio
async def test_state_and_data_survive_restart(tmp_path):
    path = str(tmp_path / "fsm.json")
    first = FileStorage(path)
    await first.set_state(KEY, "Flow:gen_brief")
    await first.set_data(KEY, {"brief": "услуги"})

    second = FileStorage(path)
    assert await second.get_state(KEY) == "Flow:gen_brief"
    assert await second.get_data(KEY) == {"brief": "услуги"}


@pytest.mark.asyncio
async def test_cleared_slots_do_not_pile_up(tmp_path):
    path = str(tmp_path / "fsm.json")
    storage = FileStorage(path)
    await storage.set_state(KEY, "Flow:gen_brief")
    await storage.set_state(KEY, None)
    await storage.set_data(KEY, {})
    assert FileStorage(path)._cache == {}


@pytest.mark.asyncio
async def test_users_do_not_share_state(tmp_path):
    storage = FileStorage(str(tmp_path / "fsm.json"))
    other = StorageKey(bot_id=1, chat_id=9, user_id=9)
    await storage.set_data(KEY, {"brief": "мой"})
    assert await storage.get_data(other) == {}
