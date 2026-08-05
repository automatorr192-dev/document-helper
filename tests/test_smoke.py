"""Тесты на то, что не ходит в сеть: привязка цитат, парсинг, вердикт, стоимость.

Всё, что зависит от ответа модели, живёт в evals/.
"""

import pytest
from pydantic import ValidationError

from analyzer import _parse, locate
from llm import cost_rub
from schema import Report

TEXT = (
    "1.1. Исполнитель вправе в одностороннем порядке\n"
    "изменять   стоимость услуг, уведомив Заказчика за 3 дня.\n"
    "1.2. Договор автоматически продлевается на год."
)


def test_locate_exact():
    quote = "Договор автоматически продлевается на год."
    start, end = locate(TEXT, quote)
    assert TEXT[start:end] == quote


def test_locate_ignores_whitespace_and_case():
    # Модель переносит цитату в одну строку и схлопывает пробелы — это норма.
    quote = "изменять стоимость услуг, Уведомив Заказчика за 3 дня"
    start, end = locate(TEXT, quote)
    assert TEXT[start:end] == "изменять   стоимость услуг, уведомив Заказчика за 3 дня"


def test_locate_rejects_invented_quote():
    assert locate(TEXT, "Исполнитель обязуется вернуть предоплату") is None


def test_parse_survives_prose_around_json():
    raw = 'Вот разбор:\n```json\n{"summary": "s", "findings": [], "actions": []}\n```'
    assert _parse(raw).summary == "s"


def test_parse_rejects_wrong_severity():
    raw = '{"summary": "s", "findings": [{"quote": "q", "severity": "оранжевый", '
    raw += '"title": "t", "plain": "p"}], "actions": []}'
    with pytest.raises(ValidationError):
        _parse(raw)


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], "Ловушек не нашёл"),
        (["green"], "Ловушек не нашёл"),
        (["red"], "1 ловушка, 1 критичная"),
        (["red", "red", "yellow"], "3 ловушки, 2 критичные"),
        (["yellow"] * 5, "5 ловушек"),
    ],
)
def test_verdict(severities, expected):
    report = Report(
        summary="s",
        findings=[{"quote": "q", "severity": s, "title": "t", "plain": "p"} for s in severities],
    )
    assert report.verdict == expected


def test_cost_counts_rubles():
    # Sonnet: $3 за миллион входных, $15 за миллион выходных, курс 80 ₽.
    assert cost_rub("anthropic/claude-sonnet-5", 1_000_000, 0) == 240.0
    assert cost_rub("anthropic/claude-sonnet-5", 0, 1_000_000) == 1200.0
    assert cost_rub("mistral/whatever", 1_000_000, 1_000_000) == 0.0
