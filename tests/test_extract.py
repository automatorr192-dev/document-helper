from extract import tidy


def test_joins_broken_sentence():
    raw = "на оказание услуг (выездное\nобслуживание) по договору"
    assert tidy(raw) == "на оказание услуг (выездное обслуживание) по договору"


def test_keeps_numbered_clauses_apart():
    raw = "1.1. Исполнитель оказывает услуги\n1.2. Заказчик оплачивает услуги"
    assert tidy(raw) == "1.1. Исполнитель оказывает услуги\n1.2. Заказчик оплачивает услуги"


def test_keeps_paragraph_breaks():
    assert tidy("Первый абзац.\n\nВторой абзац.") == "Первый абзац.\n\nВторой абзац."


def test_collapses_extra_blank_lines():
    assert tidy("Раз.\n\n\n\nДва.") == "Раз.\n\nДва."


def test_does_not_merge_after_sentence_end():
    raw = "Договор вступает в силу с даты подписания.\nСрок действия — один год."
    assert tidy(raw) == raw
