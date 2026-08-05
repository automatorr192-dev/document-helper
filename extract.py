import base64
import re

import pdfplumber


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


def image_data_url(path: str) -> str:
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"
