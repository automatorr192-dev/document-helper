import pypdfium2 as pdfium
import pytest

import ocr
from extract import ScannedPdfError, page_images
from llm import Reply


@pytest.fixture
def blank_pdf(tmp_path):
    """Три пустые страницы. Датасет договоров в git не лежит, поэтому PDF собираем сами."""
    path = tmp_path / "scan.pdf"
    pdf = pdfium.PdfDocument.new()
    for _ in range(3):
        pdf.new_page(595, 842)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def test_pages_become_data_urls(blank_pdf):
    urls = page_images(blank_pdf)
    assert len(urls) == 3
    assert all(u.startswith("data:image/jpeg;base64,") for u in urls)


def test_page_limit_is_respected(blank_pdf):
    assert len(page_images(blank_pdf, max_pages=2)) == 2


def reply(text: str) -> Reply:
    return Reply(text=text, model="anthropic/claude-haiku-4.5", input_tokens=10, output_tokens=5)


async def test_pages_are_joined_in_order(blank_pdf, monkeypatch):
    async def fake_page(index, url):
        return reply(f"Пункт {index}. Текст страницы номер {index} для проверки склейки.")

    monkeypatch.setattr(ocr, "_page", fake_page)
    text, replies = await ocr.text_from_scan(blank_pdf)
    assert text.index("страницы номер 1") < text.index("страницы номер 3")
    assert len(replies) == 3


async def test_empty_pages_are_not_passed_on(blank_pdf, monkeypatch):
    async def blank(index, url):
        return reply("")

    monkeypatch.setattr(ocr, "_page", blank)
    with pytest.raises(ScannedPdfError):
        await ocr.text_from_scan(blank_pdf)


def test_actual_cost_beats_our_estimate():
    guessed = Reply("текст", "anthropic/claude-sonnet-5", 1_000_000, 0)
    actual = Reply("текст", "anthropic/claude-sonnet-5", 1_000_000, 0, usd=0.5)
    assert guessed.cost != actual.cost
    assert actual.cost == pytest.approx(0.5 * 80, rel=0.01)
