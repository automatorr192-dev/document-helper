"""Метрика проекта: нашёл ли хелпер размеченные ловушки. Балл от 0 до 1 за кейс.

Самая тупая метрика, которую можно посчитать без модели: для каждой размеченной
ловушки проверяем, попал ли её опорный фрагмент в цитату, по которой поднята тревога
(красный или жёлтый).
LLM-судья дороже, медленнее и сам ошибается — он понадобится, когда захотим мерить
качество формулировок «на человеческом», а не сам факт находки.

Формат разметки в cases.jsonl:
  must_find     — фрагменты пунктов, которые обязаны попасть в цитаты
  must_not_find — нормальные пункты: поднимать по ним тревогу (красный или жёлтый) нельзя.
                  Показать их зелёным, «это в порядке», — правильное поведение продукта.
"""


def _flat(s: str) -> str:
    return " ".join(s.split()).lower()


def score(expected: dict, output: dict) -> float:
    # Цитата без привязки к тексту бесполезна — подсвечивать нечего.
    if not output.get("anchored", True):
        return 0.0

    traps = [r for r in output.get("risks", []) if r["severity"] != "green"]

    alarms = [_flat(r["quote"]) for r in traps]
    for frag in expected.get("must_not_find", []):
        if any(_flat(frag) in q for q in alarms):
            return 0.0

    wanted = expected.get("must_find", [])
    if not wanted:
        # Чистый договор: правильный ответ — не выдумывать ловушек.
        return 1.0 if not traps else 0.0

    # Ловушка найдена, только если по ней поднята тревога: опасный пункт, показанный
    # зелёным, — промах, а не находка.
    hits = sum(1 for frag in wanted if any(_flat(frag) in q for q in alarms))
    return hits / len(wanted)
