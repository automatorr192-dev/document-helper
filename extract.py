import base64
import io
import os
import re

import pdfplumber
import pypdfium2 as pdfium

# Сколько страниц скана вообще смотрим. Каждая — отдельный платный запрос к модели,
# поэтому потолок нужен: без него один PDF на сто страниц выест дневной бюджет.
OCR_MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", 5))
# 150 DPI — компромисс: мелкий шрифт договора ещё читается, а страница весит ~300 КБ.
OCR_DPI = int(os.environ.get("OCR_DPI", 150))


class ScannedPdfError(Exception):
    pass


# Строка продолжает предыдущую, если та не закончилась знаком конца мысли.
_CLOSERS = (".", ":", ";", "!", "?", "»", '"', ")")
_NEW_BLOCK = re.compile(r"^\s*(\d+(\.\d+)*[.)]?\s|[-–—•])")


def tidy(text: str) -> str:
    """Склеивает строки одного абзаца.

    pdfplumber ставит перенос в конце каждой визуальной строки, поэтому сырой текст
    рвётся посреди предложений. Читать такой договор на телефоне невозможно, а
    подсветку строим по индексам символов — значит причёсывать надо один раз, здесь,
    до того как текст уйдёт и в модель, и в мини-апп.
    """
    blocks: list[str] = []
    buf = ""
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if buf:
                blocks.append(buf)
                buf = ""
            blocks.append("")
            continue
        if not buf:
            buf = line
        elif buf.endswith(_CLOSERS) or _NEW_BLOCK.match(line):
            blocks.append(buf)
            buf = line
        else:
            buf += " " + line
    if buf:
        blocks.append(buf)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(blocks)).strip()


def pdf_text(path: str) -> str:
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    text = tidy("\n".join(parts))
    if len(text) < 80:
        raise ScannedPdfError
    return text


def page_images(path: str, max_pages: int = OCR_MAX_PAGES, dpi: int = OCR_DPI) -> list[str]:
    """Страницы PDF как data-URL картинок — вход для распознавания скана.

    В скане нет ни одной буквы, внутри лежит фотография бумаги. Прочитать её может
    только тот, кто умеет смотреть, поэтому страницы превращаем в изображения и отдаём
    vision-модели.
    """
    pdf = pdfium.PdfDocument(path)
    try:
        urls = []
        for i in range(min(len(pdf), max_pages)):
            image = pdf[i].render(scale=dpi / 72).to_pil().convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=70, optimize=True)
            urls.append("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode())
        return urls
    finally:
        pdf.close()


def image_data_url(path: str) -> str:
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"
