"""Суточные лимиты на пользователя.

Счётчик в памяти процесса обнуляется при каждом передеплое, а разбор стоит живых денег —
поэтому лимит переживает рестарт и лежит в персистентном /data.
"""

import json
import os
import time

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")
PATH = os.path.join(DATA_DIR, "quota.json")
WINDOW = 24 * 3600


class Exhausted(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"лимит {limit} в сутки исчерпан")


def _load() -> dict[str, list[float]]:
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, PATH)


def take(user_id: int, limit: int, bucket: str = "analyze") -> int:
    """Списывает одну попытку, возвращает остаток. Кидает Exhausted, когда лимит выбран."""
    now = time.time()
    data = {k: fresh for k, v in _load().items() if (fresh := [t for t in v if now - t < WINDOW])}
    key = f"{bucket}:{user_id}"
    used = data.get(key, [])

    if len(used) >= limit:
        _save(data)
        raise Exhausted(limit)

    used.append(now)
    data[key] = used
    _save(data)
    return limit - len(used)
