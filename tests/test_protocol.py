import io
import zipfile

from fastapi.testclient import TestClient

import protocol
import share
from extract import docx_text

REPORT = {
    "verdict": "2 ловушки, 1 критичная",
    "summary": "Договор услуг, риски на стороне заказчика.",
    "findings": [
        {
            "severity": "red",
            "title": "Предоплата не возвращается",
            "quote": "5.2. Предоплата в размере 100% не возвращается ни при каких условиях.",
            "plain": "Если откажешься, деньги пропадут целиком.",
            "basis": "Заказчик вправе отказаться, оплатив фактически понесённые расходы.",
            "article": "ст. 782 ГК РФ",
            "fix": "5.2. При отказе Заказчика Исполнитель возвращает предоплату за вычетом "
            "фактически понесённых расходов.",
        },
        {
            "severity": "yellow",
            "title": "Подсудность у исполнителя",
            "quote": "Споры рассматриваются по месту нахождения Исполнителя.",
            "plain": "Судиться придётся в чужом городе.",
            "article": None,
            "fix": None,
        },
        {
            "severity": "green",
            "title": "Сроки понятны",
            "quote": "3.1. Срок оказания услуг — 30 дней.",
            "plain": "Всё в порядке.",
        },
    ],
    "actions": [],
}


def test_protocol_is_a_word_file_with_disputed_clauses_only(tmp_path):
    data = protocol.docx(REPORT)
    path = tmp_path / "p.docx"
    path.write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(
            archive.namelist()
        )

    text = docx_text(str(path))
    assert "ПРОТОКОЛ РАЗНОГЛАСИЙ" in text
    assert "п. 5.2" in text
    assert "возвращает предоплату за вычетом" in text
    assert "оплатив фактически понесённые расходы. (ст. 782 ГК РФ)" in text
    assert "Если откажешься" not in text
    assert "Судиться придётся в чужом городе." in text
    assert "Исключить пункт." in text
    assert "Срок оказания услуг" not in text


def test_protocol_escapes_text_from_the_contract(tmp_path):
    report = {"findings": [{"severity": "red", "quote": "<script>&</script>", "plain": "a"}]}
    path = tmp_path / "p.docx"
    path.write_bytes(protocol.docx(report))
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "&lt;script&gt;&amp;&lt;/script&gt;" in xml


def test_clause_number_is_taken_from_the_quote():
    assert protocol.clause_number("7.4.1 Арендатор обязан") == "п. 7.4.1"
    assert protocol.clause_number("Арендатор обязан") == "—"


def test_protocol_link_from_a_shared_report(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(share, "REPORTS", str(tmp_path))
    token = share.save(REPORT)
    response = TestClient(main.app).get(f"/r/{token}.docx")
    assert response.status_code == 200
    assert "wordprocessingml" in response.headers["content-type"]

    calm = share.save({**REPORT, "findings": [REPORT["findings"][2]]})
    assert TestClient(main.app).get(f"/r/{calm}.docx").status_code == 404
