"""FSM-хранилище в файле.

Дефолтный MemoryStorage живёт в словаре процесса: любой передеплой роняет всех, кто был
в середине диалога. Redis на Amvera — это отдельное платное приложение, а состояний тут
десятки, поэтому пишем в persistent /data. Процесс один и asyncio однопоточный, так что
read-modify-write между await'ами атомарен.
"""

import json
import os

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")


class FileStorage(BaseStorage):
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(DATA_DIR, "fsm.json")
        self._cache = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _flush(self) -> None:
        # Пустые слоты выкидываем: после state.clear() их иначе накапливается по одному
        # на каждого, кто когда-либо жал /start.
        self._cache = {k: v for k, v in self._cache.items() if v.get("state") or v.get("data")}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _slot(self, key: StorageKey) -> dict:
        return self._cache.setdefault(f"{key.bot_id}:{key.chat_id}:{key.user_id}", {})

    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        self._slot(key)["state"] = state.state if isinstance(state, State) else state
        self._flush()

    async def get_state(self, key: StorageKey) -> str | None:
        return self._slot(key).get("state")

    async def set_data(self, key: StorageKey, data: dict) -> None:
        self._slot(key)["data"] = data
        self._flush()

    async def get_data(self, key: StorageKey) -> dict:
        return dict(self._slot(key).get("data") or {})

    async def close(self) -> None:
        self._flush()
