"""Распознавание сканов.

В скане нет текста — внутри лежит фотография бумаги, и pdfplumber достаёт из него
пустоту. Классический OCR (tesseract) стоил бы +300 МБ образа и языковых пакетов,
а vision-модель у нас уже подключена тем же клиентом, что и всё остальное.

Ключевое решение: модель не разбирает картинку сразу в отчёт, а **переписывает текст**.
Разбор картинки выдал бы находки, которые не к чему привязать — подсветка в мини-аппе
живёт на координатах цитат внутри текста. Переписали → дальше идёт обычный конвейер.
"""

import asyncio
import logging

from extract import ScannedPdfError, page_images, tidy
from llm import MODELS_LIGHT, Reply, chat
from prompts import OCR_SYSTEM


async def _page(index: int, data_url: str) -> Reply:
    return await chat(
        OCR_SYSTEM,
        [
            {"type": "text", "text": f"Страница {index}. Перепиши весь текст с этой страницы."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
        MODELS_LIGHT,
        max_tokens=4000,
    )


async def text_from_scan(path: str) -> tuple[str, list[Reply]]:
    """Текст скана и все ответы модели. Кидает ScannedPdfError, если читать нечего."""
    images = await asyncio.to_thread(page_images, path)
    if not images:
        raise ScannedPdfError

    # Страницы независимы, поэтому идут разом: пять последовательных запросов — это
    # полминуты ожидания на ровном месте.
    replies = await asyncio.gather(*(_page(i, url) for i, url in enumerate(images, 1)))

    text = tidy("\n\n".join(r.text for r in replies if r.text))
    logging.info(
        "распознано страниц: %d, %.2f ₽",
        len(images),
        sum(r.cost for r in replies),
    )
    if len(text) < 80:
        raise ScannedPdfError
    return text, list(replies)
