import io
import zipfile

import pytest

from extract import UnsupportedFormat, docx_text, kind

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path, body: str) -> str:
    xml = f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return str(path)


def para(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_reads_paragraphs_and_tables(tmp_path):
    body = (
        para("1.1. Исполнитель оказывает услуги по настройке рекламы в срок 30 дней.")
        + para("1.2. Предоплата 100% не возвращается ни при каких обстоятельствах.")
        + "<w:tbl><w:tr><w:tc>"
        + para("Исполнитель")
        + "</w:tc><w:tc>"
        + para("Заказчик")
        + "</w:tc></w:tr></w:tbl>"
    )
    text = docx_text(make_docx(tmp_path / "d.docx", body))
    assert "1.2. Предоплата 100% не возвращается" in text
    assert "Исполнитель | Заказчик" in text
    assert text.index("1.1.") < text.index("1.2.")


def test_split_runs_are_glued(tmp_path):
    tail = "% в день без ограничения суммы и сроков, по требованию."
    runs = f"<w:p><w:r><w:t>Штраф 1</w:t></w:r><w:r><w:t>{tail}</w:t></w:r></w:p>"
    text = docx_text(make_docx(tmp_path / "d.docx", runs + para("Подписи сторон и реквизиты.")))
    assert "Штраф 1% в день" in text


def test_not_a_word_file(tmp_path):
    fake = tmp_path / "fake.docx"
    fake.write_bytes(b"%PDF-1.4 not a zip")
    with pytest.raises(UnsupportedFormat):
        docx_text(str(fake))


def test_empty_word_file(tmp_path):
    with pytest.raises(UnsupportedFormat):
        docx_text(make_docx(tmp_path / "e.docx", para("Договор")))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Договор.DOCX", "docx"),
        ("old.doc", "doc"),
        ("scan.pdf", "pdf"),
        (None, "pdf"),
        ("a.txt", "txt"),
    ],
)
def test_kind_by_name(name, expected):
    assert kind(name) == expected


def test_api_extracts_docx(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "_user", lambda init_data: {"id": 1})
    monkeypatch.setattr(main, "_take", lambda *args: None)
    path = make_docx(tmp_path / "d.docx", para("Договор оказания услуг. " * 6))
    with open(path, "rb") as file:
        response = TestClient(main.app).post(
            "/api/extract", files={"file": ("Договор.docx", io.BytesIO(file.read()))}
        )
    assert response.status_code == 200
    assert response.json()["text"].startswith("Договор оказания услуг.")
    assert response.json()["scanned"] is False


def test_paragraphs_inside_content_controls(tmp_path):
    inner = para("3.1. Арендодатель вправе в одностороннем порядке повышать ставку аренды.")
    body = (
        para("Договор аренды помещения.") + f"<w:sdt><w:sdtContent>{inner}</w:sdtContent></w:sdt>"
    )
    assert "в одностороннем порядке" in docx_text(make_docx(tmp_path / "s.docx", body))
