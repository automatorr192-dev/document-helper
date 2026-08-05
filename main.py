import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analyzer
import bot as tg
import quota
import tgauth
from extract import ScannedPdfError, pdf_text

logging.basicConfig(level=logging.INFO)

HERE = os.path.dirname(__file__)
WEBAPP = os.path.join(HERE, "webapp")
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 5))
# Парсинг PDF не платный, но нагружает процессор — свой лимит, пошире.
EXTRACT_LIMIT = int(os.environ.get("EXTRACT_LIMIT", 30))


class AnalyzeIn(BaseModel):
    init_data: str = ""
    text: str = ""


def _take(user_id: int, limit: int, bucket: str) -> int:
    try:
        return quota.take(user_id, limit, bucket)
    except quota.Exhausted as e:
        raise HTTPException(429, f"На сегодня хватит — {e.limit} разборов в сутки.") from e


async def _supervise_bot():
    """Бот делит процесс с веб-приложением, поэтому его падение веб не роняет — и наоборот,
    молча остаётся незамеченным. Логируем громко и поднимаем заново."""
    while True:
        try:
            await tg.main()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("бот упал, перезапуск через 5 с")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Бот живёт фоновой задачей внутри веб-приложения: в Amvera каждое приложение
    # тарифицируется отдельно, поэтому веб и бот делят один контейнер.
    task = asyncio.create_task(_supervise_bot())
    yield
    task.cancel()
    # Дожидаемся отмены: без этого aiogram не успевает закрыть сессию и оборвать polling.
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Документ-хелпер", lifespan=lifespan)

# Статика может жить на GitHub Pages, а API — отдельно. Пускаем только свои адреса:
# API тратит деньги на каждый разбор, открывать его всему интернету незачем.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "https://automatorr192-dev.github.io").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _user(init_data: str) -> dict:
    try:
        return tgauth.check(init_data, os.environ["TELEGRAM_BOT_TOKEN"])
    except tgauth.BadInitData as e:
        detail = (
            "Мини-апп открыт слишком давно — закрой и открой заново."
            if "протухли" in str(e)
            else "Открой хелпер через бота — так я знаю, что это ты."
        )
        raise HTTPException(401, detail) from e


@app.post("/api/extract")
async def api_extract(file: UploadFile = File(...), init_data: str = Form("")):
    """Быстрый шаг: только текст из PDF. Мини-апп показывает договор, пока идёт разбор."""
    user = _user(init_data)
    _take(user.get("id", 0), EXTRACT_LIMIT, "extract")

    raw = await file.read()
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ. Пришли договор одним PDF поменьше.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(raw)
    tmp.close()
    try:
        # pdfplumber синхронный и тяжёлый: в event loop он вешает и API, и бота в этом же
        # процессе. Считаем в отдельном потоке.
        text = await asyncio.to_thread(pdf_text, tmp.name)
    except ScannedPdfError as e:
        raise HTTPException(422, "Похоже на скан — текста внутри нет. OCR пока не умею.") from e
    except Exception as e:
        raise HTTPException(422, "Не смог прочитать этот PDF.") from e
    finally:
        os.remove(tmp.name)

    return {"text": text[:MAX_TEXT_CHARS]}


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeIn):
    user = _user(body.init_data)
    text = body.text.strip()
    if len(text) < 200:
        raise HTTPException(422, "Маловато текста для разбора.")

    left = _take(user.get("id", 0), DAILY_LIMIT, "analyze")

    try:
        report, replies = await analyzer.analyze(text[:MAX_TEXT_CHARS])
    except RuntimeError as e:
        raise HTTPException(503, f"ИИ временно недоступен ({e}). Попробуй ещё раз.") from e

    cost = sum(r.cost for r in replies)
    logging.info("разбор для %s: %d находок, %.2f руб", user.get("id"), len(report.findings), cost)

    return JSONResponse(
        {
            "verdict": report.verdict,
            "summary": report.summary,
            "findings": [f.model_dump() for f in report.findings],
            "actions": report.actions,
            "left": left,
        }
    )


class NoCacheStatic(StaticFiles):
    """Мини-апп кэшируется в WebView Telegram намертво, а способа сбросить этот кэш у
    Telegram нет. no-cache не запрещает кэшировать — он требует сверить ETag перед показом,
    поэтому новая версия доезжает сразу, а трафик почти не растёт."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if os.path.isdir(WEBAPP):
    app.mount("/", NoCacheStatic(directory=WEBAPP, html=True), name="webapp")
