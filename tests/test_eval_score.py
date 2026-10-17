"""Метрика эвалов: что считается находкой, а что ложной тревогой."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from score import score  # noqa: E402

EXPECTED = {"must_find": ["не подлежат возврату"], "must_not_find": ["в двух экземплярах"]}


def risk(severity, quote):
    return {"severity": severity, "title": "т", "quote": quote}


def test_trap_raised_and_normal_clause_shown_green_is_full_score():
    output = {
        "risks": [
            risk("red", "Деньги не подлежат возврату"),
            risk("green", "Договор составлен в двух экземплярах"),
        ]
    }
    assert score(EXPECTED, output) == 1.0


def test_alarm_on_a_normal_clause_is_zero():
    output = {
        "risks": [
            risk("red", "Деньги не подлежат возврату"),
            risk("yellow", "Договор составлен в двух экземплярах"),
        ]
    }
    assert score(EXPECTED, output) == 0.0


def test_trap_shown_green_is_a_miss():
    assert score(EXPECTED, {"risks": [risk("green", "Деньги не подлежат возврату")]}) == 0.0


def test_unanchored_quote_is_zero():
    output = {"anchored": False, "risks": [risk("red", "Деньги не подлежат возврату")]}
    assert score(EXPECTED, output) == 0.0


def test_clean_contract_needs_no_alarms():
    clean = {"must_find": [], "must_not_find": []}
    assert score(clean, {"risks": [risk("green", "Цена — 54 000 рублей")]}) == 1.0
    assert score(clean, {"risks": [risk("yellow", "Претензий не имеет")]}) == 0.0
