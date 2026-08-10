import analyzer
from analyzer import findings_so_far
from llm import Reply

TEXT = (
    "1.1. Заказчик вносит 100% предоплаты. "
    "Внесённые средства не подлежат возврату ни при каких обстоятельствах. "
    "1.2. Исполнитель вправе в одностороннем порядке изменять стоимость услуг."
)

ANSWER = (
    '{"summary": "Договор услуг с невозвратной предоплатой.", "findings": ['
    '{"quote": "не подлежат возврату", "severity": "red", "title": "Предоплата не вернётся",'
    ' "plain": "Передумаешь — деньги останутся у исполнителя.", "article": null},'
    '{"quote": "в одностороннем порядке изменять стоимость", "severity": "red",'
    ' "title": "Цену меняют без тебя", "plain": "Стоимость вырастет без твоего согласия.",'
    ' "article": null}], "actions": ["Убрать пункт о невозврате"]}'
)


def test_nothing_before_the_array_opens():
    found, pos = findings_so_far('{"summary": "пока только начало"', 0)
    assert found == []
    assert pos == 0


def test_closed_object_is_returned_before_the_answer_ends():
    """Смысл всего приёма: находку видно до того, как модель дописала ответ."""
    half = ANSWER.index('{"quote": "в одностороннем')
    found, pos = findings_so_far(ANSWER[:half], 0)
    assert len(found) == 1
    assert found[0]["quote"] == "не подлежат возврату"
    assert pos > 0


def test_reading_continues_from_where_it_stopped():
    half = ANSWER.index('{"quote": "в одностороннем')
    first, pos = findings_so_far(ANSWER[:half], 0)
    second, _ = findings_so_far(ANSWER, pos)
    assert len(first) == 1
    assert len(second) == 1
    assert second[0]["title"] == "Цену меняют без тебя"


def test_braces_inside_quotes_do_not_break_parsing():
    raw = '{"findings": [{"quote": "штраф {не более} 10%", "severity": "red", "title": "т"}]}'
    found, _ = findings_so_far(raw, 0)
    assert found[0]["quote"] == "штраф {не более} 10%"


def test_half_written_object_waits():
    cut = ANSWER.index('"severity": "red"')
    found, _ = findings_so_far(ANSWER[:cut], 0)
    assert found == []


async def fake_stream(*args, **kwargs):
    for i in range(0, len(ANSWER), 40):
        yield ANSWER[i : i + 40]
    yield Reply(text=ANSWER, model="anthropic/claude-sonnet-5", input_tokens=10, output_tokens=20)


async def test_findings_arrive_one_by_one_then_done(monkeypatch):
    monkeypatch.setattr(analyzer, "stream", fake_stream)

    events = [event async for event in analyzer.analyze_stream(TEXT)]

    kinds = [e["type"] for e in events]
    assert kinds == ["finding", "finding", "done"]
    assert events[0]["data"]["quote"] == "не подлежат возврату"
    # Привязка к тексту обязана быть: без координат подсвечивать нечего.
    assert events[0]["data"]["start"] >= 0
    assert events[-1]["data"]["verdict"]
    assert len(events[-1]["data"]["findings"]) == 2


async def test_unanchored_quote_is_dropped(monkeypatch):
    answer = (
        '{"summary": "с", "findings": ['
        '{"quote": "такого текста в договоре нет", "severity": "red", "title": "т",'
        ' "plain": "п", "article": null}], "actions": []}'
    )

    async def stream(*args, **kwargs):
        yield answer
        yield Reply(text=answer, model="m", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(analyzer, "stream", stream)
    events = [e async for e in analyzer.analyze_stream(TEXT)]
    assert [e["type"] for e in events] == ["done"]
    assert events[0]["data"]["findings"] == []
